"""The primary Hermes gate and worker must stay on the local application DB."""

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import hermes_data_agent_gate as gate
from scripts import hermes_data_agent_worker as worker


ROOT = Path(__file__).resolve().parents[1]


def test_primary_daily_gate_is_paused_without_touching_formal_db(monkeypatch, capsys):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"run_id": 1, "awaiting_agent_jobs": 64, "failed_jobs": 18}),
            stderr="",
        )

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.main(["--config", str(ROOT / "config" / "hermes_primary_gate.json")]) == 0
    assert observed == {}
    decision = json.loads(capsys.readouterr().out)
    assert decision["wakeAgent"] is False
    assert decision["status"] == "paused"


def test_primary_worker_does_not_claim_while_paused(monkeypatch, capsys):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return SimpleNamespace(returncode=0, stdout='{"jobs": []}', stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    assert worker._run(ROOT / "config" / "hermes_primary_gate.json", "data-agent-claim") == 0
    assert observed == {}
    assert json.loads(capsys.readouterr().out)["status"] == "paused"


def test_primary_publication_bridge_is_paused(monkeypatch, capsys):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs["input"]
        return SimpleNamespace(returncode=0, stdout='{"status":"accepted"}', stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    payload = '{"candidate_ids":[7]}'
    assert worker._run(
        ROOT / "config" / "hermes_primary_gate.json", "candidate-publish", payload
    ) == 0
    assert observed == {}
    assert json.loads(capsys.readouterr().out)["status"] == "paused"


def test_primary_configs_reject_another_local_container(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"transport": "local_docker", "container": "unrelated"}), encoding="utf-8")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.main(["--config", str(bad)]) == 1
    assert called is False
    try:
        worker._run(bad, "data-agent-claim")
    except ValueError as exc:
        assert "只允许 chip-recommend" in str(exc)
    else:
        raise AssertionError("worker accepted an unrelated container")
