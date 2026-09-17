"""Tests for Agent result validation and the shared candidate Publisher."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from chip_model.database import init_db
from chip_model.pipeline import candidate_publisher
from chip_model.pipeline.candidate_publisher import publish_candidates
from chip_model.pipeline.data_agent import finish_agent_job, revalidate_ready_candidates
from chip_model.pipeline.candidate_validation import CandidateValidationError


def _fixture(tmp_path: Path) -> tuple[Path, int, int]:
    db_path = tmp_path / "publisher.db"
    init_db(db_path)
    diff_path = tmp_path / "source_snapshots" / "diffs" / "change.diff"
    diff_path.parent.mkdir(parents=True)
    diff_path.write_text(
        "--- before\n+++ after\n-TestChip GPU memory: 80 GB\n+TestChip GPU memory: 96 GB\n",
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as db:
        chip_id = db.execute(
            "INSERT INTO chips (chip_model,vram_gb) VALUES ('TestChip','80')"
        ).lastrowid
        run_id = db.execute(
            "INSERT INTO update_runs (run_type,mode,status,started_at,created_at) "
            "VALUES ('data_agent_cycle','automatic','running','2026-09-11T00:00:00Z','2026-09-11T00:00:00Z')"
        ).lastrowid
        link_id = db.execute(
            "INSERT INTO link_library (url,description,category) "
            "VALUES ('https://vendor.example/spec','TestChip spec','芯片硬件参数')"
        ).lastrowid
        check_id = db.execute(
            "INSERT INTO source_checks "
            "(run_id,link_id,requested_url,final_url,outcome,checked_at) "
            "VALUES (?,?, 'https://vendor.example/spec','https://vendor.example/spec','changed','2026-09-11T00:00:00Z')",
            (run_id, link_id),
        ).lastrowid
        diff_id = db.execute(
            "INSERT INTO source_diffs "
            "(source_check_id,run_id,link_id,diff_path,created_at) VALUES (?,?,?,?,?)",
            (check_id, run_id, link_id, str(diff_path), "2026-09-11T00:00:00Z"),
        ).lastrowid
        job_id = db.execute(
            "INSERT INTO data_agent_jobs "
            "(cycle_run_id,dedupe_key,job_type,skill_name,status,scheduled_for,started_at,"
            "worker_id,input_json,created_at,updated_at) VALUES "
            "(?,?,'agent_extract','chip-basic','running','2026-09-11T00:00:00Z',"
            "'2026-09-11T00:00:00Z','worker-1',?,'2026-09-11T00:00:00Z','2026-09-11T00:00:00Z')",
            (
                run_id,
                f"test:{run_id}",
                json.dumps(
                    {
                        "url": "https://vendor.example/spec",
                        "source_check_id": check_id,
                        "source_diff_id": diff_id,
                    }
                ),
            ),
        ).lastrowid
        db.commit()
    output = {
        "facts": [
            {
                "target_table": "chips",
                "entity_type": "chip",
                "entity_key": {"id": chip_id, "chip_model": "TestChip"},
                "field_name": "vram_gb",
                "proposed_value": "96",
                "unit": "GB",
                "evidence_text": "TestChip GPU memory: 96 GB",
                "source_url": "https://vendor.example/spec",
                "source_type": "official_datasheet",
                "confidence": "high",
            }
        ],
        "inbox": [],
    }
    finish_agent_job(
        job_id, "worker-1", status="succeeded", output=output, db_path=db_path
    )
    with sqlite3.connect(db_path) as db:
        candidate_id = db.execute(
            "SELECT id FROM extraction_candidates WHERE cycle_run_id=?", (run_id,)
        ).fetchone()[0]
    return db_path, chip_id, candidate_id


def test_agent_result_becomes_candidate_then_publishes_via_shadow_db(tmp_path: Path):
    db_path, chip_id, candidate_id = _fixture(tmp_path)
    result = publish_candidates(
        [candidate_id], db_path=db_path, backup_dir=tmp_path / "backups"
    )

    assert result["status"] == "success"
    assert Path(result["backup_path"]).is_file()
    assert "chip_search" in result["validation"]["shadow_tests"]
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=?", (chip_id,)).fetchone()[0] == "96"
        assert db.execute(
            "SELECT status FROM extraction_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0] == "published"
        provenance = db.execute(
            "SELECT old_value,new_value,source_url FROM field_provenance "
            "WHERE table_name='chips' AND row_id=? AND field_name='vram_gb'",
            (str(chip_id),),
        ).fetchone()
        assert provenance == ("80", "96", "https://vendor.example/spec")
        assert db.execute("SELECT COUNT(*) FROM candidate_publications").fetchone()[0] == 1
        assert db.execute(
            "SELECT status FROM update_runs WHERE run_type='data_agent_cycle'"
        ).fetchone()[0] == "success"


def test_invalid_chip_key_and_boolean_are_not_published(tmp_path: Path):
    db_path, chip_id, candidate_id = _fixture(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE extraction_candidates SET entity_key_json=?,field_name='is_released',"
            "proposed_value='true' WHERE id=?",
            (json.dumps({"chip_model": "TestChip", "vendor": "Unknown"}), candidate_id),
        )
        run_id = db.execute("SELECT cycle_run_id FROM extraction_candidates WHERE id=?", (candidate_id,)).fetchone()[0]
        db.commit()
    result = revalidate_ready_candidates(run_id, db_path=db_path)
    assert len(result["rejected"]) == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT status FROM extraction_candidates WHERE id=?", (candidate_id,)).fetchone()[0] == "rejected"
        assert db.execute("SELECT is_released FROM chips WHERE id=?", (chip_id,)).fetchone()[0] is None


def test_finish_requires_exact_existing_identity(tmp_path: Path):
    db_path, chip_id, _ = _fixture(tmp_path)
    with sqlite3.connect(db_path) as db:
        job_id = db.execute(
            "SELECT id FROM data_agent_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        db.execute("UPDATE data_agent_jobs SET status='running',worker_id='worker-1' WHERE id=?", (job_id,))
        db.commit()
    output = {"facts": [{
        "target_table": "chips", "entity_key": {"chip_model": "Other", "vendor": "AWS"},
        "field_name": "vram_gb", "proposed_value": "96",
        "evidence_text": "TestChip GPU memory: 96 GB",
        "source_url": "https://vendor.example/spec",
    }]}
    with pytest.raises(CandidateValidationError):
        finish_agent_job(job_id, "worker-1", status="succeeded", output=output, db_path=db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM extraction_candidates WHERE cycle_run_id=(SELECT cycle_run_id FROM data_agent_jobs WHERE id=?)", (job_id,)).fetchone()[0] == 1


def test_candidate_dedupe_keeps_distinct_entities(tmp_path: Path):
    db_path, _, candidate_id = _fixture(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO extraction_candidates "
            "(cycle_run_id,source_diff_id,owner_skill,target_table,entity_key_json,source_url,"
            "field_name,proposed_value,status,extractor_version,created_at) "
            "SELECT cycle_run_id,source_diff_id,owner_skill,target_table,?,source_url,"
            "field_name,proposed_value,status,extractor_version,created_at "
            "FROM extraction_candidates WHERE id=?",
            (json.dumps({"id": 999, "chip_model": "Other"}), candidate_id),
        )
        assert db.execute("SELECT COUNT(*) FROM extraction_candidates").fetchone()[0] == 2


def test_live_publish_rolls_back_business_write_when_audit_phase_fails(
    tmp_path: Path, monkeypatch
):
    db_path, chip_id, candidate_id = _fixture(tmp_path)
    original = candidate_publisher._apply_group

    def fail_during_live_apply(path, candidates, **kwargs):
        connection = kwargs.get("connection")
        if connection is None:
            return original(path, candidates, **kwargs)
        connection.execute("UPDATE chips SET vram_gb='96' WHERE id=?", (chip_id,))
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(candidate_publisher, "_apply_group", fail_during_live_apply)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        publish_candidates(
            [candidate_id], db_path=db_path, backup_dir=tmp_path / "backups"
        )

    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT vram_gb FROM chips WHERE id=?", (chip_id,)
        ).fetchone()[0] == "80"
        assert db.execute(
            "SELECT status FROM extraction_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0] == "ready_to_publish"
        assert db.execute("SELECT COUNT(*) FROM candidate_publications").fetchone()[0] == 0
