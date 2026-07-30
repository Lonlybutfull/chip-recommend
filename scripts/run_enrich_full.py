#!/usr/bin/env python3
"""Phase B: Enrichment Pipeline — dispatch subagent clusters for deep web search.

Reads data/discovered_chips_v2.csv, groups chips by vendor, and generates
per-vendor enrichment prompts for Claude Code subagents.

Each subagent receives:
  - A list of chip names for one vendor
  - Known source URLs from discovery
  - Instructions to WebSearch + WebFetch for detailed hardware specs

Output: data/enriched_chips_v2.csv (78 chip fields + source metadata)

Usage:
    python scripts/run_enrich_full.py                     # Generate enrichment prompts
    python scripts/run_enrich_full.py --vendor NVIDIA     # Single vendor
    python scripts/run_enrich_full.py --print-prompts     # Print prompts for manual use
    python scripts/run_enrich_full.py --max-chips 20      # Limit total chips
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

NOW = datetime.now().isoformat(timespec="seconds")

# ── All 78 chip columns (schema.sql) ──
CHIP_FIELDS = [
    # Identity
    "vendor", "vendor_display", "vendor_region", "chip_series", "chip_model",
    "chip_type", "usage", "tier",
    # Architecture
    "architecture", "arch_codename", "generation", "process_node_nm",
    "foundry", "die_size_mm2", "transistors_b", "package_type", "is_chiplet",
    # Memory
    "vram_gb", "vram_type", "vram_bus_bit", "vram_bw_gb_s", "vram_clock_mhz",
    # Compute units
    "compute_units", "tensor_cores", "rt_cores", "shading_units", "sm_count",
    # Cache
    "l1_cache_kb", "l2_cache_mb", "on_chip_sram_mb",
    # Precision
    "precision_support", "precision_perf",
    # Clock / Power / Physical
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

# Extra metadata columns
ENRICH_META_COLS = [
    "_source_urls", "_source_type", "_extraction_confidence", "_extraction_notes",
    "_is_new", "_db_id",
]

OUTPUT_COLUMNS = CHIP_FIELDS + ENRICH_META_COLS


def load_discovered_chips(csv_path: Path) -> list[dict]:
    """Load Phase A discovery CSV."""
    if not csv_path.exists():
        print(f"[ERROR] Not found: {csv_path}")
        sys.exit(1)
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def group_by_vendor(chips: list[dict]) -> dict[str, list[dict]]:
    """Group chips by vendor for parallel enrichment."""
    groups: dict[str, list[dict]] = {}
    for c in chips:
        vendor = c.get("vendor_display", c.get("vendor", "")) or "Unknown"
        groups.setdefault(vendor, []).append(c)
    return groups


def build_subagent_prompt(vendor: str, chips: list[dict]) -> str:
    """Generate the enrichment prompt for one vendor's chips."""
    chip_names = [c["chip_model"] for c in chips if c.get("chip_model")]
    source_urls = []
    for c in chips:
        urls = (c.get("source_urls") or "").split("|")
        source_urls.extend(u.strip() for u in urls if u.strip())

    # Limit to first 30 chip names + unique URLs
    chip_list = "\n".join(f"  - {n}" for n in chip_names[:50])
    url_list = "\n".join(f"  - {u}" for u in list(dict.fromkeys(source_urls))[:30])

    prompt = f"""Deep search and collect detailed hardware specifications for {vendor} AI accelerator chips.

Chips to research ({len(chip_names)} total, showing first 50):
{chip_list}

{len(source_urls)} known source URLs from discovery:
{url_list}

INSTRUCTIONS:
1. For each chip, use WebSearch to find detailed specifications
2. Start with official vendor datasheets, product pages, and whitepapers
3. Then check TechPowerUp, AnandTech, Tom's Hardware, ServeTheHome, WikiChip
4. For Chinese chips, also check 半导体行业观察, 知乎专栏, cnBeta, CSDN

Priority fields (in order):
  - vram_gb, vram_type (HBM2e/HBM3/HBM3e/GDDR6X), vram_bw_gb_s
  - tdp_w, process_node_nm, architecture, arch_codename
  - precision_support (list: FP64/FP32/TF32/FP16/BF16/FP8/INT8/INT4/FP4)
  - precision_perf (TFLOPS/TOPS per precision: "FP16:312TFLOPS;FP32:156TFLOPS;FP64:78TFLOPS")
  - interconnect_bw_gb_s, interconnect_tech (NVLink/PCIe/Infinity Fabric)
  - compute_units, tensor_cores, sm_count
  - release_date, production_status (量产/已发布/未公开发布/待发布)
  - price_usd, price_cny_wan
  - die_size_mm2, transistors_b, foundry
  - form_factor (SXM/OAM/PCIe/Passive/主动散热)
  - On failure, leave the field empty rather than guessing.

OUTPUT FORMAT:
Return a JSON object mapping each chip_model to its filled fields. Example:

{{
  "H100 SXM5 80GB": {{
    "vram_gb": "80",
    "vram_type": "HBM3",
    "vram_bw_gb_s": "3350",
    "tdp_w": "700",
    "process_node_nm": "4",
    "architecture": "Hopper",
    "precision_perf": "FP16:1979TFLOPS;FP32:989TFLOPS;FP64:494TFLOPS",
    "_source_urls": "https://www.nvidia.com/en-us/data-center/h100/|https://www.techpowerup.com/gpu-specs/h100-pcie-80-gb.c3899",
    "_source_type": "official_datasheet",
    "_extraction_confidence": "high"
  }},
  ...
}}

IMPORTANT:
- Only include fields where you found ACTUAL data from a source. Do not invent values.
- _source_urls must contain ALL URLs you used (pipe-separated).
- _source_type: "official_datasheet" for vendor pages, "community" for review sites.
- _extraction_confidence: "high" for vendor-confirmed values, "medium" for community-confirmed, "low" for estimates.
"""
    return prompt


def init_output_csv(output_path: Path) -> None:
    """Create output CSV with headers."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()


def append_enriched_rows(output_path: Path, rows: list[dict]) -> None:
    """Append enriched chip rows to output CSV."""
    with open(output_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Phase B: Chip detail enrichment via subagent dispatch"
    )
    parser.add_argument("--input", default=str(HERE / "data" / "discovered_chips_v2.csv"),
                        help="Discovery CSV from Phase A")
    parser.add_argument("--output", default=str(HERE / "data" / "enriched_chips_v2.csv"),
                        help="Output enriched CSV")
    parser.add_argument("--vendor", default=None,
                        help="Only process one vendor")
    parser.add_argument("--max-chips", type=int, default=0,
                        help="Limit total chips (0=all)")
    parser.add_argument("--print-prompts", action="store_true",
                        help="Print per-vendor prompts for manual use")
    parser.add_argument("--new-only", action="store_true",
                        help="Only enrich new chips (is_new_to_db=1)")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    chips = load_discovered_chips(input_path)
    print(f"[enrich] Loaded {len(chips)} chips from {input_path}")

    # Filter
    if args.new_only:
        chips = [c for c in chips if c.get("is_new_to_db") == "1"]
        print(f"[enrich] New-only mode: {len(chips)} chips")

    if args.max_chips > 0:
        chips = chips[:args.max_chips]
        print(f"[enrich] Limited to {args.max_chips} chips")

    # Group by vendor
    groups = group_by_vendor(chips)
    print(f"[enrich] {len(groups)} vendor groups")

    if args.vendor:
        groups = {args.vendor: groups.get(args.vendor, [])}
        if not groups[args.vendor]:
            print(f"[ERROR] Vendor '{args.vendor}' not found")
            sys.exit(1)

    # Sort by group size (biggest first)
    sorted_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    init_output_csv(output_path)

    if args.print_prompts:
        for vendor, vendor_chips in sorted_groups:
            print(f"\n{'='*70}")
            print(f"=== {vendor} ({len(vendor_chips)} chips) ===")
            print(f"{'='*70}")
            print(build_subagent_prompt(vendor, vendor_chips))
            print()
        return

    # Print summary for Claude to dispatch subagents
    print(f"\n{'='*60}")
    print(f"Ready for enrichment: {len(sorted_groups)} vendor groups, {len(chips)} chips")
    print(f"Output CSV: {output_path}")
    print(f"{'='*60}")

    for vendor, vendor_chips in sorted_groups:
        new_count = sum(1 for c in vendor_chips if c.get("is_new_to_db") == "1")
        exist_count = sum(1 for c in vendor_chips if c.get("is_new_to_db") == "0")
        print(f"\n  {vendor}: {len(vendor_chips)} chips (new={new_count}, existing={exist_count})")
        # Print first few chips as preview
        for c in vendor_chips[:5]:
            sources = (c.get("source_urls") or "")[:80]
            print(f"    - {c['chip_model']} [{c.get('discovery_method', '?')}]")

    print(f"\n{'-'*60}")
    print(f"Per-vendor enrichment prompts are available.")
    print(f"Use --print-prompts to generate them for manual dispatch,")
    print(f"or --vendor <name> to focus on one vendor at a time.")
    print(f"{'-'*60}")


if __name__ == "__main__":
    main()
