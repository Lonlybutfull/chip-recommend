#!/usr/bin/env python3
"""Migrate models from old parse1.db to current data.db via CLI.

Source: E:/BUPT_PS/P_0/芯片+模型/parse1/芯片+模型/parse1.db (206 models, 18 columns)
Target: data/data.db (70 models, same 18 columns)

Strategy:
  - For each model in old DB, check if model_id exists in new DB
  - New model → parse1 model add (CLI)
  - Existing model → parse1 model update (CLI) if missing fields
  - Source provenance: official_datasheet, HF API URL, high confidence

Usage:
    python scripts/migrate_models.py --dry-run    # Preview only
    python scripts/migrate_models.py              # Full migration
    python scripts/migrate_models.py --limit 10   # Test with 10 models
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

OLD_DB = Path("E:/BUPT_PS/P_0/芯片+模型/parse1/芯片+模型/parse1.db")
CLI_PY = HERE / "scripts" / "run_cli.py"

# Fields to migrate (all 18 columns except id, created_at, updated_at)
MODEL_FIELDS = [
    "model_id", "author", "pipeline_tag", "library_name", "tags",
    "downloads", "likes", "last_modified", "private", "gated",
    "architecture_family", "total_params_b",
    "config_json", "card_data_json", "api_response_json",
]

# Fields to skip when updating (raw JSON payloads are large, handled separately)
SKIP_ON_UPDATE = {"api_response_json"}  # Don't update huge raw API response


def run_cli(*args):
    """Run parse1 CLI, return (rc, stdout_stripped, stderr_stripped)."""
    result = subprocess.run(
        [sys.executable, str(CLI_PY)] + list(args),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(HERE),
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def load_old_models() -> list[dict]:
    """Load all models from old parse1.db."""
    if not OLD_DB.exists():
        print(f"[ERROR] Old DB not found: {OLD_DB}")
        sys.exit(1)

    old = sqlite3.connect(str(OLD_DB))
    old.row_factory = sqlite3.Row
    rows = old.execute("SELECT * FROM models ORDER BY id").fetchall()
    old.close()
    return [dict(r) for r in rows]


def load_new_model_ids() -> set[str]:
    """Load existing model_id values from current data.db."""
    from chip_model.database import get_db
    with get_db(readonly=True) as db:
        rows = db.execute("SELECT model_id FROM models").fetchall()
    return {r["model_id"] for r in rows}


def get_new_model_row_id(model_id: str) -> int | None:
    """Get the DB row id for an existing model."""
    from chip_model.database import get_db
    with get_db(readonly=True) as db:
        r = db.execute(
            "SELECT id FROM models WHERE model_id = ?", (model_id,)
        ).fetchone()
    return r["id"] if r else None


def migrate(rows: list[dict], dry_run: bool = False) -> dict:
    """Migrate model rows via CLI."""
    existing_ids = load_new_model_ids()

    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    for i, row in enumerate(rows):
        model_id = (row.get("model_id") or "").strip()
        if not model_id:
            skipped += 1
            continue

        # Build fields dict
        fields = {}
        for k in MODEL_FIELDS:
            v = row.get(k)
            if v is not None and str(v).strip():
                fields[k] = str(v).strip()

        # Build source
        source = {
            "source_type": "official_datasheet",
            "source_url": f"https://huggingface.co/{model_id}",
            "source_detail": "Migrated from old parse1.db (previously fetched via HF API)",
            "confidence": "high",
            "is_official": True,
            "notes": f"Model migration — original model data from parse1.db",
        }

        is_new = model_id not in existing_ids

        if is_new:
            cmd = ["model", "add",
                   "-d", json.dumps(fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]
        else:
            db_id = get_new_model_row_id(model_id)
            if not db_id:
                skipped += 1
                continue
            # For updates, skip fields that are already filled
            update_fields = {k: v for k, v in fields.items() if k not in SKIP_ON_UPDATE}
            if not update_fields:
                skipped += 1
                continue
            cmd = ["model", "update", "--id", str(db_id),
                   "-d", json.dumps(update_fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]

        if dry_run:
            action = "INSERT" if is_new else f"UPDATE"
            print(f"  [DRY] {action} {model_id} ({len(fields)} fields)")
            inserted += 1 if is_new else 0
            updated += 0 if is_new else 1
            continue

        rc, stdout, stderr = run_cli(*cmd)
        if rc == 0:
            if is_new:
                inserted += 1
                print(f"  INSERT {model_id}")
            else:
                updated += 1
                print(f"  UPDATE {model_id}")
        elif "UNIQUE constraint" in (stderr or "") or "already exists" in (stderr or ""):
            print(f"  SKIP {model_id} (already exists)")
            skipped += 1
        else:
            print(f"  ERROR {model_id}: {stderr[:120]}")
            errors += 1

        if (i + 1) % 50 == 0:
            print(f"  ... progress: {i+1}/{len(rows)} (ins={inserted}, upd={updated})")

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Migrate models from old parse1.db")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--limit", type=int, default=0, help="Limit models (0=all)")
    parser.add_argument("--source", default=str(OLD_DB), help="Source DB path")
    args = parser.parse_args()

    global OLD_DB
    OLD_DB = Path(args.source)

    print("=" * 60)
    print(f"Model Migration: {OLD_DB} → data/data.db")
    print("=" * 60)

    # Load
    all_rows = load_old_models()
    print(f"\n[1] Old DB: {len(all_rows)} models")

    existing = load_new_model_ids()
    new_count = sum(1 for r in all_rows if r.get("model_id", "") not in existing)
    exist_count = sum(1 for r in all_rows if r.get("model_id", "") in existing)
    print(f"[2] New models: {new_count}, Already in target: {exist_count}")

    rows = all_rows[:args.limit] if args.limit > 0 else all_rows
    print(f"[3] Processing {len(rows)} models...\n")

    mode = "DRY RUN" if args.dry_run else "LIVE"
    result = migrate(rows, dry_run=args.dry_run)

    print(f"\n{'='*50}")
    print(f"Migration Summary ({mode}):")
    print(f"  Inserted: {result['inserted']}")
    print(f"  Updated:  {result['updated']}")
    print(f"  Skipped:  {result['skipped']}")
    print(f"  Errors:   {result['errors']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
