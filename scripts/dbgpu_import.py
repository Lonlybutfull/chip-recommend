#!/usr/bin/env python3
"""Import GPUs from dbgpu's pre-built data.pkl into chips table via CLI.

dbgpu ships with data.pkl (1.5MB, 2,824 GPUs from TechPowerUp) in the package.
This script:
  1. Loads the pickle data
  2. Filters to AI-relevant GPUs (high-memory datacenter GPUs from NVIDIA/AMD/Intel)
  3. Maps 54 dbgpu fields → 78 chips table fields
  4. Outputs enriched CSV for Phase A discovery

Usage:
    python scripts/dbgpu_import.py                    # Generate CSV only
    python scripts/dbgpu_import.py --import           # Also import via CLI (chip add/update)
    python scripts/dbgpu_import.py --limit 20         # Limit to 20 GPUs for testing
    python scripts/dbgpu_import.py --dry-run          # Preview only
"""

import argparse
import csv
import json
import pickle
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from datetime import date, datetime

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from chip_model.database import get_db

# ── dbgpu data.pkl path ──
import dbgpu as _dbgpu_pkg
DBGPU_PKL = Path(_dbgpu_pkg.__file__).parent / "data.pkl"

# ── Default thresholds (overridable via CLI args) ──
_DEFAULT_MIN_VRAM = 4.0
_DEFAULT_MIN_YEAR = 2018
AI_VENDORS = {"NVIDIA", "AMD", "Intel"}

# ── Consumer GPU exclusion patterns ──
# We want datacenter/workstation GPUs, not consumer gaming cards
# NVIDIA datacenter: A100, H100, B100, B200, H200, L40, L40S, RTX (workstation only)
# AMD datacenter: MI series, Instinct
# Intel datacenter: Data Center GPU, Gaudi, Arctic Sound, Flex
CONSUMER_EXCLUDE_PATTERNS = [
    r'^GeForce',       # GeForce RTX 3060, etc. — gaming
    r'^Radeon RX',     # Radeon RX 7900 XT — gaming (not MI or Instinct)
    r'^Radeon PRO(?! W)',  # Radeon PRO W7900D is workstation, keep W-series
    r'^Arc A\d',       # Arc A770 — consumer
    r'^Arc B\d',       # Arc B580 — consumer
    r'^RTX (20|30|40|50)\d',  # RTX 3060, 4070 — consumer (not RTX PRO)
    r'(?i)\bGRE\b',    # Golden Rabbit Edition — consumer
    r'(?i)\bXTX?\b',   # Radeon XT/XTX — consumer
]

# ── Keep patterns (datacenter/enterprise) ──
DATACENTER_KEEP_PATTERNS = [
    r'^NVIDIA (A|B|H|L|T)\d',       # A100, B200, H100, L40, T4
    r'^RTX PRO',                      # RTX PRO 5000/6000 (workstation)
    r'^(Tesla|Quadro)',               # Tesla, Quadro
    r'^(GRID)',                       # GRID
    r'^(Jetson)',                     # Jetson edge AI
    r'(?i)MI\d',                      # AMD Instinct MI300X, MI250X
    r'(?i)Instinct',                  # AMD Instinct
    r'(?i)Gaudi',                     # Intel Gaudi
    r'(?i)Falcon',                    # Intel Falcon Shores
    r'(?i)Data Center GPU',           # Intel Data Center GPU Max
    r'(?i)Arctic Sound',              # Intel Arctic Sound
    r'(?i)Flex \d',                   # Intel Data Center GPU Flex
    r'(?i)Ponte Vecchio',             # Intel Ponte Vecchio
    r'^GB\d',                         # NVIDIA GB10, GB200, GB300 (Grace-Blackwell)
    r'(?i)DGX',                       # NVIDIA DGX
    r'(?i)Radeon PRO W\d',            # AMD Radeon PRO W7900D (workstation w/48GB)
]

# ── Precision format templates ──
def format_precision_support(half_gflops, single_gflops, double_gflops):
    """Build precision_support string from raw GFLOPS values."""
    parts = []
    if double_gflops and double_gflops > 0:
        parts.append("FP64")
    if single_gflops and single_gflops > 0:
        parts.append("FP32")
    if half_gflops and half_gflops > 0:
        # dbgpu 'half_float_performance_gflop_s' can be FP16 or BF16
        parts.append("FP16/BF16")
    if not parts:
        parts.append("FP32")
    # Add INT8
    parts.append("INT8")
    return "/".join(parts)


def format_precision_perf(half_gflops, single_gflops, double_gflops):
    """Build precision_perf string with TFLOPS values."""
    parts = []
    if double_gflops and double_gflops > 0:
        tf = double_gflops / 1000
        parts.append(f"FP64:{tf:.1f}TFLOPS")
    if single_gflops and single_gflops > 0:
        tf = single_gflops / 1000
        parts.append(f"FP32:{tf:.1f}TFLOPS")
    if half_gflops and half_gflops > 0:
        tf = half_gflops / 1000
        parts.append(f"FP16/BF16:{tf:.1f}TFLOPS")
    if not parts:
        return ""
    return ";".join(parts)


def deduce_chip_tier(gpu):
    """Deduce chip tier (datacenter/consumer/workstation) from name + specs."""
    name = gpu.get("name", "")

    # Explicit datacenter names
    if any(re.search(p, name) for p in DATACENTER_KEEP_PATTERNS):
        mem = gpu.get("memory_size_gb") or 0
        if mem and float(mem) >= 16:
            return "datacenter"
        return "workstation"

    # High-memory professional GPUs
    mem = gpu.get("memory_size_gb") or 0
    if mem and float(mem) >= 24:
        return "datacenter"

    return "workstation"


def is_ai_relevant(gpu: dict, min_vram: float = _DEFAULT_MIN_VRAM, min_year: int = _DEFAULT_MIN_YEAR) -> bool:
    """Filter: keep only GPUs relevant to AI workloads."""
    name = gpu.get("name", "")
    vendor = gpu.get("manufacturer", "")
    mem_gb = float(gpu.get("memory_size_gb") or 0)
    release_date = gpu.get("release_date")

    # Must be from relevant vendor
    if vendor not in AI_VENDORS:
        return False

    # Must have meaningful VRAM
    if mem_gb < min_vram:
        return False

    # Must be recent enough
    if release_date and hasattr(release_date, 'year'):
        if release_date.year < min_year:
            return False

    # Exclude consumer gaming GPUs
    for pat in CONSUMER_EXCLUDE_PATTERNS:
        if re.search(pat, name):
            return False

    return True


def map_dbgpu_to_chip(gpu: dict) -> dict:
    """Map one dbgpu GPU dict → chips table fields dict."""
    def _f(v):
        """Format a value: None → "", float → string, bool → string."""
        if v is None:
            return ""
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return f"{v:.2f}".rstrip('0').rstrip('.')
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, bool):
            return "1" if v else "0"
        return str(v)

    mem_gb = float(gpu.get("memory_size_gb") or 0)
    mem_type = gpu.get("memory_type") or ""

    # Fix: dbgpu sometimes classifies HBM as DRAM for old GPUs
    if mem_gb >= 40 and "DRAM" in str(mem_type):
        mem_type = ""

    chip = {
        # Identity
        "vendor": gpu.get("manufacturer", ""),
        "vendor_display": gpu.get("manufacturer", ""),
        "vendor_region": "foreign",
        "chip_series": gpu.get("gpu_name", gpu.get("name", "")),
        "chip_model": gpu.get("name", ""),
        "chip_type": "GPU",
        "usage": "both",  # Most datacenter GPUs support both
        "tier": deduce_chip_tier(gpu),

        # Architecture
        "architecture": gpu.get("architecture") or "",
        "arch_codename": gpu.get("gpu_name") or "",
        "generation": gpu.get("generation") or "",
        "process_node_nm": _f(gpu.get("process_size_nm")),
        "foundry": gpu.get("foundry") or "",
        "die_size_mm2": _f(gpu.get("die_size_mm2")),
        "transistors_b": _f((gpu.get("transistor_count_m") or 0) / 1000) if gpu.get("transistor_count_m") else "",
        "package_type": gpu.get("chip_package") or "",

        # Memory
        "vram_gb": _f(gpu.get("memory_size_gb")),
        "vram_type": mem_type,
        "vram_bus_bit": _f(gpu.get("memory_bus_bits")),
        "vram_bw_gb_s": _f(gpu.get("memory_bandwidth_gb_s")),
        "vram_clock_mhz": _f(gpu.get("memory_clock_mhz")),

        # Compute units
        "compute_units": _f(gpu.get("shading_units")),
        "tensor_cores": _f(gpu.get("tensor_cores")),
        "rt_cores": _f(gpu.get("ray_tracing_cores")),
        "sm_count": _f(gpu.get("streaming_multiprocessors")),
        "shading_units": _f(gpu.get("shading_units")),

        # Cache
        "l1_cache_kb": _f(gpu.get("l1_cache_kb")),
        "l2_cache_mb": _f(gpu.get("l2_cache_mb")),

        # Precision — derive from GFLOPS
        "precision_support": format_precision_support(
            gpu.get("half_float_performance_gflop_s"),
            gpu.get("single_float_performance_gflop_s"),
            gpu.get("double_float_performance_gflop_s"),
        ),
        "precision_perf": format_precision_perf(
            gpu.get("half_float_performance_gflop_s"),
            gpu.get("single_float_performance_gflop_s"),
            gpu.get("double_float_performance_gflop_s"),
        ),

        # Clock
        "base_clock_mhz": _f(gpu.get("base_clock_mhz")),
        "boost_clock_mhz": _f(gpu.get("boost_clock_mhz")),

        # Power / physical
        "tdp_w": _f(gpu.get("thermal_design_power_w")),
        "psu_w": _f(gpu.get("suggested_psu_w")),
        "power_connector": gpu.get("power_connectors") or "",
        "board_length_mm": _f(gpu.get("board_length_mm")),
        "board_width_mm": _f(gpu.get("board_width_mm")),
        "slot_width": gpu.get("board_slot_width") or "",
        "bus_interface": gpu.get("bus_interface") or "",

        # Lifecycle
        "release_date": gpu.get("release_date").isoformat() if gpu.get("release_date") else "",
        "production_status": "已量产",
        "is_released": "1",
        "target_market": "AI训练/推理 / HPC",
        "description": f"{gpu.get('manufacturer', '')} {gpu.get('name', '')} — {gpu.get('architecture', '')} architecture, {gpu.get('generation', '')} generation, {gpu.get('memory_size_gb', '')}GB {gpu.get('memory_type', '')}",

        # Source metadata (appended as extra columns, not chips fields)
        "_source_urls": gpu.get("tpu_url", ""),
        "_source_type": "community",  # TechPowerUp is community source
        "_source_confidence": "high",
    }

    return chip


def load_dbgpu_gpus() -> list[dict]:
    """Load all GPUs from dbgpu data.pkl."""
    if not DBGPU_PKL.exists():
        print(f"[ERROR] dbgpu data.pkl not found at {DBGPU_PKL}")
        sys.exit(1)

    with open(DBGPU_PKL, "rb") as f:
        raw = pickle.load(f)

    return raw  # list of dicts, 54 keys each


def filter_ai_gpus(all_gpus: list[dict], min_vram: float = _DEFAULT_MIN_VRAM, min_year: int = _DEFAULT_MIN_YEAR) -> list[dict]:
    """Filter to AI-relevant GPUs and deduplicate."""
    filtered = []
    seen = set()

    for gpu in all_gpus:
        if not is_ai_relevant(gpu, min_vram=min_vram, min_year=min_year):
            continue

        name = gpu.get("name", "")
        vendor = gpu.get("manufacturer", "")
        key = f"{vendor}::{name}"
        if key in seen:
            continue
        seen.add(key)
        filtered.append(gpu)

    return filtered


def check_existing_in_db() -> dict[str, int]:
    """Get mapping of chip_model → id for existing chips in DB."""
    try:
        with get_db(readonly=True) as db:
            rows = db.execute(
                "SELECT id, chip_model FROM chips WHERE chip_type != 'CPU' AND chip_type != 'NOT_A_CHIP' AND chip_type != 'Server'"
            ).fetchall()
        return {r["chip_model"]: r["id"] for r in rows}
    except Exception:
        return {}


def generate_csv(gpus: list[dict], output_path: Path, dry_run: bool = False) -> list[dict]:
    """Map dbgpu GPUs to chip fields and write to CSV."""
    existing_chips = check_existing_in_db()

    rows = []
    new_count = 0
    existing_count = 0

    for gpu in gpus:
        chip = map_dbgpu_to_chip(gpu)
        chip_model = chip["chip_model"]

        # Check if already in DB
        if chip_model in existing_chips:
            chip["_db_id"] = str(existing_chips[chip_model])
            chip["_is_new"] = "0"
            existing_count += 1
        else:
            chip["_db_id"] = ""
            chip["_is_new"] = "1"
            new_count += 1

        rows.append(chip)

    print(f"  New to DB: {new_count}")
    print(f"  Existing in DB: {existing_count}")
    print(f"  Total: {len(rows)}")

    # Write CSV
    if not dry_run:
        # Determine all columns from first row
        fieldnames = list(rows[0].keys()) if rows else []

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        print(f"  Output: {output_path}")

    return rows


def import_via_cli(rows: list[dict], dry_run: bool = False, limit: int = 0) -> dict:
    """Import chips via parse1 CLI (chip add / chip update)."""
    cli_py = HERE / "scripts" / "run_cli.py"

    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    to_process = rows[:limit] if limit > 0 else rows

    for i, chip in enumerate(to_process):
        name = chip["chip_model"]
        is_new = chip.get("_is_new") == "1"

        # Build fields dict — only include non-metadata, non-empty fields
        fields = {}
        for k, v in chip.items():
            if k.startswith("_"):
                continue
            if v and str(v).strip():
                fields[k] = str(v).strip()

        # Build source
        source = {
            "source_type": chip.get("_source_type", "community"),
            "source_url": chip.get("_source_urls", f"https://www.techpowerup.com/gpu-specs/"),
            "source_detail": f"dbgpu import — TechPowerUp GPU database (built-in data.pkl)",
            "confidence": chip.get("_source_confidence", "high"),
            "is_official": False,
            "notes": f"Auto-imported from dbgpu data.pkl. {len(fields)} fields mapped.",
        }

        if is_new:
            cmd = ["chip", "add", "-d", json.dumps(fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]
        else:
            db_id = chip.get("_db_id", "")
            if not db_id:
                skipped += 1
                continue
            cmd = ["chip", "update", "--id", db_id,
                   "-d", json.dumps(fields, ensure_ascii=False),
                   "-s", json.dumps(source, ensure_ascii=False)]

        if dry_run:
            action = "INSERT" if is_new else f"UPDATE [{chip.get('_db_id', '?')}]"
            print(f"  [DRY] {action} {name} ({len(fields)} fields)")
            if is_new:
                inserted += 1
            else:
                updated += 1
            continue

        result = subprocess.run(
            [sys.executable, str(cli_py)] + cmd,
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(HERE),
        )

        if result.returncode == 0:
            if is_new:
                inserted += 1
                action = "[INSERT]"
            else:
                updated += 1
                action = "[UPDATE]"
            print(f"  {action} {name} ({len(fields)} fields)")
        elif "UNIQUE constraint" in (result.stderr or ""):
            print(f"  [SKIP] {name} — already exists")
            skipped += 1
        else:
            print(f"  [ERROR] {name}: {result.stderr[:120] if result.stderr else 'Unknown'}")
            errors += 1

        if i % 20 == 19:
            print(f"  ... progress: {i+1}/{len(to_process)}")

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(
        description="Import GPUs from dbgpu data.pkl into chips CSV / DB"
    )
    parser.add_argument("--output", default=str(HERE / "data" / "dbgpu_chips.csv"),
                        help="Output CSV path")
    parser.add_argument("--import", action="store_true", dest="do_import",
                        help="Import into DB via CLI")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to N GPUs (0 = all)")
    parser.add_argument("--min-vram", type=float, default=_DEFAULT_MIN_VRAM,
                        help=f"Minimum VRAM GB (default: {_DEFAULT_MIN_VRAM})")
    parser.add_argument("--min-year", type=int, default=_DEFAULT_MIN_YEAR,
                        help=f"Minimum release year (default: {_DEFAULT_MIN_YEAR})")
    args = parser.parse_args()

    output_path = Path(args.output)
    min_vram = args.min_vram
    min_year = args.min_year

    print("=" * 60)
    print("dbgpu → chips import")
    print(f"  data.pkl: {DBGPU_PKL}")
    print(f"  Filters: VRAM ≥ {min_vram}GB, year ≥ {min_year}")
    print("=" * 60)

    # Step 1: Load
    print("\n[1/4] Loading dbgpu data.pkl...")
    all_gpus = load_dbgpu_gpus()
    print(f"  Total GPUs in data.pkl: {len(all_gpus)}")

    # Step 2: Filter
    print(f"\n[2/4] Filtering for AI-relevant GPUs ({', '.join(sorted(AI_VENDORS))})...")
    filtered = filter_ai_gpus(all_gpus, min_vram=min_vram, min_year=min_year)
    if args.limit > 0:
        filtered = filtered[:args.limit]
    print(f"  After filter: {len(filtered)} GPUs")

    # Show breakdown
    from collections import Counter
    by_vendor = Counter(g["manufacturer"] for g in filtered)
    for v, c in by_vendor.most_common():
        print(f"    {v}: {c}")

    # Step 3: Map + CSV
    print(f"\n[3/4] Mapping to chips schema → CSV...")
    rows = generate_csv(filtered, output_path, dry_run=args.dry_run)

    # Step 4: Import (optional)
    if args.do_import:
        print(f"\n[4/4] Importing via CLI...")
        result = import_via_cli(rows, dry_run=args.dry_run, limit=args.limit)
        print(f"\n  Summary: {result['inserted']} inserted, {result['updated']} updated, "
              f"{result['skipped']} skipped, {result['errors']} errors")
    else:
        print(f"\n[4/4] Skipped — use --import to import into DB")

    print("\nDone.")


if __name__ == "__main__":
    main()
