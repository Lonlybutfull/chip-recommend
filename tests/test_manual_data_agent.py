"""Test-mode isolation and protected manual controls."""

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from chip_model.database import get_db_path, set_db_path
from chip_model.pipeline.test_workspace import (
    create_test_workspace, latest_test_db, save_test_seed,
)
from chip_model.pipeline.manual_control import (
    execute_open_web_manual_request, queue_manual_run,
)
from chip_model.server import app


def _db(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent.parent / "schema.sql").read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO link_library (url,description,category,accessible) "
            "VALUES ('https://example.org/original','旧来源','芯片官方规格','1')"
        )
    return path


def test_test_workspace_seeds_and_changes_are_isolated(tmp_path):
    formal = _db(tmp_path)
    with sqlite3.connect(formal) as db:
        old_run = db.execute(
            "INSERT INTO update_runs (run_type,mode,status,started_at,created_at) "
            "VALUES ('data_agent_cycle','automatic','awaiting_agent','2026-09-15','2026-09-15')"
        ).lastrowid
        db.execute(
            "INSERT INTO data_agent_jobs "
            "(cycle_run_id,dedupe_key,job_type,skill_name,status,scheduled_for,created_at,updated_at) "
            "VALUES (?, 'old-task', 'agent_extract', 'chip-basic', 'awaiting_agent', "
            "'2026-09-15', '2026-09-15', '2026-09-15')",
            (old_run,),
        )
    save_test_seed("https://example.org/test-spec", "测试来源", "芯片官方规格", source_db=formal)
    first = create_test_workspace(formal)
    test_db = Path(first["db_path"])
    assert latest_test_db(formal) == test_db
    assert first["seed_ids"]
    with sqlite3.connect(test_db) as clone:
        assert clone.execute("SELECT status FROM data_agent_jobs WHERE dedupe_key='old-task'").fetchone()[0] == "rejected"
        assert clone.execute("SELECT status FROM update_runs WHERE id=?", (old_run,)).fetchone()[0] == "interrupted"
        clone.execute("UPDATE chips SET description='测试' WHERE id=999")
        clone.execute("INSERT INTO link_library (url,description) VALUES ('https://example.org/clone','测试')")
        clone.commit()
        assert clone.execute("SELECT COUNT(*) FROM link_library").fetchone()[0] == 3
    with sqlite3.connect(formal) as live:
        assert live.execute("SELECT status FROM data_agent_jobs WHERE dedupe_key='old-task'").fetchone()[0] == "awaiting_agent"
        assert live.execute("SELECT COUNT(*) FROM link_library").fetchone()[0] == 1
        assert live.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    marker = json.loads((test_db.parent / "test_mode.json").read_text(encoding="utf-8"))
    assert marker["mode"] == "test"


def test_manual_api_needs_admin_token_and_test_seed_does_not_change_formal(tmp_path):
    formal = _db(tmp_path)
    original = get_db_path()
    set_db_path(formal)
    try:
        client = TestClient(app)
        denied = client.post("/api/v1/data-agent/manual-run", json={"mode": "test"})
        assert denied.status_code == 403
        denied_seed = client.post("/api/v1/data-agent/seeds", json={
            "mode": "test", "url": "https://example.org/spec", "description": "官方",
            "category": "芯片官方规格"
        })
        assert denied_seed.status_code == 403
        token_path = formal.parent / "manual_control" / "admin_token"
        token_path.parent.mkdir()
        token_path.write_text("test-admin-secret", encoding="utf-8")
        headers = {"X-Data-Agent-Admin": "test-admin-secret"}
        added = client.post("/api/v1/data-agent/seeds", headers=headers, json={
            "mode": "test", "url": "https://example.org/spec", "description": "官方",
            "category": "芯片官方规格"
        })
        assert added.status_code == 200
        assert client.get("/api/v1/data-agent/seeds?mode=test", headers=headers).json()["seeds"][0]["url"] == "https://example.org/spec"
        with sqlite3.connect(formal) as db:
            assert db.execute("SELECT COUNT(*) FROM link_library").fetchone()[0] == 1
        queued = client.post("/api/v1/data-agent/manual-run", headers=headers, json={"mode": "test"})
        assert queued.status_code == 200
        assert queued.json()["status"] == "queued"
        assert client.get("/api/v1/data-agent/manual-run?mode=test").json()["latest_request"]["id"] == queued.json()["id"]
    finally:
        set_db_path(original)


def test_admin_token_is_rejected_over_public_http(tmp_path):
    formal = _db(tmp_path)
    original = get_db_path()
    set_db_path(formal)
    try:
        token_path = formal.parent / "manual_control" / "admin_token"
        token_path.parent.mkdir()
        token_path.write_text("test-admin-secret", encoding="utf-8")
        client = TestClient(app, base_url="http://public.example")
        response = client.get(
            "/api/v1/data-agent/seeds?mode=test",
            headers={"X-Data-Agent-Admin": "test-admin-secret"},
        )
        assert response.status_code == 426
        assert "HTTPS" in response.json()["detail"]
    finally:
        set_db_path(original)


def test_open_web_manual_request_runs_in_api_background_worker(tmp_path, monkeypatch):
    formal = _db(tmp_path)
    original = get_db_path()
    set_db_path(formal)
    try:
        request = queue_manual_run(
            "test", 2, pipeline="open_web", skill="chip-benchmark",
            chips=["TestChip X1"], target_fields=["throughput_tok_s"]
        )
        assert request["status"] == "running"
        assert request["skill"] == "chip-benchmark"
        assert request["target_fields"] == ["throughput_tok_s"]

        def fake_run(**kwargs):
            assert kwargs["source_db"] == formal
            assert kwargs["skill_name"] == "chip-benchmark"
            assert kwargs["target_fields"] == ["throughput_tok_s"]
            return {
                "session_id": "fixture-session",
                "status": "success",
                "db_path": str(tmp_path / "test_runs" / "fixture-session" / "data.db"),
                "counts": {"validated": 1},
                "formal_database_modified": False,
            }

        monkeypatch.setattr(
            "chip_model.pipeline.open_web_test.run_open_web_test", fake_run
        )
        completed = execute_open_web_manual_request(request["id"])

        assert completed["status"] == "completed"
        assert completed["result"]["session_id"] == "fixture-session"
        assert completed["result"]["formal_database_modified"] is False
    finally:
        set_db_path(original)


def test_open_web_manual_request_rejects_unknown_skill(tmp_path):
    formal = _db(tmp_path)
    original = get_db_path()
    set_db_path(formal)
    try:
        try:
            queue_manual_run(
                "test", 2, pipeline="open_web", skill="model-catalog",
                chips=["TestChip X1"],
            )
        except ValueError as exc:
            assert "不支持的信息类别" in str(exc)
        else:
            raise AssertionError("unknown skill should be rejected")
    finally:
        set_db_path(original)


def test_open_web_manual_request_rejects_cross_category_field(tmp_path):
    formal = _db(tmp_path)
    original = get_db_path()
    set_db_path(formal)
    try:
        try:
            queue_manual_run(
                "test", 2, pipeline="open_web", skill="chip-specs",
                chips=["TestChip X1"], target_fields=["throughput_tok_s"],
            )
        except ValueError as exc:
            assert "字段不属于基础参数" in str(exc)
        else:
            raise AssertionError("cross-category field should be rejected")
    finally:
        set_db_path(original)
