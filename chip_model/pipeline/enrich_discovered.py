#!/usr/bin/env python3
"""Detail enrichment pipeline for discovered AI chips.

Reads data/discovered_chips.csv → for each chip, produces structured spec data
via WebSearch + WebFetch → writes intermediate CSV → imports via database helpers.

This script is designed to work with Claude Code sub-agents for deep search.
Each chip is enriched independently, with results checkpointed to CSV.

Usage:
    python chip_model/pipeline/enrich_discovered.py
    python chip_model/pipeline/enrich_discovered.py --input data/discovered_chips.csv
    python chip_model/pipeline/enrich_discovered.py --max-chips 10
    python chip_model/pipeline/enrich_discovered.py --import    # also import after enrich
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

# ── All 78 chip columns (matching chips table schema) ──
CHIP_FIELDS = [
    # Identity
    "vendor", "vendor_display", "vendor_region", "chip_series", "chip_model",
    "chip_type", "usage", "tier",
    # Architecture
    "architecture", "arch_codename", "generation", "process_node_nm",
    "foundry", "die_size_mm2", "transistors_b", "package_type", "is_chiplet",
    # Memory
    "vram_gb", "vram_type", "vram_bus_bit", "vram_bw_gb_s", "vram_clock_mhz",
    # Compute Units
    "compute_units", "tensor_cores", "rt_cores", "shading_units", "sm_count",
    # Cache
    "l1_cache_kb", "l2_cache_mb", "on_chip_sram_mb",
    # Precision
    "precision_support", "precision_perf",
    # Clock/Power/Physical
    "base_clock_mhz", "boost_clock_mhz", "tdp_w", "max_power_w",
    "psu_w", "power_connector", "board_length_mm", "board_width_mm",
    "slot_width", "form_factor", "bus_interface",
    # Interconnect
    "interconnect_bw_gb_s", "interconnect_tech", "network_interface",
    # Software
    "software_stack", "compatible_frameworks",
    # Lifecycle
    "release_date", "production_status", "eol_date", "target_market",
    "is_released", "expected_release_date", "known_specs", "unconfirmed_items",
    # Pricing
    "price_usd", "price_cny_wan", "price_period", "price_notes",
    # Description
    "description", "highlights", "limitations",
    "target_workloads", "typical_deployment", "competitor_comparison",
    # Ecosystem
    "ecosystem_notes", "maturity_level", "framework_compat", "sw_stack",
    "cuda_compat", "cloud_available", "cluster_scale",
    "key_strength", "key_weakness",
]

# Extra columns for enrichment metadata
ENRICH_META_COLS = ["_source_urls", "_extraction_confidence", "_extraction_notes"]

OUTPUT_COLUMNS = CHIP_FIELDS + ENRICH_META_COLS

NOW = datetime.now().isoformat(timespec="seconds")


def load_discovered_chips(csv_path: Path) -> list[dict]:
    """Load discovered chips from CSV."""
    if not csv_path.exists():
        print(f"[ERROR] Input CSV not found: {csv_path}")
        print(f"  Run discover_chips.py first to generate it.")
        sys.exit(1)
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"[enrich] Loaded {len(rows)} chips from {csv_path}")
    return rows


def load_existing_enriched(output_path: Path) -> set[str]:
    """Load already-enriched chip names from an existing output CSV."""
    if not output_path.exists():
        return set()
    with open(output_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {r.get("chip_model", "") for r in rows if r.get("chip_model")}


def build_enrich_prompt(chip: dict) -> str:
    """Build a search-focused prompt for a given chip.

    This is consumed by Claude Code sub-agents that perform WebSearch + WebFetch.
    """
    name = chip.get("chip_model", chip.get("chip_series", "Unknown"))
    vendor = chip.get("vendor_display", chip.get("vendor", ""))
    return (
        f"Search the web for detailed hardware specifications of the AI accelerator chip: "
        f"**{name}** by {vendor}.\n\n"
        f"Search queries to try:\n"
        f'1. "{name} specifications datasheet GPU specs vram tdp"\n'
        f'2. "{vendor} {name} 硬件规格 参数 显存 功耗 算力"\n'
        f"3. Check official vendor website first, then tech news sites (Tom's Hardware, "
        f"ServeTheHome, AnandTech, 半导体行业观察, etc.)\n\n"
        f"For each spec found, record the exact value AND the source URL.\n"
        f"Focus on these fields in order of priority:\n"
        f"- vram_gb (显存容量), vram_type (HBM2e/HBM3/HBM3e), vram_bw_gb_s (显存带宽)\n"
        f"- tdp_w (功耗), process_node_nm (制程), architecture (架构名), arch_codename (架构代号)\n"
        f"- precision_support (FP32/FP16/BF16/FP8/INT8/INT4/FP4), precision_perf (各精度算力 TFLOPS)\n"
        f"- interconnect_bw_gb_s (互联带宽), interconnect_tech (NVLink/PCIe/Infinity Fabric)\n"
        f"- compute_units (计算单元数), tensor_cores (张量核心数)\n"
        f"- release_date (发布日期), production_status (量产/已发布/未发布)\n"
        f"- price_usd / price_cny_wan (价格)\n"
        f"- die_size_mm2 (芯片面积), transistors_b (晶体管数)\n\n"
        f"Return the results as a JSON object mapping field names to values, "
        f"plus a _source_urls field with all URLs used."
    )


def init_output_csv(output_path: Path) -> None:
    """Create output CSV with headers if it doesn't exist."""
    if not output_path.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()


def append_enriched_row(output_path: Path, row: dict) -> None:
    """Append one enriched chip row to the output CSV."""
    with open(output_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writerow(row)


def import_enriched_chips(
    csv_path: Path,
    dry_run: bool = False,
) -> dict:
    """Import enriched chip data into database via add_chip / update_chip_fields.

    Returns summary dict with insert/update/skip counts.
    """
    from chip_model.database import get_db, add_chip, update_chip_fields

    if not csv_path.exists():
        print(f"[ERROR] Enriched CSV not found: {csv_path}")
        return {"inserted": 0, "updated": 0, "skipped": 0}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if dry_run:
        print(f"[import] DRY RUN — would process {len(rows)} chips")
        for r in rows[:5]:
            print(f"  {r.get('chip_model', '?')} ({r.get('vendor_display', '?')})")
        return {"inserted": len(rows), "updated": 0, "skipped": 0}

    inserted = 0
    updated = 0
    skipped = 0

    with get_db() as db:
        for row in rows:
            chip_model = (row.get("chip_model", "") or "").strip()
            if not chip_model:
                skipped += 1
                continue

            # Separate data fields from meta columns
            fields = {}
            for k, v in row.items():
                if k in ENRICH_META_COLS or k.startswith("_"):
                    continue
                if v and v.strip():
                    fields[k] = v.strip()

            if not fields:
                skipped += 1
                continue

            # Build source
            source_urls = row.get("_source_urls", "")
            source = {
                "source_type": "web_crawl",
                "source_url": source_urls or f"https://www.google.com/search?q={chip_model}+specifications",
                "source_detail": "Enrichment pipeline via WebSearch/WebFetch",
                "confidence": row.get("_extraction_confidence", "medium"),
                "is_official": "0",
                "notes": row.get("_extraction_notes", ""),
            }

            # Check if chip exists
            existing = db.execute(
                "SELECT id FROM chips WHERE chip_model = ?",
                (chip_model,),
            ).fetchone()

            if existing:
                if dry_run:
                    print(f"  [DRY] UPDATE [{existing['id']}] {chip_model}")
                else:
                    update_chip_fields(db, existing["id"], fields, source)
                    print(f"  UPDATE [{existing['id']}] {chip_model} ({len(fields)} fields)")
                updated += 1
            else:
                if dry_run:
                    print(f"  [DRY] INSERT {chip_model}")
                else:
                    # Ensure identity fields are in data
                    for id_field in ["vendor", "vendor_display", "chip_series", "chip_type", "tier"]:
                        if id_field not in fields and id_field in row:
                            val = (row.get(id_field) or "").strip()
                            if val:
                                fields[id_field] = val
                    chip_id = add_chip(db, fields, source)
                    print(f"  INSERT [{chip_id}] {chip_model} ({len(fields)} fields)")
                inserted += 1

        if not dry_run:
            db.commit()

    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(
        description="Enrich discovered AI chips with detailed hardware specs"
    )
    parser.add_argument(
        "--input", default=str(HERE / "data" / "discovered_chips.csv"),
        help="Path to discovered chips CSV (from discover_chips.py)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output CSV path (default: data/enriched_chips_TIMESTAMP.csv)"
    )
    parser.add_argument("--max-chips", type=int, default=0,
                        help="Limit to N chips (0 = all)")
    parser.add_argument("--import", action="store_true", dest="do_import",
                        help="Import enriched data after generating CSV")
    parser.add_argument("--import-only", default=None,
                        help="Only import from an existing enriched CSV")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, no writes")
    parser.add_argument("--resume", action="store_true",
                        help="Skip chips already in output CSV")
    parser.add_argument("--print-prompts", action="store_true",
                        help="Print per-chip search prompts (for manual/Claude use)")
    args = parser.parse_args()

    # Import-only mode
    if args.import_only:
        result = import_enriched_chips(Path(args.import_only), dry_run=args.dry_run)
        print(f"\n[import] Summary: {result['inserted']} inserted, "
              f"{result['updated']} updated, {result['skipped']} skipped")
        return

    # Enrich mode
    input_path = Path(args.input)
    chips = load_discovered_chips(input_path)

    if args.max_chips > 0:
        chips = chips[:args.max_chips]
        print(f"[enrich] Limited to {args.max_chips} chips")

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = HERE / "data" / f"enriched_chips_{ts}.csv"

    init_output_csv(output_path)

    # Resume: skip already-processed
    if args.resume:
        done = load_existing_enriched(output_path)
        chips = [c for c in chips if c.get("chip_model", c.get("chip_series", "")) not in done]
        print(f"[enrich] Resume mode: {len(chips)} chips remaining")

    if args.print_prompts:
        print(f"\n{'='*60}")
        print(f"Enrichment prompts for {len(chips)} chips:")
        print(f"{'='*60}\n")
        for i, chip in enumerate(chips):
            print(f"--- Chip {i+1}/{len(chips)} ---")
            print(build_enrich_prompt(chip))
            print()
        return

    # Enrich each chip — this is where sub-agents get dispatched
    # For now, this provides the framework; actual WebSearch is done
    # by Claude Code sub-agents reading the prompts and filling the CSV
    print(f"\n[enrich] Ready to process {len(chips)} chips.")
    print(f"  Output: {output_path}")
    print(f"\n  Each chip needs deep web search for hardware specs.")
    print(f"  Use --print-prompts to generate per-chip search prompts.")
    print(f"  After enrichment, use --import-only {output_path} to import.")
    print(f"\n  Template: Fill the output CSV with extracted specs,")
    print(f"  then run: python chip_model/pipeline/enrich_discovered.py --import-only ...")

    # If running in auto mode with Claude Code, the sub-agents would
    # be dispatched here. For standalone mode, we just prepare the output.
    if args.do_import:
        result = import_enriched_chips(output_path, dry_run=args.dry_run)
        print(f"\n[import] Summary: {result['inserted']} inserted, "
              f"{result['updated']} updated, {result['skipped']} skipped")


if __name__ == "__main__":
    main()
