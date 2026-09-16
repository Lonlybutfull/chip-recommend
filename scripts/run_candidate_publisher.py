#!/usr/bin/env python3
"""Publish one validated candidate group through the shared safe Publisher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chip_model.pipeline.candidate_publisher import publish_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-ids", required=True)
    parser.add_argument("--db", default=str(ROOT / "data" / "data.db"))
    parser.add_argument("--backup-dir", default=str(ROOT / "backups" / "candidate-publisher"))
    args = parser.parse_args()
    ids = [int(value) for value in args.candidate_ids.split(",") if value.strip()]
    result = publish_candidates(ids, db_path=args.db, backup_dir=args.backup_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
