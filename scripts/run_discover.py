#!/usr/bin/env python3
"""Phase A: Chip Discovery Pipeline — merge all sources into one CSV.

Sources:
  1. dbgpu data.pkl — 287 AI-relevant foreign GPUs (via dbgpu_import.py)
  2. Existing chips DB — 现有芯片（过滤 Server/CPU/NOT_A_CHIP）
  3. Series search — 同系列下一代码产品（WebSearch）
  4. link_library crawl — 从链接库描述中提取芯片名

Output: data/discovered_chips_v2.csv
  Columns: chip_series, chip_model, vendor, vendor_display, vendor_region,
           chip_type, tier, discovery_method, source_urls, is_new_to_db, notes

The CSV is used by Phase B (run_enrich_full.py) for detail enrichment,
then by run_ingest.py for CLI-based DB import.

Usage:
    python scripts/run_discover.py                      # Full pipeline
    python scripts/run_discover.py --skip-dbgpu          # Skip dbgpu source
    python scripts/run_discover.py --skip-series-search  # Skip series search
    python scripts/run_discover.py --output custom.csv
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from chip_model.database import get_db, get_db_path

NOW = datetime.now().isoformat(timespec="seconds")

# ── Exclusion patterns for non-AI-accelerator products ──
EXCLUDE_PATTERNS = [
    r'(?i)\b(CPU|至强|Xeon|EPYC|霄龍|Epyc|Pentium|Core\s*i\d)\b',
    r'(?i)\b(服务器|机柜|集群|Pod|SuperPOD|CloudMatrix|REX|Atlas\s*800|Atlas\s*900)\b',
    r'(?i)\b(边缘|Edge|车载|自动驾|ADAS|座舱|Cockpit|MCU|SoC|嵌入式)\b',
    r'(?i)\b(FPGA|Versal|Zynq|Alveo)\b',
    r'(?i)\b(液冷|散热|电源|机箱|Chassis|L40S|RTX\s*40|RTX\s*50|GeForce)\b',
    r'(?i)\b(NOT_A_CHIP|Server|CPU\b)',
]

# ── Known chip_type keywords ──
VALID_CHIP_TYPES = {"GPU", "NPU", "TPU", "DCU", "MLU", "PPU", "ASIC", "IPU", "LPU", "RDU", "RPU", "GCU"}

# ── Non-chip words that match chip patterns but are not chips ──
NON_CHIP_WORDS = {
    "HBM2", "HBM2e", "HBM3", "HBM3e", "HBM4",
    "GDDR6", "GDDR6X", "GDDR7",
    "GTC 2025", "GTC 2024", "GTC 2026",
    "Transformer", "DeepSeek", "Llama", "GPT-4", "GPT-5",
    "NVLink", "PCIe", "Infinity Fabric",
    "SXM5", "SXM4", "OAM",
}

# ── Same-series next-gen hints for series search ──
SERIES_NEXT_GEN = {
    # NVIDIA
    "A100": ["A800", "A100X", "A30", "A40"],
    "H100": ["H200", "H800", "H100 NVL", "H200 NVL"],
    "B200": ["B100", "B200 Ultra", "B300", "GB200", "GB300", "GB10"],
    "B300": ["GB300", "GB200", "B200 Ultra"],
    "H200": ["H200 NVL", "H800"],
    "GB200": ["GB300", "GB10"],
    # AMD
    "MI300X": ["MI300A", "MI325X", "MI350X", "MI355X", "MI400X", "MI250X"],
    "MI350X": ["MI355X", "MI400X", "MI450X"],
    "MI250X": ["MI300X", "MI300A"],
    # Intel
    "Gaudi 3": ["Gaudi 4", "Falcon Shores"],
    "Falcon Shores": ["Gaudi 4", "Jaguar Shores"],
    "Data Center GPU Max": ["Falcon Shores", "Gaudi 4"],
    # Google
    "Ironwood": ["Trillium", "TPU v6e", "TPU v7", "TPU v5p", "TPU v5e"],
    "Trillium": ["Ironwood", "TPU v6e"],
    # Huawei
    "昇腾910B": ["昇腾910C", "昇腾950P", "昇腾950PR", "昇腾910A", "昇腾920"],
    "昇腾910C": ["昇腾950P", "昇腾950PR", "昇腾920", "昇腾930"],
    "昇腾950PR": ["昇腾920", "昇腾930"],
    # Cambricon
    "MLU370": ["MLU590", "MLU690", "MLU270", "MLU220"],
    "MLU590": ["MLU690", "MLU790"],
    "MLU270": ["MLU370", "MLU220"],
    # Biren
    "BR100": ["BR104", "BR108", "BR200"],
    "BR104": ["BR108", "BR200"],
    # MetaX
    "C500": ["C550", "C600", "C700"],
    "C600": ["C700", "C800"],
    # Hygon
    "K100": ["K200", "K300", "深算二号", "深算三号"],
    # Iluvatar
    "天垓100": ["天垓200", "智铠100"],
    # Kunlunxin
    "P800": ["P800s", "P900", "P1000"],
    # MooreThreads
    "S4000": ["S5000", "S6000"],
    # AWS
    "Trainium2": ["Trainium3", "Inferentia3", "Inferentia2"],
    # Microsoft
    "Maia 200": ["Maia 300", "Maia 400"],
    # Enflame
    "T20": ["T30", "L600"],
    # Cerebras
    "WSE-3": ["WSE-4"],
    # Groq
    "GroqCard": ["GroqCard 2"],
    # SambaNova
    "SN40L": ["SN50"],
    # Graphcore
    "Colossus MK2": ["Colossus MK3"],
}


def _norm_name(name: str) -> str:
    """Normalize chip name for dedup."""
    name = re.sub(r'\s+', ' ', name.strip())
    # Remove trailing parenthetical like "(24GB)" or "(壁砺100)"
    # but keep the core name
    return name


def _dedup_key(chip: dict) -> str:
    """Unique key for dedup: vendor + normalized chip_model."""
    vendor = (chip.get("vendor", "") or "").lower().strip()
    model = _norm_name(chip.get("chip_model", ""))
    return f"{vendor}::{model}"


def load_dbgpu_csv() -> list[dict]:
    """Load chips from dbgpu_import.py output."""
    csv_path = HERE / "data" / "dbgpu_chips.csv"
    if not csv_path.exists():
        print(f"  [WARN] dbgpu CSV not found at {csv_path}")
        print(f"  Run: python scripts/dbgpu_import.py --min-vram 4 --min-year 2018")
        return []

    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = (r.get("chip_model") or "").strip()
            if not name:
                continue
            rows.append({
                "chip_series": r.get("chip_series", name),
                "chip_model": name,
                "vendor": r.get("vendor", ""),
                "vendor_display": r.get("vendor_display", r.get("vendor", "")),
                "vendor_region": r.get("vendor_region", "foreign"),
                "chip_type": r.get("chip_type", "GPU"),
                "tier": r.get("tier", "datacenter"),
                "discovery_method": "dbgpu",
                "source_urls": r.get("_source_urls", ""),
                "is_new_to_db": r.get("_is_new", "1"),
                "notes": f"dbgpu import: {r.get('architecture', '')} {r.get('vram_gb', '')}GB",
            })
    print(f"  dbgpu source: {len(rows)} chips")
    return rows


def load_existing_chips() -> list[dict]:
    """Load existing chips from DB, filtering out non-accelerators."""
    rows = []
    exclude_types = {"NOT_A_CHIP", "Server", "CPU"}
    try:
        with get_db(readonly=True) as db:
            results = db.execute(
                "SELECT id, vendor, vendor_display, vendor_region, chip_series, "
                "chip_model, chip_type, tier "
                "FROM chips ORDER BY vendor, chip_model"
            ).fetchall()
            for r in results:
                chip_type = r["chip_type"] or ""
                if chip_type in exclude_types:
                    continue
                chip_model = r["chip_model"] or ""
                if not chip_model:
                    continue
                # Apply exclusion patterns
                skip = False
                for pat in EXCLUDE_PATTERNS:
                    if re.search(pat, chip_model):
                        skip = True
                        break
                if skip:
                    continue

                rows.append({
                    "chip_series": r["chip_series"] or chip_model,
                    "chip_model": chip_model,
                    "vendor": r["vendor"] or "",
                    "vendor_display": r["vendor_display"] or r["vendor"] or "",
                    "vendor_region": r["vendor_region"] or "",
                    "chip_type": chip_type if chip_type in VALID_CHIP_TYPES else "GPU",
                    "tier": r["tier"] or "datacenter",
                    "discovery_method": "existing_db",
                    "source_urls": "",
                    "is_new_to_db": "0",
                    "notes": f"Existing in DB (id={r['id']})",
                })
    except Exception as e:
        print(f"  [WARN] Could not read existing chips: {e}")

    print(f"  Existing DB chips: {len(rows)} (filtered non-accelerators)")
    return rows


def load_link_library_chips() -> list[dict]:
    """Extract chip names from link_library descriptions."""
    rows = []
    chip_name_re = re.compile(
        r'(?:^|\s)'
        r'('
        r'[A-Z][A-Z0-9]+[-\s]?\d+[\w\-]*'        # MLU370, BR100, H100, MI300X
        r'|'
        r'[A-Z][a-z]+[\s-]?\d+[\w]*'               # Gaudi 3, Falcon Shores
        r'|'
        r'(?:昇腾|昆仑|天垓|邃思|壁砺|深算|思元|镇岳)[\w\d\-]*'  # Chinese names
        r'|'
        r'C\d{3,4}'                                 # C500, C600
        r'|'
        r'[A-Z]{2,}\d{2,}[A-Z]?'                    # MLU590, BR104, JM9200
        r')'
        r'(?:\s*\([^)]*\))?'                       # optional (24GB), (壁砺100)
    )

    try:
        with get_db(readonly=True) as db:
            results = db.execute(
                "SELECT url, description, vendor, category FROM link_library "
                "WHERE category LIKE '%芯片%' "
                "ORDER BY id"
            ).fetchall()

            seen = set()
            for r in results:
                desc = r["description"] or ""
                url = r["url"] or ""
                vendor = r["vendor"] or ""

                # Try extracting chip names from description
                # Split on " | " first
                for part in desc.split("|"):
                    part = part.strip()
                    # Split on " - " to separate vendor/context from model
                    subparts = part.split(" - ")
                    for sp in subparts:
                        sp = sp.strip()
                        if not sp or len(sp) < 3:
                            continue
                        # Skip obvious non-chip lines
                        if any(w in sp for w in ["价格", "训练", "推理", "模型", "系统", "集群"]):
                            continue

                    # Try the regex on the full part
                    match = chip_name_re.search(part)
                    if match:
                        name = match.group(0).strip()
                        if len(name) < 3:
                            continue
                        # Filter non-chip words
                        if name in NON_CHIP_WORDS:
                            continue
                        # Apply exclusion
                        skip = False
                        for pat in EXCLUDE_PATTERNS:
                            if re.search(pat, name):
                                skip = True
                                break
                        if skip:
                            continue

                        key = f"{vendor}::{_norm_name(name)}"
                        if key in seen:
                            continue
                        seen.add(key)

                        rows.append({
                            "chip_series": name,
                            "chip_model": name,
                            "vendor": vendor,
                            "vendor_display": vendor,
                            "vendor_region": "",
                            "chip_type": "GPU",
                            "tier": "datacenter",
                            "discovery_method": "link_library",
                            "source_urls": url,
                            "is_new_to_db": "1",
                            "notes": f"Extracted from: {desc[:100]}",
                        })
    except Exception as e:
        print(f"  [WARN] link_library extraction failed: {e}")

    print(f"  link_library extracted: {len(rows)} chip names")
    return rows


def generate_series_search(known_chips: list[dict]) -> list[dict]:
    """For known chip series, generate next-gen search targets."""
    # Build series → vendor/chip_type lookup
    series_info: dict[str, list[dict]] = {}
    for c in known_chips:
        series = (c.get("chip_series") or "").strip()
        if not series:
            continue
        if series not in series_info:
            series_info[series] = []
        series_info[series].append(c)

    results = []
    seen_targets = set(c.get("chip_model", c.get("chip_series", "")) for c in known_chips)

    for series, variants in series_info.items():
        vendor = variants[0].get("vendor", "")
        vendor_dsp = variants[0].get("vendor_display", "")
        chip_type = variants[0].get("chip_type", "GPU")
        tier = variants[0].get("tier", "datacenter")

        # Check known next-gen hints
        hints = SERIES_NEXT_GEN.get(series, [])
        for hint_name in hints:
            if hint_name not in seen_targets:
                seen_targets.add(hint_name)
                results.append({
                    "chip_series": hint_name,
                    "chip_model": hint_name,
                    "vendor": vendor,
                    "vendor_display": vendor_dsp,
                    "vendor_region": variants[0].get("vendor_region", ""),
                    "chip_type": chip_type,
                    "tier": tier,
                    "discovery_method": "series_search",
                    "source_urls": f"https://www.google.com/search?q={vendor}+{hint_name}+specifications",
                    "is_new_to_db": "1",
                    "notes": f"Next-gen candidate from {series} series",
                })

    print(f"  Series search targets: {len(results)}")
    return results


def deduplicate(all_chips: list[dict]) -> list[dict]:
    """Deduplicate by vendor+chip_model, merging sources."""
    merged: dict[str, dict] = {}

    for c in all_chips:
        key = _dedup_key(c)
        if not key or key == "::":
            continue

        if key in merged:
            # Merge source URLs
            existing_urls = set((merged[key].get("source_urls") or "").split("|"))
            new_urls = set((c.get("source_urls") or "").split("|"))
            merged_urls = "|".join(u for u in (existing_urls | new_urls) if u.strip())

            merged[key]["source_urls"] = merged_urls
            # Keep the more informative notes
            if c.get("notes") and len(c.get("notes", "")) > len(merged[key].get("notes", "")):
                merged[key]["notes"] = c["notes"]
            # Preserve "existing" over "new" for is_new
            if merged[key].get("is_new_to_db") == "1" and c.get("is_new_to_db") == "0":
                merged[key]["is_new_to_db"] = "0"
        else:
            merged[key] = dict(c)
            merged[key].setdefault("source_urls", "")
            merged[key].setdefault("notes", "")

    result = list(merged.values())
    result.sort(key=lambda x: (x.get("vendor_display", ""), x.get("chip_model", "")))
    return result


def mark_new_vs_existing(chips: list[dict]) -> list[dict]:
    """Cross-check against current DB to mark is_new_to_db."""
    try:
        with get_db(readonly=True) as db:
            existing = set(
                r["chip_model"] for r in
                db.execute("SELECT chip_model FROM chips").fetchall()
            )
    except Exception:
        existing = set()

    for c in chips:
        if c.get("chip_model") in existing:
            c["is_new_to_db"] = "0"
        else:
            c["is_new_to_db"] = "1"

    return chips


def output_csv(chips: list[dict], output_path: Path) -> None:
    """Write discovered chips CSV."""
    fieldnames = [
        "chip_series", "chip_model", "vendor", "vendor_display", "vendor_region",
        "chip_type", "tier", "discovery_method", "source_urls", "is_new_to_db", "notes",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(chips)

    new_count = sum(1 for c in chips if c.get("is_new_to_db") == "1")
    existing_count = sum(1 for c in chips if c.get("is_new_to_db") == "0")

    print(f"\n{'='*60}")
    print(f"[run_discover] Output: {output_path}")
    print(f"  Total chips: {len(chips)}")
    print(f"  New to DB:   {new_count}")
    print(f"  Existing:    {existing_count}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Phase A: Multi-source chip discovery → CSV"
    )
    parser.add_argument("--output", default=str(HERE / "data" / "discovered_chips_v2.csv"),
                        help="Output CSV path")
    parser.add_argument("--skip-dbgpu", action="store_true",
                        help="Skip dbgpu source")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip loading existing chips from DB")
    parser.add_argument("--skip-links", action="store_true",
                        help="Skip link_library extraction")
    parser.add_argument("--skip-series-search", action="store_true",
                        help="Skip series search")
    args = parser.parse_args()

    output_path = Path(args.output)
    all_chips: list[dict] = []

    print("Phase A: Chip Discovery Pipeline\n")

    # Source 1: dbgpu (largest foreign GPU source)
    if not args.skip_dbgpu:
        print("[1/4] Loading dbgpu GPUs...")
        all_chips.extend(load_dbgpu_csv())

    # Source 2: Existing DB chips
    if not args.skip_existing:
        print("[2/4] Loading existing DB chips...")
        all_chips.extend(load_existing_chips())

    # Source 3: Link library extraction
    if not args.skip_links:
        print("[3/4] Extracting chip names from link_library...")
        all_chips.extend(load_link_library_chips())

    # Source 4: Series search
    if not args.skip_series_search:
        print("[4/4] Generating series search targets...")
        all_chips.extend(generate_series_search(all_chips))

    # Deduplicate
    print(f"\n[Dedup] Before: {len(all_chips)} entries")
    deduped = deduplicate(all_chips)
    print(f"[Dedup] After: {len(deduped)} unique chips")

    # Mark new vs existing
    deduped = mark_new_vs_existing(deduped)

    # Output
    output_csv(deduped, output_path)

    return len(deduped)


if __name__ == "__main__":
    main()
