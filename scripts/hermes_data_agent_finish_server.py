#!/usr/bin/env python3
"""Restricted server-side stdin bridge for completing one leased Agent job."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.pipeline.data_agent import DataAgentOrchestrator, finish_agent_job
from chip_model.pipeline.candidate_validation import CandidateValidationError


def main() -> int:
    payload = json.load(sys.stdin)
    job_id = int(payload["job_id"])
    worker_id = str(payload.get("worker_id") or "hermes-heartbeat")
    try:
        result = finish_agent_job(
            job_id, worker_id, status=str(payload["status"]),
            output=payload.get("output") or {},
            error_summary=str(payload.get("error_summary") or ""),
        )
    except CandidateValidationError as exc:
        result = finish_agent_job(
            job_id, worker_id, status="rejected",
            output={"reason": str(exc), "invalid_facts": payload.get("output", {}).get("facts", [])},
            error_summary="候选事实与现有实体或字段格式不兼容",
        )
    if result.get("detail_job_ids"):
        result["target_fetch"] = DataAgentOrchestrator().advance_target_phase(
            int(result["cycle_run_id"]),
            job_ids=result["detail_job_ids"],
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
