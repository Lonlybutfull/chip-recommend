"""Tests for the Hermes-to-app restricted auto-apply bridge."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parent.parent / "scripts" / "hermes_source_auto_apply.py"
SPEC = importlib.util.spec_from_file_location("hermes_source_auto_apply", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def _config(tmp_path: Path) -> Path:
    identity = tmp_path / "identity"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test", encoding="utf-8")
    known_hosts.write_text("test", encoding="utf-8")
    path = tmp_path / "source_refresh_gate.json"
    path.write_text(
        json.dumps({
            "transport": "ssh",
            "ssh": {
                "host": "81.70.231.92",
                "port": 22,
                "user": "lxc",
                "identity_file": str(identity),
                "known_hosts_file": str(known_hosts),
            },
        }),
        encoding="utf-8",
    )
    return path


def test_bridge_passes_compact_json_to_only_allowed_remote_command(
    tmp_path: Path, monkeypatch, capsys,
):
    config = _config(tmp_path)
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"source_update_id": 7, "chip_model": "H100", "fields": {"vram_gb": "96"}}),
        encoding="utf-8",
    )
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs["input"]
        return SimpleNamespace(returncode=0, stdout='{"status":"success"}\n', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge.main(["--config", str(config), "--payload", str(payload)]) == 0
    assert observed["command"][-1] == "source-auto-apply"
    assert observed["command"][-2] == "lxc@81.70.231.92"
    assert json.loads(observed["input"])["source_update_id"] == 7
    assert '"status":"success"' in capsys.readouterr().out


def test_bridge_rejects_non_object_payload_before_ssh(tmp_path: Path, monkeypatch, capsys):
    config = _config(tmp_path)
    payload = tmp_path / "payload.json"
    payload.write_text("[]", encoding="utf-8")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge.main(["--config", str(config), "--payload", str(payload)]) == 1
    assert called is False
    assert "必须是 JSON 对象" in capsys.readouterr().err
