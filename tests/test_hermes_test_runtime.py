"""Active Hermes test-mode wiring must stay pinned to the isolated clone."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import hermes_data_agent_gate as gate
from scripts import hermes_data_agent_test_gate as test_gate
from scripts import hermes_data_agent_worker as worker


def _test_config(tmp_path: Path) -> tuple[Path, str]:
    db_path = "/app/data/test_runs/session-20260916/data.db"
    config = tmp_path / "test-gate.json"
    config.write_text(
        json.dumps(
            {
                "transport": "local_docker",
                "container": "chip-recommend",
                "mode": "test",
                "db_path": db_path,
                "source_limit": 4,
                "job_limit": 3,
                "worker_id": "hermes-test-session",
                "skills": ["url-discovery", "chip-basic"],
                "timeout_seconds": 60,
            }
        ),
        encoding="utf-8",
    )
    return config, db_path


def test_test_gate_resumes_only_the_isolated_clone(tmp_path, monkeypatch, capsys):
    config, db_path = _test_config(tmp_path)
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"run_id": 8, "awaiting_agent_jobs": 2, "failed_jobs": 0}
            ),
            stderr="",
        )

    monkeypatch.setattr(gate.subprocess, "run", fake_run)

    assert gate.main(["--config", str(config)]) == 0
    command = observed["command"]
    assert command[:4] == ["/usr/bin/docker", "exec", "-e", f"DATA_DB_PATH={db_path}"]
    assert command[4:8] == ["chip-recommend", "python", "scripts/run_data_agent.py", "run"]
    assert command[-3:] == ["4", "--resume-stale-targets", "--resume-only"]
    assert "--daily-start-guard" not in command
    assert observed["kwargs"]["cwd"] == config.parent
    decision = json.loads(capsys.readouterr().out)
    assert decision["wakeAgent"] is True
    assert decision["context"]["data_agent"]["run_id"] == 8


def test_test_worker_forwards_finish_payload_to_same_clone(tmp_path, monkeypatch, capsys):
    config, db_path = _test_config(tmp_path)
    observed = {}
    payload = json.dumps({"job_id": 7, "status": "succeeded", "output": {"facts": []}})

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"status":"succeeded"}', stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    assert worker._run(config, "data-agent-finish", payload) == 0
    assert observed["command"] == [
        "/usr/bin/docker",
        "exec",
        "-i",
        "-e",
        f"DATA_DB_PATH={db_path}",
        "-e",
        "PYTHONPATH=/app",
        "chip-recommend",
        "python",
        "scripts/hermes_data_agent_finish_server.py",
    ]
    assert observed["kwargs"]["input"] == payload
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"


def test_test_gate_selects_latest_valid_session_and_builds_bounded_config(tmp_path):
    root = tmp_path / "data"
    older = root / "test_runs" / "20260915-120000"
    latest = root / "test_runs" / "20260916-120000"
    ignored = root / "test_runs" / "20260917-120000"
    for session in (older, latest, ignored):
        session.mkdir(parents=True)
        (session / "test_mode.json").write_text('{"mode":"test"}', encoding="utf-8")
    (older / "data.db").write_bytes(b"")
    (latest / "data.db").write_bytes(b"")

    assert test_gate.latest_session(root) == latest
    config_path = test_gate.session_config(latest)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["mode"] == "test"
    assert config["container"] == "chip-recommend"
    assert config["db_path"] == "/app/data/test_runs/20260916-120000/data.db"
    assert 1 <= config["source_limit"] <= 50
