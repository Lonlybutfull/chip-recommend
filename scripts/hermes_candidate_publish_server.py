#!/usr/bin/env python3
"""Restricted server-side stdin bridge for the shared candidate Publisher."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import get_db_path, get_project_root
from chip_model.pipeline.candidate_publisher import publish_candidates


def main() -> int:
    payload = json.load(sys.stdin)
    ids = [int(value) for value in payload.get("candidate_ids", [])]
    selected_db = get_db_path().resolve()
    backup_root = (
        selected_db.parent / "backups"
        if selected_db.parent.parent.name == "test_runs"
        else Path(get_project_root()) / "backups"
    )
    result = publish_candidates(
        ids,
        db_path=selected_db,
        backup_dir=backup_root / "candidate-publisher",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
