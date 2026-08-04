#!/usr/bin/env python
"""
Migrate benchmarks from parse1.db into data.db chip_model_benchmarks.

parse1.db has 34,110 benchmark rows with source/source_url/citation/evidence_level
that our current DB lacks. We match hardware_sku to our chip_model names and
insert with enriched provenance.

Usage:
    cd 芯片+模型
    python scripts/migrate_parse1_benchmarks.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARSE_DB = Path("E:/BUPT_PS/P_0/芯片+模型/parse11/parse1/data/parse1.db")
OUR_DB = PROJECT_ROOT / "data" / "data.db"

# ── Dest field mapping: parse1 → our (only the columns we have) ──
COLUMN_MAP = {
    "model_id": "model_id",
    "model_name": "model_name",
    "model_params_b": "model_params_b",
    "hardware_vendor": "hardware_vendor",
    "hardware_sku": "hardware_sku",
    "chip_count": "chip_count",
    "gpu_count": "gpu_count",
    "workload_type": "workload_type",
    "scenario": "scenario",
    "task": "task",
    "framework": "framework",
    "precision": "precision",
    "batch_size": "batch_size",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "concurrency": "concurrency",
    "throughput_tok_s": "throughput_tok_s",
    "throughput_samples_s": "throughput_samples_s",
    "ttft_ms": "ttft_ms",
    "tpot_ms": "tpot_ms",
    "mfu_pct": "mfu_pct",
    "gpu_hours": "gpu_hours",
    "training_tokens_T": "training_tokens_T",
    "training_workload": "training_workload",
    "training_gpu_count": "training_gpu_count",
    "tokens_per_gpu_day_b": "tokens_per_gpu_day_b",
    "tokens_per_watt": "tokens_per_watt",
    # Source fields (new columns we just added)
    "source": "source_type",
    "source_url": "source_url",
    "source_title": "source_title",
    "citation": "citation",
    "reference_url": "reference_url",
    "confidence": "confidence",
    "evidence_level": "evidence_level",
    "methodology": "methodology",
    "metric_unit": "metric_unit",
    "test_date": "test_date",
    "notes": "notes",
    "region": "region",
    "measured_at": "suite_name",  # e.g. "v5.0"
    "record_id": "record_id",
    "extra_attrs": "extra_attrs",
}


def load_our_chips(db_path: Path) -> set[str]:
    """Get the set of chip_model names from our chips + benchmark tables."""
    our = sqlite3.connect(str(db_path))
    # Collect all chip names from chips table
    rows = our.execute("SELECT DISTINCT chip_model FROM chips").fetchall()
    chips_set = {r[0] for r in rows if r[0]}
    # Also from existing benchmarks
    rows2 = our.execute("SELECT DISTINCT chip_model FROM chip_model_benchmarks").fetchall()
    chips_set |= {r[0] for r in rows2 if r[0]}
    our.close()
    return chips_set


def build_fuzzy_map(parse_db: Path, our_chips: set[str]) -> dict[str, str]:
    """Build a mapping from parse hardware_sku → our chip_model name."""
    parse = sqlite3.connect(str(parse_db))
    parse_skus = parse.execute("SELECT DISTINCT hardware_sku FROM benchmarks").fetchall()
    parse.close()

    mapping: dict[str, str] = {}
    for (sku,) in parse_skus:
        if not sku:
            continue
        # Direct match
        if sku in our_chips:
            mapping[sku] = sku
            continue
        # Substring match: our_chip in sku
        for oc in our_chips:
            if oc.lower() in sku.lower():
                mapping[sku] = oc
                break
        # Fallback: sku substring in our_chip
        if sku not in mapping:
            for oc in our_chips:
                if sku.lower() in oc.lower():
                    mapping[sku] = oc
                    break
    return mapping


def migrate(dry_run: bool = False, limit: int = 0) -> None:
    """Main migration logic."""
    if not PARSE_DB.exists():
        print(f"ERROR: parse1.db not found at {PARSE_DB}")
        sys.exit(1)

    print(f"Loading chip names from {OUR_DB}...")
    our_chips = load_our_chips(OUR_DB)
    print(f"  → {len(our_chips)} unique chip_model names")

    print(f"Building hardware_sku → chip_model mapping...")
    mapping = build_fuzzy_map(PARSE_DB, our_chips)
    mapped = len(mapping)
    print(f"  → {mapped} parse hardware_sku values mapped to our chips")

    parse = sqlite3.connect(str(PARSE_DB))
    our = sqlite3.connect(str(OUR_DB))

    # Get our dest column names
    dest_cols_raw = our.execute("PRAGMA table_info(chip_model_benchmarks)").fetchall()
    dest_col_names = [c[1] for c in dest_cols_raw]

    # Build the list of parse columns to read
    parse_cols = list(COLUMN_MAP.keys())
    # Also read raw parse columns for extra_attrs
    all_parse_cols_raw = parse.execute("PRAGMA table_info(benchmarks)").fetchall()
    all_parse_col_names = [c[1] for c in all_parse_cols_raw]

    # Read all parse benchmarks
    sql = f"SELECT {', '.join(parse_cols)} FROM benchmarks"
    if limit > 0:
        sql += f" LIMIT {limit}"
    rows = parse.execute(sql).fetchall()

    # Check which dest columns exist (handle new vs old schema)
    available_dest = set(dest_col_names)
    # Build the subset of COLUMN_MAP that our dest table actually has
    valid_map = {}
    for parse_col, dest_col in COLUMN_MAP.items():
        if dest_col in available_dest:
            valid_map[parse_col] = dest_col

    total = len(rows)
    inserted = 0
    skipped = 0
    errors = 0

    print(f"\nMigrating {total} benchmark rows...")

    for i, row in enumerate(rows):
        row_dict = dict(zip(parse_cols, row))
        hw_sku = row_dict.get("hardware_sku", "")
        our_chip = mapping.get(hw_sku)

        if not our_chip:
            skipped += 1
            continue

        # Build insert row for our DB
        insert_data = {}
        for parse_col, dest_col in valid_map.items():
            val = row_dict.get(parse_col)
            insert_data[dest_col] = val

        # Override chip_model with our normalized name
        insert_data["chip_model"] = our_chip
        # Ensure suite_name is set
        if not insert_data.get("suite_name"):
            insert_data["suite_name"] = row_dict.get("source", "")
        # Set created_at if missing
        if not insert_data.get("created_at"):
            insert_data["created_at"] = row_dict.get("created_at", "")

        dest_cols = list(insert_data.keys())
        dest_vals = list(insert_data.values())
        placeholders = ", ".join("?" for _ in dest_cols)

        if dry_run:
            inserted += 1
        else:
            try:
                our.execute(
                    f"INSERT INTO chip_model_benchmarks ({', '.join(dest_cols)}) "
                    f"VALUES ({placeholders})",
                    dest_vals,
                )
                inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error on row {i}: {e}")

        if (i + 1) % 5000 == 0:
            if not dry_run:
                our.commit()
            print(f"  ... {i+1}/{total} processed ({inserted} inserted, {skipped} skipped)")

    if not dry_run:
        our.commit()

    parse.close()
    our.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done!")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (no chip match): {skipped}")
    print(f"  Errors: {errors}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Migrate parse1.db benchmarks → data.db")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (no actual insert)")
    ap.add_argument("--limit", type=int, default=0, help="Limit source rows (0 = all)")
    args = ap.parse_args()
    migrate(dry_run=args.dry_run, limit=args.limit)
