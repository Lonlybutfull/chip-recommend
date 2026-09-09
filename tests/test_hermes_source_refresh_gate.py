"""Tests for the Hermes cron source-refresh wake gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parent.parent / "scripts" / "hermes_source_refresh_gate.py"
SPEC = importlib.util.spec_from_file_location("hermes_source_refresh_gate", MODULE_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def config(**overrides):
    base = {
        "failure_notify_threshold": 3,
        "notify_on_new": False,
    }
    base.update(overrides)
    return base


def summary(*results, business_tables_modified=False):
    counts = {"selected": len(results)}
    for result in results:
        outcome = result["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    return {
        "run_id": 7,
        "status": "success",
        "started_at": "2026-09-07T00:00:00Z",
        "finished_at": "2026-09-07T00:00:02Z",
        "duration_ms": 2000,
        "counts": counts,
        "results": list(results),
        "business_tables_modified": business_tables_modified,
    }


def test_unchanged_and_initial_baseline_stay_silent():
    decision = gate.decide(
        summary(
            {"link_id": 1, "outcome": "unchanged"},
            {"link_id": 2, "outcome": "new"},
        ),
        config(),
    )

    assert decision == {"wakeAgent": False}


def test_changed_source_wakes_agent_with_compact_context():
    decision = gate.decide(
        summary(
            {
                "link_id": 20,
                "outcome": "changed",
                "requested_url": "https://vendor.example/spec",
                "content_hash": "new-hash",
                "previous_content_hash": "old-hash",
                "snapshot_path": "/snapshots/new.html",
                "normalized_snapshot_path": "/snapshots/normalized/new.txt",
                "diff_path": "/snapshots/diffs/old..new.diff",
                "diff_summary": "新增 1 行，删除 1 行；- VRAM 64 GB；+ VRAM 96 GB",
                "diff_added_lines": 1,
                "diff_removed_lines": 1,
                "candidate_fields": [
                    {
                        "field": "vram_gb",
                        "label": "显存容量",
                        "change_type": "added",
                        "evidence": "VRAM 96 GB",
                    }
                ],
            }
        ),
        config(),
    )

    assert decision["wakeAgent"] is True
    context = decision["context"]["source_refresh"]
    assert context["run_id"] == 7
    assert context["trigger"] == "hermes_ticker"
    assert context["duration_ms"] == 2000
    assert context["changed_sources"][0]["link_id"] == 20
    assert context["changed_sources"][0]["diff_path"].endswith(".diff")
    assert context["changed_sources"][0]["candidate_fields"][0]["field"] == "vram_gb"
    assert context["business_tables_modified"] is False


def test_failure_only_wakes_after_threshold():
    below = gate.decide(
        summary({"link_id": 1, "outcome": "retry_wait", "failure_count": 2}),
        config(),
    )
    reached = gate.decide(
        summary(
            {
                "link_id": 1,
                "outcome": "retry_wait",
                "failure_count": 3,
                "error_code": "timeout",
            }
        ),
        config(),
    )

    assert below == {"wakeAgent": False}
    assert reached["wakeAgent"] is True
    assert reached["context"]["source_refresh"]["failing_sources"][0][
        "error_code"
    ] == "timeout"


def test_new_source_can_be_configured_to_notify():
    decision = gate.decide(
        summary({"link_id": 2, "outcome": "new"}),
        config(notify_on_new=True),
    )

    assert decision["wakeAgent"] is True
    assert decision["context"]["source_refresh"]["new_sources"][0]["link_id"] == 2


def test_missing_business_table_isolation_signal_fails_closed():
    with pytest.raises(RuntimeError, match="业务表保持不变"):
        gate.decide(
            {
                "run_id": 1,
                "results": [],
            },
            config(),
        )


def test_load_config_resolves_project_paths(tmp_path: Path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "run_source_refresh.py").write_text(
        "print('{}')\n", encoding="utf-8"
    )
    config_path = tmp_path / "gate.json"
    config_path.write_text(
        json.dumps(
            {
                "project_root": str(project),
                "link_ids": [20, 20, 21],
                "db_path": "data/data.db",
                "snapshot_dir": "data/source_snapshots",
            }
        ),
        encoding="utf-8",
    )

    loaded = gate.load_config(config_path)

    assert loaded["link_ids"] == [20, 21]
    assert loaded["db_path"] == (project / "data" / "data.db").resolve()
    assert loaded["snapshot_dir"] == (
        project / "data" / "source_snapshots"
    ).resolve()


def test_load_config_builds_pinned_restricted_ssh_transport(tmp_path: Path):
    identity = tmp_path / "heartbeat-key"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test", encoding="utf-8")
    known_hosts.write_text("test", encoding="utf-8")
    config_path = tmp_path / "gate.json"
    config_path.write_text(
        json.dumps(
            {
                "transport": "ssh",
                "failure_notify_threshold": 3,
                "ssh": {
                    "host": "81.70.231.92",
                    "port": 22,
                    "user": "lxc",
                    "identity_file": identity.name,
                    "known_hosts_file": known_hosts.name,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = gate.load_config(config_path)
    command = gate.build_command(loaded)

    assert loaded["transport"] == "ssh"
    assert str(identity.resolve()) in command
    assert f"UserKnownHostsFile={known_hosts.resolve()}" in command
    assert command[-2:] == ["lxc@81.70.231.92", "source-refresh"]


@pytest.mark.parametrize(
    "override",
    [
        {"project_root": ""},
        {"notify_on_new": "false"},
        {"failure_notify_threshold": "many"},
    ],
)
def test_load_config_rejects_ambiguous_values(tmp_path: Path, override: dict):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "run_source_refresh.py").write_text(
        "print('{}')\n", encoding="utf-8"
    )
    payload = {"project_root": str(project), "link_ids": [20]}
    payload.update(override)
    config_path = tmp_path / "invalid.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(gate.GateConfigurationError):
        gate.load_config(config_path)


def test_gate_runs_refresh_and_emits_single_hermes_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    refresh_summary = summary(
        {
            "link_id": 20,
            "outcome": "changed",
            "requested_url": "https://vendor.example/spec",
            "content_hash": "new",
            "previous_content_hash": "old",
        }
    )
    (project / "scripts" / "run_source_refresh.py").write_text(
        "import json\n"
        f"print(json.dumps({refresh_summary!r}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "gate.json"
    config_path.write_text(
        json.dumps(
            {
                "project_root": str(project),
                "python_executable": sys.executable,
                "link_ids": [20],
            }
        ),
        encoding="utf-8",
    )

    exit_code = gate.run(config_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    decision = json.loads(captured.out)
    assert decision["wakeAgent"] is True
    assert decision["context"]["source_refresh"]["changed_sources"][0][
        "link_id"
    ] == 20
