"""End-to-end tests for data-agent planning, routing and worker leases."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chip_model.database import init_db
from chip_model.pipeline.data_agent import (
    abort_agent_cycle,
    DataAgentOrchestrator,
    SKILL_FIELD_TARGETS,
    canonicalize_source_url,
    claim_agent_jobs,
    finish_agent_job,
    get_data_agent_status,
    recover_agent_jobs,
    recover_target_jobs,
    resolve_entity_alias,
    upsert_entity_alias,
)
from chip_model.pipeline.source_refresh import SourceRefresher
from tests.test_source_refresh import FakeResponse, FakeSession
from scripts.run_data_agent import _active_cycle_summary, _daily_cycle_due


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "agent.db"
    init_db(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO link_library (id,url,description,vendor,category) VALUES "
            "(1,'https://vendor.example/spec','TestChip 官方规格','Vendor','芯片硬件参数')"
        )
        db.commit()
    return path


def _orchestrator(db_path: Path, tmp_path: Path, responses: list[FakeResponse]):
    session = FakeSession(responses)

    def factory(**kwargs):
        kwargs["session"] = session
        kwargs["sleep_fn"] = lambda _: None
        kwargs["resolver"] = lambda _: ["93.184.216.34"]
        return SourceRefresher(**kwargs)

    return DataAgentOrchestrator(
        db_path=db_path,
        snapshot_dir=tmp_path / "source_snapshots",
        source_refresher_factory=factory,
        model_sync_fn=lambda **_: {
            "counts": {"selected": 0, "updated": 0, "unchanged": 0, "failed": 0},
            "results": [],
        },
    )


def test_cycle_reuses_one_fetch_across_skills_and_routes_changed_fields(tmp_path: Path):
    db_path = _db(tmp_path)
    first = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip VRAM 80 GB</main>", headers={"Content-Type": "text/html"})],
    )
    first_summary = first.run_cycle(limit=1, force=True)
    assert first_summary.completed_jobs == 2
    assert first_summary.awaiting_agent_jobs == 7  # 4 extract + 3 bounded discovery/quality jobs

    second = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip VRAM 96 GB</main>", headers={"Content-Type": "text/html"})],
    )
    summary = second.run_cycle(limit=1, force=True)
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id, limit=100)

    assert summary.source_run_id is not None
    assert summary.candidate_count >= 1
    assert any(item["field_name"] == "vram_gb" for item in status["candidates"])
    assert any(item["owner_skill"] == "chip-basic" for item in status["candidates"])
    extract_jobs = [job for job in status["jobs"] if job["job_type"] == "agent_extract"]
    assert {job["skill_name"] for job in extract_jobs} >= {
        "chip-catalog", "chip-basic", "chip-compute", "chip-interconnect"
    }
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(DISTINCT source_check_id) FROM parse_ledger WHERE run_id=?",
            (summary.run_id,),
        ).fetchone()[0] == 1


def test_agent_jobs_are_leased_and_completed_atomically(tmp_path: Path):
    db_path = _db(tmp_path)
    orchestrator = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip</main>", headers={"Content-Type": "text/html"})],
    )
    summary = orchestrator.run_cycle(limit=1, force=True)

    jobs = claim_agent_jobs(
        "hermes-worker-1", ["chip-catalog"], db_path=db_path, limit=1
    )
    assert len(jobs) == 1
    assert jobs[0]["worker_id"] == "hermes-worker-1"
    second_worker_jobs = claim_agent_jobs(
        "hermes-worker-2", ["chip-catalog"], db_path=db_path, limit=1
    )
    assert all(job["id"] != jobs[0]["id"] for job in second_worker_jobs)

    finish_agent_job(
        jobs[0]["id"],
        "hermes-worker-1",
        status="rejected",
        output={"reason": "没有新的官方芯片"},
        db_path=db_path,
    )
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id)
    finished = next(item for item in status["jobs"] if item["id"] == jobs[0]["id"])
    assert finished["status"] == "rejected"
    assert finished["output"]["reason"] == "没有新的官方芯片"


def test_status_reports_full_counts_when_recent_jobs_are_limited(tmp_path: Path):
    db_path = _db(tmp_path)
    summary = _orchestrator(
        db_path, tmp_path,
        [FakeResponse(200, b"<main>TestChip</main>", headers={"Content-Type": "text/html"})],
    ).run_cycle(limit=1, force=True)
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id, limit=1)
    assert len(status["jobs"]) == 1
    assert status["totals"]["jobs"] > len(status["jobs"])
    assert status["totals"]["statuses"]["awaiting_agent"] > 0
    with sqlite3.connect(db_path) as db:
        newest = db.execute(
            "SELECT MAX(id) FROM data_agent_jobs WHERE cycle_run_id=?", (summary.run_id,)
        ).fetchone()[0]
    assert status["jobs"][0]["id"] == newest


def test_interrupted_worker_jobs_can_be_released_for_immediate_recovery(tmp_path: Path):
    db_path = _db(tmp_path)
    orchestrator = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip</main>", headers={"Content-Type": "text/html"})],
    )
    summary = orchestrator.run_cycle(limit=1, force=True)
    jobs = claim_agent_jobs(
        "hermes-heartbeat", ["chip-catalog"], db_path=db_path, limit=1
    )
    assert len(jobs) == 1

    recovered = recover_agent_jobs(
        summary.run_id,
        "hermes-heartbeat",
        db_path=db_path,
        reason="model call interrupted",
    )

    assert recovered["released_jobs"] == 1
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id)
    released = next(item for item in status["jobs"] if item["id"] == jobs[0]["id"])
    assert released["status"] == "awaiting_agent"
    assert released["worker_id"] is None
    assert any(event["stage"] == "lease_recovery" for event in status["latest_cycle"]["events"])


def test_worker_reclaim_is_idempotent_while_it_has_running_jobs(tmp_path: Path):
    db_path = _db(tmp_path)
    orchestrator = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip</main>", headers={"Content-Type": "text/html"})],
    )
    orchestrator.run_cycle(limit=1, force=True)

    first = claim_agent_jobs(
        "hermes-heartbeat", ["chip-catalog", "chip-basic"], db_path=db_path, limit=1
    )
    second = claim_agent_jobs(
        "hermes-heartbeat", ["chip-catalog", "chip-basic"], db_path=db_path, limit=1
    )

    assert len(first) == 1
    assert len(second) == 1
    assert second[0]["id"] == first[0]["id"]
    assert second[0]["attempt_count"] == first[0]["attempt_count"] + 1
    third = claim_agent_jobs(
        "hermes-heartbeat", ["chip-catalog", "chip-basic"], db_path=db_path, limit=1
    )
    assert third[0]["attempt_count"] == second[0]["attempt_count"]


def test_active_cycle_blocks_unforced_overlap_and_can_be_aborted(tmp_path: Path):
    db_path = _db(tmp_path)
    first = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(200, b"<main>TestChip</main>", headers={"Content-Type": "text/html"})],
    )
    summary = first.run_cycle(limit=1, force=False)
    second = _orchestrator(db_path, tmp_path, [])

    resumed = _active_cycle_summary(db_path)
    assert resumed is not None
    assert resumed["run_id"] == summary.run_id
    assert resumed["resumed_existing_cycle"] is True
    assert resumed["awaiting_agent_jobs"] > 0

    with pytest.raises(RuntimeError, match="拒绝重叠启动"):
        second.run_cycle(limit=1, force=False)

    aborted = abort_agent_cycle(summary.run_id, db_path=db_path, reason="test cleanup")
    assert aborted["failed_jobs"] > 0
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id)
    assert status["latest_cycle"]["status"] == "failed"


def test_daily_guard_preserves_0200_start_and_suppresses_repeat_cycles(tmp_path: Path):
    db_path = _db(tmp_path)
    before_start = datetime(2026, 9, 15, 17, 59, tzinfo=timezone.utc)
    at_start = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
    assert not _daily_cycle_due(db_path, before_start)
    assert _daily_cycle_due(db_path, at_start)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO update_runs (run_type,status,started_at) "
            "VALUES ('data_agent_cycle','success','2026-09-15T18:00:00Z')"
        )
        db.commit()
    assert not _daily_cycle_due(db_path, at_start)
    assert not _daily_cycle_due(
        db_path, datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)
    )
    assert _daily_cycle_due(
        db_path, datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    )


def test_entity_aliases_are_shared_and_conflicts_are_rejected(tmp_path: Path):
    db_path = _db(tmp_path)
    alias_id = upsert_entity_alias(
        "chip", 7, "昇腾910B", "Ascend 910B", db_path=db_path
    )
    resolved = resolve_entity_alias("chip", "ascend-910b", db_path=db_path)
    assert resolved["id"] == alias_id
    assert resolved["entity_id"] == "7"


def test_skill_publish_fields_use_a_closed_ownership_map():
    assert "vram_gb" in SKILL_FIELD_TARGETS["chip-basic"]["chips"]
    assert "vram_gb" not in SKILL_FIELD_TARGETS["chip-compute"]["chips"]
    assert "precision_perf" in SKILL_FIELD_TARGETS["chip-compute"]["chips"]
    assert "arbitrary_sql_field" not in {
        field
        for targets in SKILL_FIELD_TARGETS.values()
        for fields in targets.values()
        for field in fields
    }


def test_discovery_url_canonicalization_removes_tracking_noise():
    assert canonicalize_source_url(
        "https://Vendor.Example/products/?utm_source=mail&b=2&a=1#spec"
    ) == "https://vendor.example/products?a=1&b=2"


def test_seed_discovery_fetches_targets_and_routes_extract_in_same_cycle(tmp_path: Path):
    db_path = _db(tmp_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE link_library SET url='https://vendor.example/products',"
            "description='TestChip 产品目录',category='芯片产品列表' WHERE id=1"
        )
        db.commit()

    first = _orchestrator(
        db_path,
        tmp_path,
        [
            FakeResponse(
                200,
                b'<main>TestChip product</main><a href="/products/testchip?utm_source=list">spec</a>',
                url="https://vendor.example/products",
                headers={"Content-Type": "text/html"},
            )
        ],
    )
    summary = first.run_cycle(limit=1, force=True)
    discovery_jobs = claim_agent_jobs(
        "hermes-worker-1", ["url-discovery"], db_path=db_path, limit=1
    )
    assert len(discovery_jobs) == 1
    assert discovery_jobs[0]["job_type"] == "agent_url_discovery"
    assert discovery_jobs[0]["input"]["outbound_links"] == [
        "https://vendor.example/products/testchip?utm_source=list"
    ]

    finished = finish_agent_job(
        discovery_jobs[0]["id"],
        "hermes-worker-1",
        status="succeeded",
        output={
            "discovered_sources": [
                {
                    "url": "https://vendor.example/products/testchip?utm_source=list",
                    "description": "TestChip 产品规格",
                    "category": "芯片硬件参数",
                    "vendor": "Vendor",
                    "role": "detail",
                }
            ]
        },
        db_path=db_path,
    )
    assert len(finished["detail_job_ids"]) == 1

    second = _orchestrator(
        db_path,
        tmp_path,
        [
            FakeResponse(
                200,
                b"<main>TestChip VRAM 96 GB</main>",
                url="https://vendor.example/products/testchip?utm_source=list",
                headers={"Content-Type": "text/html"},
            )
        ],
    )
    target_result = second.advance_target_phase(
        summary.run_id, job_ids=finished["detail_job_ids"]
    )
    status = get_data_agent_status(db_path=db_path, run_id=summary.run_id, limit=100)

    assert target_result["selected"] == 1
    assert target_result["awaiting_agent_jobs"] >= 1
    assert any(
        job["job_type"] == "agent_extract"
        and job["input"]["crawl_depth"] == 1
        for job in status["jobs"]
    )
    assert status["discoveries"][0]["status"] == "new"
    assert status["discoveries"][0]["parent_url"] == "https://vendor.example/products"
    assert status["discoveries"][0]["child_url"].startswith(
        "https://vendor.example/products/testchip"
    )


def test_interrupted_second_round_fetch_can_be_requeued_and_resumed(tmp_path: Path):
    db_path = _db(tmp_path)
    orchestrator = _orchestrator(db_path, tmp_path, [])
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO update_runs (id,run_type,status,started_at) "
            "VALUES (1,'data_agent_cycle','awaiting_agent','2026-09-15T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO data_agent_jobs "
            "(cycle_run_id,dedupe_key,job_type,skill_name,link_id,priority,status,"
            "worker_id,started_at,scheduled_for,input_json,created_at,updated_at) VALUES "
            "(1,'target-1','source_target_refresh','source-refresh',1,90,'running',"
            "'deterministic-target-fetcher','2026-09-15T00:00:00Z','2026-09-15T00:00:00Z',"
            "'{\"target_skills\":[\"chip-basic\"],\"url_role\":\"detail\",\"crawl_depth\":1}',"
            "'2026-09-15T00:00:00Z','2026-09-15T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO data_agent_jobs "
            "(cycle_run_id,dedupe_key,job_type,skill_name,priority,status,worker_id,"
            "scheduled_for,input_json,created_at,updated_at) VALUES "
            "(1,'agent-1','agent_extract','chip-basic',80,'running','hermes-heartbeat',"
            "'2026-09-15T00:00:00Z','{}','2026-09-15T00:00:00Z','2026-09-15T00:00:00Z')"
        )
        db.commit()

    recovered = recover_target_jobs(1, db_path=db_path, reason="Hermes call limit")
    assert recovered["released_jobs"] == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT status FROM data_agent_jobs WHERE dedupe_key='agent-1'"
        ).fetchone()[0] == "running"
        assert db.execute(
            "SELECT status FROM data_agent_jobs WHERE dedupe_key='target-1'"
        ).fetchone()[0] == "queued"

    resumed = _orchestrator(
        db_path,
        tmp_path,
        [FakeResponse(
            200, b"<main>TestChip VRAM 96 GB</main>",
            url="https://vendor.example/spec",
            headers={"Content-Type": "text/html"},
        )],
    ).advance_target_phase(1)
    assert resumed["selected"] == 1
    assert recovered["released_jobs"] == 1
    status = get_data_agent_status(db_path=db_path, run_id=1)
    assert any(event["stage"] == "target_recovery" for event in status["latest_cycle"]["events"])
    assert any(job["job_type"] == "agent_extract" and job["status"] == "awaiting_agent"
               for job in status["jobs"])
