#!/usr/bin/env python3
"""Phase B: Ingest Pipeline — read enriched CSV → import/update via CLI.

Reads data/enriched_chips_v2.csv and calls:
  - parse1 chip add -d <fields_json> -s <source_json>    (for new chips)
  - parse1 chip update --id <id> -d <fields_json> -s <source_json>  (for existing)

The key rule: ALL writes go through CLI to ensure field_provenance records.

Usage:
    python scripts/run_ingest.py                         # Full ingest
    python scripts/run_ingest.py --dry-run               # Preview only
    python scripts/run_ingest.py --limit 50              # First 50 chips
    python scripts/run_ingest.py --new-only               # Only new-to-DB chips
    python scripts/run_ingest.py --vendor NVIDIA          # Single vendor
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

NOW = datetime.now().isoformat(timespec="seconds")

CLI_PY = HERE / "scripts" / "run_cli.py"

# ── Fields that identify a chip (must be present for add) ──
IDENTITY_FIELDS = ["vendor", "vendor_display", "chip_model", "chip_series", "chip_type", "tier"]

# ── Extra metadata cols (not chip fields) ──
META_COLS = {"_source_urls", "_source_type", "_extraction_confidence", "_extraction_notes",
             "_is_new", "_db_id", "is_new_to_db", "discovery_method", "source_urls", "notes",
             "vendor_region"}


def run_cli(*args):
    """Run parse1 CLI and return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(CLI_PY)] + list(args),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(HERE),
    )
    return result.returncode, result.stdout, result.stderr


def load_enriched_csv(csv_path: Path) -> list[dict]:
    """Load enriched chip CSV."""
    if not csv_path.exists():
        print(f"[ERROR] Not found: {csv_path}")
        print(f"  Run enrich pipeline first.")
        sys.exit(1)
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def get_db_id_for_chip(chip_model: str) -> int | None:
    """Query DB for a chip's id by chip_model."""
    rc, stdout, stderr = run_cli("chip", "profile", chip_model)
    if rc == 0:
        try:
            profile = json.loads(stdout)
            return profile.get("id")
        except Exception:
            pass
    return None


def ingest_chips(rows: list[dict], dry_run: bool = False) -> dict:
    """Process each chip row: add if new, update if existing."""
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    for i, row in enumerate(rows):
        chip_model = (row.get("chip_model") or "").strip()
        if not chip_model:
            skipped += 1
            continue

        # Separate chip fields from meta columns
        fields = {}
        for k, v in row.items():
            if k in META_COLS or k.startswith("_"):
                continue
            if v and str(v).strip():
                fields[k] = str(v).strip()

        if not fields:
            skipped += 1
            continue

        # Build source dict
        source_urls = row.get("_source_urls", row.get("source_urls", ""))
        source_type = row.get("_source_type", "web_crawl")
        confidence = row.get("_extraction_confidence", "medium")

        source = {
            "source_type": source_type,
            "source_url": (source_urls or "").split("|")[0] or
                          f"https://www.google.com/search?q={chip_model}+specifications",
            "source_detail": f"Enrichment pipeline — Phase B auto-enrich",
            "confidence": confidence or "medium",
            "is_official": (source_type == "official_datasheet"),
            "notes": row.get("_extraction_notes", row.get("notes", "")),
        }

        is_new = row.get("_is_new", row.get("is_new_to_db", "1")) == "1"
        db_id = row.get("_db_id", "")

        if is_new or not db_id:
            # New chip — chip add
            if not fields.get("vendor") or not fields.get("chip_model"):
                fields["chip_model"] = chip_model

            cmd = ["chip", "add",
                   "-d", json.dumps(fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]

            if dry_run:
                print(f"  [DRY] INSERT {chip_model} ({len(fields)} fields)")
                inserted += 1
            else:
                rc, stdout, stderr = run_cli(*cmd)
                if rc == 0:
                    result = json.loads(stdout)
                    print(f"  INSERT [{result.get('chip_id', '?')}] {chip_model} ({len(fields)} fields)")
                    inserted += 1
                elif "UNIQUE constraint" in (stderr or "") or "already exists" in (stderr or ""):
                    # Maybe it exists — try update
                    db_id = get_db_id_for_chip(chip_model)
                    if db_id:
                        cmd2 = ["chip", "update", "--id", str(db_id),
                                "-d", json.dumps(fields, ensure_ascii=False),
                                "-s", json.dumps(source, ensure_ascii=False)]
                        rc2, _, err2 = run_cli(*cmd2)
                        if rc2 == 0:
                            print(f"  UPDATE [{db_id}] {chip_model} ({len(fields)} fields) — was existing")
                            updated += 1
                        else:
                            print(f"  SKIP {chip_model} — exists but update failed: {err2[:80]}")
                            skipped += 1
                    else:
                        print(f"  SKIP {chip_model} — exists but id lookup failed")
                        skipped += 1
                else:
                    print(f"  ERROR {chip_model}: {stderr[:120]}" if stderr else f"  ERROR {chip_model}")
                    errors += 1
        else:
            # Existing chip — chip update
            cmd = ["chip", "update", "--id", str(db_id),
                   "-d", json.dumps(fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]

            if dry_run:
                print(f"  [DRY] UPDATE [{db_id}] {chip_model} ({len(fields)} fields)")
                updated += 1
            else:
                rc, stdout, stderr = run_cli(*cmd)
                if rc == 0:
                    print(f"  UPDATE [{db_id}] {chip_model} ({len(fields)} fields)")
                    updated += 1
                else:
                    err_msg = stderr[:120] if stderr else "Unknown error"
                    print(f"  ERROR UPDATE [{db_id}] {chip_model}: {err_msg}")
                    errors += 1

        if (i + 1) % 25 == 0:
            print(f"  ... progress: {i+1}/{len(rows)} "
                  f"(ins={inserted}, upd={updated}, skip={skipped}, err={errors})")

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(
        description="Phase B: Ingest enriched chips into DB via CLI"
    )
    parser.add_argument("--input", default=str(HERE / "data" / "enriched_chips_v2.csv"),
                        help="Enriched CSV from run_enrich_full.py")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — no DB writes")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to N chips (0=all)")
    parser.add_argument("--new-only", action="store_true",
                        help="Only ingest new-to-DB chips")
    parser.add_argument("--vendor", default=None,
                        help="Only ingest one vendor's chips")
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = load_enriched_csv(input_path)
    print(f"[ingest] Loaded {len(rows)} chips from {input_path}")

    # Filter
    if args.new_only:
        rows = [r for r in rows if r.get("_is_new", r.get("is_new_to_db", "1")) == "1"]
        print(f"[ingest] New-only: {len(rows)} chips")

    if args.vendor:
        rows = [r for r in rows
                if (r.get("vendor_display", r.get("vendor", "")).lower() == args.vendor.lower())]
        print(f"[ingest] Vendor filter '{args.vendor}': {len(rows)} chips")

    if args.limit > 0:
        rows = rows[:args.limit]
        print(f"[ingest] Limited to {args.limit} chips")

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"[ingest] Mode: {mode}")
    print(f"[ingest] Processing {len(rows)} chips...\n")

    result = ingest_chips(rows, dry_run=args.dry_run)

    print(f"\n{'='*50}")
    print(f"Ingest Summary ({mode}):")
    print(f"  Inserted: {result['inserted']}")
    print(f"  Updated:  {result['updated']}")
    print(f"  Skipped:  {result['skipped']}")
    print(f"  Errors:   {result['errors']}")
    print(f"  Total:    {sum(result.values())}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
