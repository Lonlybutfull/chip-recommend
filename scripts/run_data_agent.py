#!/usr/bin/env python3
"""Run and inspect the complete AISHPerf data-agent control plane."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db, get_db_path  # noqa: E402
from chip_model.pipeline.test_workspace import start_test_cycle  # noqa: E402
from chip_model.pipeline.data_agent import (  # noqa: E402
    abort_agent_cycle,
    DataAgentOrchestrator,
    claim_agent_jobs,
    finish_agent_job,
    get_data_agent_status,
    recover_agent_jobs,
    recover_target_jobs,
    revalidate_ready_candidates,
)


def _active_cycle_summary(db_path: Path) -> dict | None:
    """Return the unfinished cycle so a heartbeat resumes instead of overlapping it."""
    with get_db(db_path, readonly=True) as db:
        row = db.execute(
            "SELECT id FROM update_runs WHERE run_type='data_agent_cycle' "
            "AND status IN ('running','awaiting_agent','awaiting_publish') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    status = get_data_agent_status(db_path=db_path, run_id=int(row["id"]), limit=10000)
    cycle = status["latest_cycle"]
    jobs = status["jobs"]
    counts: dict[str, int] = {}
    for job in jobs:
        value = str(job["status"])
        counts[value] = counts.get(value, 0) + 1
    source_run_id = (cycle.get("counts") or {}).get("source_run_id")
    return {
        "run_id": int(cycle["id"]),
        "status": str(cycle["status"]),
        "planned_jobs": len(jobs),
        "completed_jobs": counts.get("succeeded", 0) + counts.get("rejected", 0),
        "awaiting_agent_jobs": counts.get("awaiting_agent", 0) + counts.get("running", 0),
        "failed_jobs": counts.get("failed", 0),
        "candidate_count": len(status["candidates"]),
        "inbox_count": len(status["inbox"]),
        "source_run_id": source_run_id,
        "started_at": cycle["started_at"],
        "finished_at": cycle.get("finished_at") or "",
        "resumed_existing_cycle": True,
    }


def _daily_cycle_due(db_path: Path, now: datetime | None = None) -> bool:
    """Start at most one new cycle per Beijing day, not before 02:00."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    if local_now.hour < 2:
        return False
    with get_db(db_path, readonly=True) as db:
        row = db.execute(
            "SELECT started_at FROM update_runs WHERE run_type='data_agent_cycle' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row or not row["started_at"]:
        return True
    started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started.astimezone(ZoneInfo("Asia/Shanghai")).date() < local_now.date()


def _read_json(path: str) -> dict:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("结果必须是 JSON 对象。")
    return value


def _is_isolated_test_db(db_path: Path) -> bool:
    """Only a marked test clone may be resumed while formal cycles are paused."""
    marker_path = db_path.parent / "test_mode.json"
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        return (
            marker.get("mode") == "test"
            and Path(str(marker.get("db_path") or "")).resolve() == db_path.resolve()
            and db_path.parent.parent.name == "test_runs"
        )
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AISHPerf 数据抓取智能体")
    parser.add_argument("--db", default=str(get_db_path()))
    parser.add_argument("--snapshot-dir", default=str(ROOT / "data" / "source_snapshots"))
    sub = parser.add_subparsers(dest="action", required=True)

    run_parser = sub.add_parser("run", help="规划并执行一个完整周期的确定性阶段")
    run_parser.add_argument("--limit", type=int, default=50)
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--resume-only", action="store_true",
                            help="测试门控只续跑现有周期，不另建周期")
    run_parser.add_argument("--proxy", default="")
    run_parser.add_argument("--max-attempts", type=int, default=2)
    run_parser.add_argument(
        "--resume-stale-targets", action="store_true",
        help="恢复并续跑超过 30 分钟仍为 running 的中断目标抓取",
    )
    run_parser.add_argument(
        "--daily-start-guard", action="store_true",
        help="仅在北京时间 02:00 后且当天尚未开启过周期时创建新周期",
    )

    test_parser = sub.add_parser("test", help="创建隔离数据库副本并启动测试周期")
    test_parser.add_argument("--limit", type=int, default=5)
    test_parser.add_argument("--proxy", default="")
    test_parser.add_argument("--max-attempts", type=int, default=2)

    status_parser = sub.add_parser("status", help="查看最近周期、任务、候选和 inbox")
    status_parser.add_argument("--run-id", type=int)
    status_parser.add_argument("--limit", type=int, default=100)

    claim_parser = sub.add_parser("claim", help="Hermes worker 领取语义任务")
    claim_parser.add_argument("--worker", required=True)
    claim_parser.add_argument("--skills", required=True, help="逗号分隔 Skill 名称")
    claim_parser.add_argument("--limit", type=int, default=5)
    claim_parser.add_argument("--lease-minutes", type=int, default=30)

    finish_parser = sub.add_parser("finish", help="Hermes worker 完成或拒绝任务")
    finish_parser.add_argument("--job-id", required=True, type=int)
    finish_parser.add_argument("--worker", required=True)
    finish_parser.add_argument("--status", required=True, choices=["succeeded", "rejected", "failed"])
    finish_parser.add_argument("--result", default="-", help="JSON 文件；- 表示标准输入")
    finish_parser.add_argument("--error", default="")

    recover_parser = sub.add_parser("recover", help="释放异常中断 worker 的未完成租约")
    recover_parser.add_argument("--run-id", required=True, type=int)
    recover_parser.add_argument("--worker", required=True)
    recover_parser.add_argument("--reason", default="worker interrupted")

    target_recovery_parser = sub.add_parser(
        "recover-targets", help="中断的第二轮抓取进程退出后重新排队目标 URL"
    )
    target_recovery_parser.add_argument("--run-id", required=True, type=int)
    target_recovery_parser.add_argument("--reason", default="target fetch interrupted")

    targets_parser = sub.add_parser("targets", help="继续访问已排队的第二轮目标 URL")
    targets_parser.add_argument("--run-id", required=True, type=int)

    revalidate_parser = sub.add_parser("revalidate", help="审计待发布候选的实体键和枚举，不写业务表")
    revalidate_parser.add_argument("--run-id", required=True, type=int)

    abort_parser = sub.add_parser("abort", help="安全终止异常周期的未完成控制面任务")
    abort_parser.add_argument("--run-id", required=True, type=int)
    abort_parser.add_argument("--reason", default="operator cancelled")

    args = parser.parse_args(argv)
    db_path = Path(args.db).resolve()
    if args.action == "test":
        print(json.dumps(start_test_cycle(
            source_db=db_path, limit=args.limit, proxy=args.proxy or None,
            max_attempts=max(1, args.max_attempts),
        ), ensure_ascii=False, indent=2))
        return 0
    if args.action == "run":
        if not _is_isolated_test_db(db_path):
            print(json.dumps({"status": "paused", "wakeAgent": False,
                              "reason": "正式巡检已暂停；只允许手动芯片隔离测试。"},
                             ensure_ascii=False))
            return 0
        if not args.force:
            active = _active_cycle_summary(db_path)
            if active:
                if args.resume_stale_targets:
                    recovery = recover_target_jobs(
                        active["run_id"], db_path=db_path,
                        reason="previous target fetch process interrupted",
                        older_than_minutes=30,
                    )
                    if recovery["job_ids"]:
                        DataAgentOrchestrator(
                            db_path=db_path, snapshot_dir=Path(args.snapshot_dir).resolve()
                        ).advance_target_phase(
                            active["run_id"], job_ids=recovery["job_ids"],
                        )
                        active = _active_cycle_summary(db_path)
                print(json.dumps(active, ensure_ascii=False, indent=2))
                return 0
            if args.resume_only:
                print(json.dumps({"status": "not_due", "awaiting_agent_jobs": 0,
                                  "failed_jobs": 0, "resumed_existing_cycle": False},
                                 ensure_ascii=False))
                return 0
            if args.daily_start_guard and not _daily_cycle_due(db_path):
                print(json.dumps({
                    "status": "not_due", "awaiting_agent_jobs": 0,
                    "failed_jobs": 0, "resumed_existing_cycle": False,
                }, ensure_ascii=False))
                return 0
        summary = DataAgentOrchestrator(
            db_path=db_path, snapshot_dir=Path(args.snapshot_dir).resolve()
        ).run_cycle(
            limit=max(1, args.limit),
            force=args.force,
            proxy=args.proxy or None,
            max_attempts=max(1, args.max_attempts),
        )
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 2 if summary.status == "failed" else 0
    if args.action == "status":
        print(
            json.dumps(
                get_data_agent_status(db_path=db_path, run_id=args.run_id, limit=args.limit),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "claim":
        jobs = claim_agent_jobs(
            args.worker,
            args.skills.split(","),
            db_path=db_path,
            limit=args.limit,
            lease_minutes=args.lease_minutes,
        )
        print(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "recover":
        print(
            json.dumps(
                recover_agent_jobs(
                    args.run_id,
                    args.worker,
                    db_path=db_path,
                    reason=args.reason,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "recover-targets":
        print(json.dumps(
            recover_target_jobs(args.run_id, db_path=db_path, reason=args.reason),
            ensure_ascii=False, indent=2,
        ))
        return 0
    if args.action == "targets":
        print(json.dumps(
            DataAgentOrchestrator(
                db_path=db_path, snapshot_dir=Path(args.snapshot_dir).resolve()
            ).advance_target_phase(args.run_id),
            ensure_ascii=False, indent=2,
        ))
        return 0
    if args.action == "revalidate":
        print(json.dumps(
            revalidate_ready_candidates(args.run_id, db_path=db_path),
            ensure_ascii=False, indent=2,
        ))
        return 0
    if args.action == "abort":
        print(
            json.dumps(
                abort_agent_cycle(args.run_id, db_path=db_path, reason=args.reason),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = _read_json(args.result)
    finished = finish_agent_job(
        args.job_id,
        args.worker,
        status=args.status,
        output=result,
        error_summary=args.error,
        db_path=db_path,
    )
    if finished.get("detail_job_ids"):
        finished["target_fetch"] = DataAgentOrchestrator(
            db_path=db_path, snapshot_dir=Path(args.snapshot_dir).resolve()
        ).advance_target_phase(
            int(finished["cycle_run_id"]),
            job_ids=finished["detail_job_ids"],
        )
    print(json.dumps(finished, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
