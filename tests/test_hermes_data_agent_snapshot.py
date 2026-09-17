"""Security tests for the restricted Hermes evidence snapshot bridge."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parent.parent / "scripts" / "hermes_data_agent_snapshot_server.py"
SPEC = importlib.util.spec_from_file_location("hermes_data_agent_snapshot_server", MODULE_PATH)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def test_reads_utf8_snapshot_inside_restricted_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "source_snapshots"
    evidence = root / "normalized" / "aa" / "fact.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("显存容量 96 GB", encoding="utf-8")
    monkeypatch.setattr(snapshot, "SNAPSHOT_ROOT", root.resolve())

    result = snapshot.read_snapshot(str(evidence))

    assert result["content"] == "显存容量 96 GB"
    assert result["truncated"] is False


def test_rejects_paths_outside_snapshot_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "source_snapshots"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(snapshot, "SNAPSHOT_ROOT", root.resolve())

    with pytest.raises(ValueError, match="只允许读取"):
        snapshot.read_snapshot(str(outside))


def test_large_snapshot_is_returned_in_bounded_pages(tmp_path: Path, monkeypatch):
    root = tmp_path / "source_snapshots"
    evidence = root / "normalized" / "large.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("A" * 100, encoding="utf-8")
    monkeypatch.setattr(snapshot, "SNAPSHOT_ROOT", root.resolve())

    first = snapshot.read_snapshot(str(evidence), max_bytes=40)
    second = snapshot.read_snapshot(
        str(evidence), offset=int(first["next_offset"]), max_bytes=40
    )

    assert first["content"] == "A" * 40
    assert first["truncated"] is True
    assert first["next_offset"] == 40
    assert second["offset"] == 40
