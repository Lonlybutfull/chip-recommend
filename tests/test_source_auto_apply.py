"""Tests for validated, review-free Hermes source updates."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from chip_model.database import init_db
from chip_model.pipeline.source_auto_apply import AutoApplyError, apply_source_update


def _fixture(tmp_path: Path, *, url: str = "https://www.nvidia.com/en-us/data-center/h100/"):
    project = tmp_path / "project"
    snapshots = project / "data" / "source_snapshots" / "diffs"
    snapshots.mkdir(parents=True)
    db_path = project / "data" / "data.db"
    init_db(db_path)
    diff_path = snapshots / "h100.diff"
    diff_path.write_text(
        "--- before\n+++ after\n@@ -1 +1 @@\n-H100 GPU memory 80 GB\n+H100 GPU memory 96 GB\n",
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO chips (chip_model, vendor, vram_gb) VALUES (?,?,?)",
            ("H100 SXM5 80GB", "NVIDIA", "80"),
        )
        chip_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO link_library (url, description, vendor, category) VALUES (?,?,?,?)",
            (url, "NVIDIA H100 官方规格页", "NVIDIA", "芯片硬件参数"),
        )
        link_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO update_runs (run_type, mode, status, started_at, finished_at) "
            "VALUES ('source_refresh','apply-state','success','2026-09-09T00:00:00Z','2026-09-09T00:00:01Z')"
        )
        run_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO source_checks "
            "(run_id, link_id, requested_url, final_url, outcome, http_status, checked_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, link_id, url, url, "changed", 200, "2026-09-09T00:00:00Z"),
        )
        check_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        candidates = [{
            "field": "vram_gb", "label": "显存容量", "change_type": "added",
            "evidence": "H100 GPU memory 96 GB",
        }]
        db.execute(
            "INSERT INTO source_diffs "
            "(source_check_id, run_id, link_id, diff_path, candidate_fields_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (check_id, run_id, link_id, str(diff_path), json.dumps(candidates), "2026-09-09T00:00:01Z"),
        )
        update_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
    payload = {
        "source_update_id": update_id,
        "chip_model": "H100 SXM5 80GB",
        "fields": {"vram_gb": "96"},
        "evidence": {"vram_gb": "H100 GPU memory 96 GB"},
    }
    return project, db_path, chip_id, update_id, payload


def test_auto_apply_updates_chip_with_provenance_and_backup(tmp_path: Path):
    project, db_path, chip_id, update_id, payload = _fixture(tmp_path)

    result = apply_source_update(payload, db_path=db_path, project_root=project)

    assert result.status == "success"
    assert result.business_tables_modified is True
    assert result.provenance_written == 1
    assert "chip_search" in result.validation["existing_function_smoke"]
    assert Path(result.backup_path).is_file()
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=?", (chip_id,)).fetchone()[0] == "96"
        provenance = db.execute(
            "SELECT old_value,new_value,source_url,confidence,is_official "
            "FROM field_provenance WHERE table_name='chips' AND row_id=? AND field_name='vram_gb'",
            (str(chip_id),),
        ).fetchone()
        assert provenance == (
            "80", "96", "https://www.nvidia.com/en-us/data-center/h100/", "high", "1"
        )
        audit = db.execute(
            "SELECT status, source_diff_id FROM source_auto_applies WHERE chip_id=?", (chip_id,)
        ).fetchone()
        assert audit == ("success", update_id)
        events = db.execute(
            "SELECT actor,stage,status,details_json FROM update_run_events ORDER BY id"
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in events] == [
            ("validator", "candidate_validation", "started"),
            ("validator", "candidate_validation", "success"),
            ("validator", "shadow_test", "success"),
            ("publisher", "publish", "started"),
            ("publisher", "publish", "success"),
        ]
        assert json.loads(events[-1][3])["provenance_written"] == 1


def test_auto_apply_rejects_unapproved_domain_without_writing(tmp_path: Path):
    project, db_path, chip_id, _, payload = _fixture(tmp_path, url="https://news.example/h100")

    with pytest.raises(AutoApplyError, match="不在自动更新官方域名白名单"):
        apply_source_update(payload, db_path=db_path, project_root=project)

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=?", (chip_id,)).fetchone()[0] == "80"
        assert db.execute("SELECT COUNT(*) FROM field_provenance").fetchone()[0] == 0
        failure = db.execute(
            "SELECT status,message FROM update_run_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert failure[0] == "failed"
        assert "自动拒绝" in failure[1]


def test_auto_apply_requires_verbatim_added_evidence(tmp_path: Path):
    project, db_path, chip_id, _, payload = _fixture(tmp_path)
    payload["evidence"]["vram_gb"] = "someone said it is 96 GB"

    with pytest.raises(AutoApplyError, match="不是差异中的新增原文"):
        apply_source_update(payload, db_path=db_path, project_root=project)

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=?", (chip_id,)).fetchone()[0] == "80"


def test_auto_apply_requires_field_semantics_in_evidence(tmp_path: Path):
    project, db_path, chip_id, _, payload = _fixture(tmp_path)
    diff_path = project / "data" / "source_snapshots" / "diffs" / "h100.diff"
    diff_path.write_text(
        "--- before\n+++ after\n@@ -1 +1 @@\n-H100 release 80 GB\n+H100 release 96 GB\n",
        encoding="utf-8",
    )
    payload["evidence"]["vram_gb"] = "H100 release 96 GB"

    with pytest.raises(AutoApplyError, match="缺少对应的字段语义"):
        apply_source_update(payload, db_path=db_path, project_root=project)

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=?", (chip_id,)).fetchone()[0] == "80"


def test_auto_apply_is_idempotent_per_diff_and_chip(tmp_path: Path):
    project, db_path, _, _, payload = _fixture(tmp_path)
    apply_source_update(payload, db_path=db_path, project_root=project)

    with pytest.raises(AutoApplyError, match="处理过"):
        apply_source_update(payload, db_path=db_path, project_root=project)
