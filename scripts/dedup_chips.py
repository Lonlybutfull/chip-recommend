#!/usr/bin/env python3
"""Chip Dedup — find and merge duplicate chip entries in data.db.

Duplicate patterns found:
  1. H100 SXM5 80GB vs H100 SXM5 80 GB (space in VRAM)
  2. Ascend 910C OAM 128GB vs 昇腾910C (Chinese vs English)
  3. MI300X 192GB vs MI300X vs Radeon Instinct MI300X (prefix/suffix variants)
  4. B200 SXM 192GB vs B200 (detailed vs bare)
  5. B1 (64GB) vs Ascend 910B B1 64GB (truncated name)

Strategy:
  1. Normalize names: strip VRAM suffix, unify spacing, strip Radeon/Instinct prefix
  2. Group by (vendor, normalized_name) → find duplicates
  3. Keep the "best" record (by priority: seed > dbgpu, more-fields > fewer)
  4. Merge: update kept record with missing fields from duplicates
  5. Delete duplicate records via CLI `parse1 chip delete`

Usage:
    python scripts/dedup_chips.py --dry-run    # Preview duplicates
    python scripts/dedup_chips.py              # Execute merge + delete
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CLI_PY = HERE / "scripts" / "run_cli.py"


def run_cli(*args):
    """Run parse1 CLI, return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(CLI_PY)] + list(args),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(HERE),
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def normalize_chip_name(name: str) -> str:
    """Normalize chip model name for dedup matching.

    Transformations:
      - Strip VRAM suffixes: "80GB", "80 GB", "141GB", "192GB", etc.
      - Strip form-factor suffixes: "SXM5", "SXM4", "OAM", "PCIe", "NVL16"
      - Strip "Radeon Instinct" and "Radeon Pro" and "Radeon" prefix
      - Normalize spaces: "H100 SXM5 80 GB" → "h100"
      - Remove Chinese parentheses content like "(壁砺100)"
    """
    name = name.strip()

    # Remove parenthetical content (Chinese aliases, VRAM in parentheses)
    name = re.sub(r'\s*\([^)]*\)', '', name)

    # Remove common suffixes
    suffixes = [
        r'\s+\d{2,4}\s*GB',           # 80GB, 141 GB, 64 GB
        r'\s+SXM\d+',                  # SXM5, SXM4
        r'\s+OAM\b',                   # OAM
        r'\s+PCIe\b(\s+\d+\s*GB)?',   # PCIe, PCIe 80 GB
        r'\s+NVL\d*\b',               # NVL, NVL16
        r'\s+\d+GB\b',                 # standalone "80GB"
        r'\s+CNX\b',                   # H100 CNX
        r'\s+FHHL\b',                  # FHHL
        r'\s+DGXS?\s+\d+\s*GB',       # DGXS 16 GB
        r'\s+Max-Q\b',
        r'\s+Mobile\b',
        r'\s+Mobile Refresh\b',
        r'\s+Passive\b',
        r'\s+Server\b',
        r'\s+Subsystem\b',
        r'\s+X2\b',
        r'\s+CEO Edition\b',
        r'\s+PROD\b',
        r'\s+B1\b',                    # B1 board variant
        r'\s+48\s*GB\b',              # 48GB variant suffix
        r'\s+32\s*GB\b',
        r'\s+16\s*GB\b',
        r'\s+96\s*GB\b',
        r'\s+72\s*GB\b',
        r'\s+24\s*GB\b',
        r'\s+12\s*GB\b',
        r'\s+8\s*GB\b',
        r'\s+4\s*GB\b',
    ]
    for pat in suffixes:
        name = re.sub(pat, '', name, flags=re.IGNORECASE)

    # Remove "Radeon Instinct", "Radeon Pro", "Radeon" prefix
    name = re.sub(r'^Radeon\s+(?:Instinct|Pro|AI\s+PRO)\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^Radeon\s+', '', name, flags=re.IGNORECASE)

    # Remove "Tesla" / "Quadro" / "GRID" / "DRIVE" prefix (old NVIDIA branding)
    name = re.sub(r'^(Tesla|Quadro|GRID|DRIVE)\s+', '', name, flags=re.IGNORECASE)

    # Remove "GeForce " prefix (shouldn't be in DB, but just in case)
    name = re.sub(r'^GeForce\s+', '', name, flags=re.IGNORECASE)

    # Normalize to lowercase, collapse whitespace
    name = name.lower().strip()
    name = re.sub(r'\s+', ' ', name)

    return name


def load_chips() -> list[dict]:
    """Load all chips from the DB."""
    from chip_model.database import get_db
    with get_db(readonly=True) as db:
        rows = db.execute("SELECT * FROM chips ORDER BY vendor, chip_model").fetchall()
    return [dict(r) for r in rows]


def find_duplicates(chips: list[dict]) -> list[dict]:
    """Group chips by (vendor, normalized_name) and find duplicates."""
    groups = defaultdict(list)
    for c in chips:
        vendor = (c.get("vendor") or "").lower().strip()
        norm = normalize_chip_name(c.get("chip_model") or "")
        key = f"{vendor}::{norm}"
        groups[key].append(c)

    # Find groups with >1 chip
    dup_groups = []
    for key, group in groups.items():
        if len(group) > 1:
            dup_groups.append(group)

    # Sort by vendor
    dup_groups.sort(key=lambda g: (g[0].get("vendor", ""), g[0].get("chip_model", "")))
    return dup_groups


def rank_chip(chip: dict) -> tuple:
    """Score a chip for which one to KEEP.
    Higher score = better candidate.
    Priority:
      1. seed/original data over dbgpu import (id <= 112 = original)
      2. More non-empty fields = better data coverage
      3. Has description / ecosystem notes (Chinese data)
      4. Lower id (older = more curated)
    """
    score = 0

    # Original chips (id <= 112) are more curated
    cid = int(chip.get("id", 0))
    if cid <= 112:
        score += 1000

    # More fields filled = better
    filled = sum(1 for v in chip.values() if v and str(v).strip())
    score += filled

    # Has Chinese/localized data
    desc = chip.get("description") or ""
    eco = chip.get("ecosystem_notes") or ""
    if any('一' <= c <= '鿿' for c in desc + eco):
        score += 500

    # Has ecosystem evaluation
    if chip.get("maturity_level"):
        score += 200

    return (score, -cid)


def merge_chips(keep: dict, duplicates: list[dict]) -> dict:
    """Merge fields from duplicates into keep record.
    Only fills fields that are empty in keep but have values in duplicates.
    """
    updates = {}

    for dup in duplicates:
        for k, v in dup.items():
            if k in ("id", "created_at", "updated_at"):
                continue
            if v is not None and str(v).strip():
                # Only merge if keep doesn't have this field
                keep_val = keep.get(k)
                if not keep_val or not str(keep_val).strip():
                    updates[k] = str(v).strip()

    return updates


def execute_merge(keep_chip: dict, dup_chips: list[dict], dry_run: bool = False) -> dict:
    """Update the kept chip, delete duplicates."""
    results = {"merged_fields": 0, "deleted": 0, "errors": 0}

    chip_model = keep_chip["chip_model"]
    keep_id = keep_chip["id"]

    # Merge fields
    updates = merge_chips(keep_chip, dup_chips)
    if updates:
        source = {
            "source_type": "web_crawl",
            "source_url": "https://github.com/Lonlybutfull/chip-recommend/tree/master/scripts/dedup_chips.py",
            "source_detail": "Dedup merge — fields from duplicate records",
            "confidence": "medium",
            "is_official": False,
            "notes": f"Merged from {len(dup_chips)} duplicate chip records during dedup",
        }

        if dry_run:
            print(f"  --> Would UPDATE [{keep_id}] {chip_model}: {list(updates.keys())}")
        else:
            rc, stdout, stderr = run_cli(
                "chip", "update", "--id", str(keep_id),
                "-d", json.dumps(updates, ensure_ascii=False),
                "-s", json.dumps(source, ensure_ascii=False),
            )
            if rc == 0:
                print(f"  [OK] MERGED [{keep_id}] {chip_model}: {len(updates)} fields")
                results["merged_fields"] = len(updates)
            else:
                print(f"  [ERR] MERGE [{keep_id}]: {stderr[:100]}")
                results["errors"] += 1

    # Delete duplicates
    for dup in dup_chips:
        dup_id = dup["id"]
        dup_name = dup["chip_model"]
        del_source = {
            "source_type": "community",
            "source_url": "",
            "notes": f"Dedup: merged into [{keep_id}] {chip_model} (same chip, different name)",
        }

        if dry_run:
            print(f"  --> Would DELETE [{dup_id}] {dup_name}")
            results["deleted"] += 1
        else:
            rc, stdout, stderr = run_cli(
                "chip", "delete", "--id", str(dup_id), "--force",
                "-s", json.dumps(del_source, ensure_ascii=False),
            )
            if rc == 0:
                print(f"  [OK] DELETED [{dup_id}] {dup_name}")
                results["deleted"] += 1
            else:
                print(f"  [ERR] DELETE [{dup_id}]: {stderr[:100]}")
                results["errors"] += 1

    return results


def main():
    parser = argparse.ArgumentParser(description="Dedup chips in data.db")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--max-groups", type=int, default=0, help="Limit dedup groups")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"

    print("=" * 60)
    print(f"Chip Dedup ({mode})")
    print("=" * 60)

    # Load
    chips = load_chips()
    print(f"\n[1] Total chips: {len(chips)}")

    # Find duplicates
    dup_groups = find_duplicates(chips)
    print(f"[2] Duplicate groups found: {len(dup_groups)}")

    if args.max_groups > 0:
        dup_groups = dup_groups[:args.max_groups]
        print(f"    (limited to {args.max_groups} groups)")

    if not dup_groups:
        print("\nNo duplicates found.")
        return

    # Process each group
    total_merged = 0
    total_deleted = 0
    total_errors = 0

    for i, group in enumerate(dup_groups):
        group.sort(key=rank_chip, reverse=True)
        keep = group[0]
        dups = group[1:]

        vendor = keep.get("vendor", "?")
        names = [c["chip_model"] for c in group]
        print(f"\n{'─'*50}")
        print(f"Group {i+1}: {vendor} — {len(group)} variants")
        print(f"  KEEP: [{keep['id']}] {keep['chip_model']}")
        for d in dups:
            print(f"  DEL:  [{d['id']}] {d['chip_model']}")

        result = execute_merge(keep, dups, dry_run=args.dry_run)
        total_merged += result["merged_fields"]
        total_deleted += result["deleted"]
        total_errors += result["errors"]

    print(f"\n{'='*50}")
    print(f"Dedup Summary ({mode}):")
    print(f"  Groups processed: {len(dup_groups)}")
    print(f"  Fields merged:    {total_merged}")
    print(f"  Duplicates deleted: {total_deleted}")
    print(f"  Errors:           {total_errors}")
    print(f"{'='*50}")

    if args.dry_run:
        print(f"\nRun without --dry-run to execute merges and deletions.")


if __name__ == "__main__":
    main()
