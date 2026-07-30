#!/usr/bin/env python3
"""Phase 1: Import seed chips (26 known-good chips) via CLI chip add.

NOT directly manipulating the DB — uses parse1 chip add commands.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent

def run_cli(*args):
    """Run parse1 CLI and return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(HERE / "scripts" / "run_cli.py")] + list(args),
        capture_output=True, text=True, encoding='utf-8',
    )
    return result.returncode, result.stdout, result.stderr

def main():
    # Read discovered chips
    with open(HERE / "data" / "discovered_chips.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # Only seed chips (26)
    seed_chips = [r for r in rows if r["discovery_method"] == "seed"]

    inserted = 0
    skipped = 0
    errors = 0

    for chip in seed_chips:
        name = chip["chip_model"].strip()
        if not name:
            continue

        # Build fields dict
        fields = {
            "chip_model": name,
            "vendor": chip["vendor"] or "",
            "vendor_display": chip["vendor_display"] or "",
            "chip_series": chip["chip_series"] or "",
            "chip_type": chip["chip_type"] or "GPU",
            "tier": chip["tier"] or "datacenter",
        }
        # Don't import empty vendor
        if not fields["vendor"]:
            fields.pop("vendor")

        source = {
            "source_type": "web_crawl",
            "source_url": (chip.get("source_urls") or f"https://www.google.com/search?q={name}+AI+chip").split("|")[0],
            "source_detail": f"Seed chip from known catalog",
            "confidence": "high",
            "is_official": False,
            "notes": "Initial identity import — specs to be enriched via WebSearch",
        }

        data_json = json.dumps(fields, ensure_ascii=False)
        src_json = json.dumps(source, ensure_ascii=False)

        rc, stdout, stderr = run_cli("chip", "add", "-d", data_json, "-s", src_json)

        if rc == 0:
            result = json.loads(stdout)
            print(f"  INSERT [{result['chip_id']}] {name}")
            inserted += 1
        elif "UNIQUE constraint" in stderr or "already exists" in stderr:
            print(f"  SKIP {name} (already in DB)")
            skipped += 1
        else:
            # Try to check if it exists
            rc2, out2, _ = run_cli("chip", "profile", name)
            if rc2 == 0:
                print(f"  SKIP {name} (already in DB, confirmed)")
                skipped += 1
            else:
                print(f"  ERROR {name}: {stderr[:120]}")
                errors += 1

    print(f"\n=== Phase 1: Seed chips ===")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped:  {skipped}")
    print(f"  Errors:   {errors}")

if __name__ == "__main__":
    main()
