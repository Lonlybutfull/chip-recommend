"""Manual dispatcher starts only the requested container and wakes Hermes afterward."""

import json
from pathlib import Path

from scripts import hermes_manual_run_dispatcher as dispatcher


def test_dispatch_test_request_uses_isolated_cli_and_test_heartbeat(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"id": "abc", "mode": "test", "limit": 3, "status": "queued"}))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        class Result:
            returncode = 0
            stdout = '{"run_id": 7, "status": "awaiting_agent", "session_id": "test123"}'
            stderr = ""
        return Result()

    def fake_popen(command, **kwargs):
        calls.append(command)
        return object()

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    monkeypatch.setattr(dispatcher.subprocess, "Popen", fake_popen)
    final = dispatcher.dispatch(request, container="chip-recommend", formal_job_id="formal-job",
                                test_job_id="test-job", hermes_python="hermes-python")
    assert final["status"] == "completed"
    assert calls[0] == ["/usr/bin/docker", "exec", "chip-recommend", "python",
                        "scripts/run_data_agent.py", "test", "--limit", "3"]
    assert calls[1][-1] == "test-job"


def test_dispatch_refuses_unexpected_container(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"id": "abc", "mode": "formal", "limit": 5, "status": "queued"}))
    try:
        dispatcher.dispatch(request, container="other-container", formal_job_id="formal",
                            test_job_id="test", hermes_python="hermes-python")
    except ValueError:
        pass
    else:
        assert False, "unexpected container must be rejected"


def test_dispatch_does_not_run_queued_formal_request(tmp_path, monkeypatch):
    request = tmp_path / "formal.json"
    request.write_text(json.dumps({"id": "old", "mode": "formal", "limit": 5, "status": "queued"}))
    monkeypatch.setattr(dispatcher.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("formal subprocess must not run")
    ))
    result = dispatcher.dispatch(request, container="chip-recommend", formal_job_id="formal",
                                 test_job_id="test", hermes_python="hermes-python")
    assert result["status"] == "failed"
    assert "暂停" in result["error"]


def test_paused_test_agent_is_reported_as_partial(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"id": "test", "mode": "test", "limit": 1, "status": "queued"}))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        class Result:
            returncode = 0
            stdout = '{"run_id": 5, "status": "awaiting_agent"}'
            stderr = ""
        return Result()

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    final = dispatcher.dispatch(request, container="chip-recommend", formal_job_id="formal",
                                test_job_id="paused", hermes_python="hermes-python",
                                test_job_active=False)
    assert final["status"] == "partial"
    assert "语义提取尚未执行" in final["message"]
    assert len(calls) == 1


def test_open_web_request_runs_once_without_waking_hermes(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "id": "web", "mode": "test", "pipeline": "open_web", "limit": 4,
        "skill": "chip-specs", "chips": ["昇腾 910B"],
        "queries": ["官方规格"], "target_fields": ["vram_gb"],
        "status": "queued",
    }), encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        class Result:
            returncode = 0
            stdout = '{"session_id":"web123","status":"success"}'
            stderr = ""
        return Result()

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    monkeypatch.setattr(
        dispatcher.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("open-web test must not wake Hermes")
        ),
    )
    final = dispatcher.dispatch(
        request, container="chip-recommend", formal_job_id="formal",
        test_job_id="test", hermes_python="hermes-python",
    )
    assert final["status"] == "completed"
    assert calls == [[
        "/usr/bin/docker", "exec", "chip-recommend", "python",
        "scripts/run_open_web_test.py", "--search-limit", "4", "--visit-limit", "4",
        "--skill", "chip-specs", "--chip", "昇腾 910B", "--query", "官方规格",
        "--field", "vram_gb",
    ]]
