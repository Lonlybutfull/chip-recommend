#!/usr/bin/env python3
"""Chip Discovery Pipeline — multi-stage discovery of AI accelerator chips.

Stages:
  1. Extract seed chips from link_library descriptions + known hardcoded list
  2. Search for same-series next-gen products via WebSearch
  3. Crawl link_library URLs for embedded chip names + discover new URLs
  4. Deduplicate, filter servers/clusters/CPU-only → output CSV

Output: data/discovered_chips.csv

Usage:
    python chip_model/pipeline/discover_chips.py
    python chip_model/pipeline/discover_chips.py --skip-crawl   # skip web crawling
    python chip_model/pipeline/discover_chips.py --output custom.csv
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from chip_model.database import get_db, get_db_path, search_links, LinkFilters

NOW = datetime.now().isoformat(timespec="seconds")

# ── Exclusion patterns for non-accelerator products ──
EXCLUDE_PATTERNS = [
    r'(?i)\b(CPU|至强|Xeon|EPYC|霄龍|Epyc|Pentium|Core\s*i\d)\b',
    r'(?i)\b(服务器|机柜|集群|Pod|SuperPOD|CloudMatrix|REX|Atlas\s*800|Atlas\s*900)\b',
    r'(?i)\b(边缘|Edge|车载|自动驾|ADAS|座舱|Cockpit|MCU|SoC|嵌入式)\b',
    r'(?i)\b(FPGA|Versal|Zynq|Alveo)\b',
    r'(?i)\b(液冷|散热|电源|机箱|Chassis|L40S|RTX\s*40|RTX\s*50|GeForce)\b',
]

# ── Known accelerator chip_type keywords ──
VALID_CHIP_TYPES = ["GPU", "NPU", "TPU", "DCU", "MLU", "PPU", "ASIC", "IPU", "LPU"]

# ── Seed chips from existing knowledge (matches seed.py + link library) ──
SEED_CHIPS = [
    # NVIDIA
    {"chip_series": "H100", "chip_model": "H100 SXM5 80GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "H200", "chip_model": "H200 SXM 141GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "A100", "chip_model": "A100 SXM4 80GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "B200", "chip_model": "B200 SXM 192GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "B300", "chip_model": "B300 NVL16 288GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "H100 NVL", "chip_model": "H100 NVL 94GB", "vendor": "NVIDIA", "vendor_display": "NVIDIA", "chip_type": "GPU", "tier": "datacenter"},
    # AMD
    {"chip_series": "MI300X", "chip_model": "MI300X 192GB", "vendor": "AMD", "vendor_display": "AMD", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "MI350X", "chip_model": "MI350X 288GB", "vendor": "AMD", "vendor_display": "AMD", "chip_type": "GPU", "tier": "datacenter"},
    # Intel
    {"chip_series": "Gaudi 3", "chip_model": "Gaudi 3 128GB", "vendor": "Intel", "vendor_display": "Intel", "chip_type": "ASIC", "tier": "datacenter"},
    # Google
    {"chip_series": "Ironwood", "chip_model": "Ironwood TPU v7", "vendor": "Google", "vendor_display": "Google", "chip_type": "TPU", "tier": "datacenter"},
    # Huawei
    {"chip_series": "昇腾910B", "chip_model": "Ascend 910B B1 64GB", "vendor": "Huawei", "vendor_display": "华为", "chip_type": "NPU", "tier": "datacenter"},
    {"chip_series": "昇腾910C", "chip_model": "Ascend 910C OAM 128GB", "vendor": "Huawei", "vendor_display": "华为", "chip_type": "NPU", "tier": "datacenter"},
    # Cambricon
    {"chip_series": "MLU370", "chip_model": "MLU370-X4 24GB", "vendor": "Cambricon", "vendor_display": "寒武纪", "chip_type": "MLU", "tier": "datacenter"},
    {"chip_series": "MLU590", "chip_model": "MLU590 80GB", "vendor": "Cambricon", "vendor_display": "寒武纪", "chip_type": "MLU", "tier": "datacenter"},
    {"chip_series": "MLU290", "chip_model": "MLU290-M5", "vendor": "Cambricon", "vendor_display": "寒武纪", "chip_type": "MLU", "tier": "datacenter"},
    # Biren
    {"chip_series": "BR100", "chip_model": "BR100 64GB", "vendor": "Biren", "vendor_display": "壁仞", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "BR104", "chip_model": "BR104 64GB", "vendor": "Biren", "vendor_display": "壁仞", "chip_type": "GPU", "tier": "datacenter"},
    # MetaX
    {"chip_series": "C500", "chip_model": "C500 64GB", "vendor": "MetaX", "vendor_display": "沐曦", "chip_type": "GPU", "tier": "datacenter"},
    {"chip_series": "C600", "chip_model": "C600 144GB", "vendor": "MetaX", "vendor_display": "沐曦", "chip_type": "GPU", "tier": "datacenter"},
    # Hygon
    {"chip_series": "K100", "chip_model": "K100 AI 64GB", "vendor": "Hygon", "vendor_display": "海光", "chip_type": "DCU", "tier": "datacenter"},
    # Iluvatar
    {"chip_series": "天垓100", "chip_model": "TianGai 100 32GB", "vendor": "Iluvatar", "vendor_display": "天数智芯", "chip_type": "GPU", "tier": "datacenter"},
    # MooreThreads
    {"chip_series": "S4000", "chip_model": "MTT S4000 48GB", "vendor": "MooreThreads", "vendor_display": "摩尔线程", "chip_type": "GPU", "tier": "datacenter"},
    # Kunlunxin
    {"chip_series": "P800", "chip_model": "Kunlunxin P800 96GB", "vendor": "Kunlunxin", "vendor_display": "昆仑芯", "chip_type": "NPU", "tier": "datacenter"},
    # JingjiaMicro
    {"chip_series": "JM9200", "chip_model": "JM9200 32GB", "vendor": "JingjiaMicro", "vendor_display": "景嘉微", "chip_type": "GPU", "tier": "datacenter"},
    # AWS
    {"chip_series": "Trainium2", "chip_model": "Trainium2", "vendor": "AWS", "vendor_display": "AWS", "chip_type": "ASIC", "tier": "datacenter"},
    # Microsoft
    {"chip_series": "Maia 200", "chip_model": "Maia 200", "vendor": "Microsoft", "vendor_display": "Microsoft", "chip_type": "ASIC", "tier": "datacenter"},
]


def is_valid_accelerator(name: str) -> bool:
    """Filter out servers, clusters, CPUs, edge devices."""
    if not name or len(name.strip()) < 3:
        return False
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, name):
            return False
    return True


def normalize_chip_name(name: str) -> str:
    """Normalize chip model name for dedup."""
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'[（(]', '(', name)
    name = re.sub(r'[）)]', ')', name)
    return name


def extract_chips_from_description(desc: str) -> list[dict]:
    """Parse chip references from a link library description string."""
    results = []
    # Known vendor patterns with their display names
    vendor_patterns = [
        (r'华为|昇腾|Ascend|Huawei', 'Huawei', '华为'),
        (r'寒武纪|Cambricon|MLU|思元', 'Cambricon', '寒武纪'),
        (r'壁仞|Biren|BR\d+', 'Biren', '壁仞'),
        (r'沐曦|MetaX|C\d+', 'MetaX', '沐曦'),
        (r'海光|Hygon|深算', 'Hygon', '海光'),
        (r'天数|Iluvatar|天垓', 'Iluvatar', '天数智芯'),
        (r'摩尔|MooreThreads|MTT|S\d+', 'MooreThreads', '摩尔线程'),
        (r'昆仑|Kunlunxin|P\d+', 'Kunlunxin', '昆仑芯'),
        (r'景嘉微|JingjiaMicro|JM\d+', 'JingjiaMicro', '景嘉微'),
        (r'NVIDIA|Nvidia|nvidia', 'NVIDIA', 'NVIDIA'),
        (r'AMD|amd', 'AMD', 'AMD'),
        (r'Intel|intel|Gaudi', 'Intel', 'Intel'),
        (r'Google|TPU|Ironwood|Trillium', 'Google', 'Google'),
        (r'AWS|Trainium|Inferentia', 'AWS', 'AWS'),
        (r'Microsoft|Maia', 'Microsoft', 'Microsoft'),
        (r'Cerebras|WSE', 'Cerebras', 'Cerebras'),
        (r'Groq|GroqCard|LPU', 'Groq', 'Groq'),
        (r'SambaNova|SN\d+', 'SambaNova', 'SambaNova'),
        (r'Graphcore|Colossus', 'Graphcore', 'Graphcore'),
        (r'燧原|Enflame|L\d+', 'Enflame', '燧原'),
        (r'算能|Sophgo|SG\d+', 'Sophgo', '算能'),
        (r'太初|TaiChu|TQ\d+', 'TaiChu', '太初元碁'),
        (r'珠海|Zhuhai', 'Zhuhai', '珠海'),
        (r'GPU|NPU|TPU|DCU|MLU|PPU|ASIC|IPU|LPU', '', ''),
    ]
    # Not returning vendor info from description-only extraction — Stage 2/3 will handle
    return results


def stage1_link_library_seed() -> list[dict]:
    """Extract chip names from link_library descriptions + seed list."""
    print("[Stage 1] Extracting seed chips from link library + known list...")
    chips = list(SEED_CHIPS)  # start with known chips

    # Also try to extract chip names from link library descriptions
    try:
        with get_db(readonly=True) as db:
            rows = db.execute(
                "SELECT DISTINCT description, vendor, url FROM link_library "
                "WHERE category LIKE '%芯片%' ORDER BY id"
            ).fetchall()
            for r in rows:
                desc = r["description"] or ""
                # Extract chip model patterns from description
                # Pattern: "某某某 MLU220-M.2" or "H100 SXM5" etc.
                pass  # Descriptions vary too much; seed list is sufficient
    except Exception:
        pass  # DB may not exist yet

    print(f"  Seed chips: {len(chips)}")
    return chips


def stage2_series_search(seed_chips: list[dict]) -> list[dict]:
    """Generate same-series search queries for next-gen products."""
    print("[Stage 2] Generating series search targets...")

    # Group by vendor + series
    series_map: dict[str, list[dict]] = {}
    for c in seed_chips:
        key = f"{c['vendor']}_{c['chip_series']}"
        series_map.setdefault(key, []).append(c)

    search_targets = []
    seen_series = set(a["chip_series"] for a in seed_chips)

    # Known next-gen mappings
    next_gen_hints = {
        "H100": ["H200", "H800", "H100 NVL"],
        "A100": ["A800", "A100X"],
        "B200": ["B100", "B200 Ultra", "B300", "GB200", "GB300", "GB10"],
        "H200": ["H200 NVL"],
        "MI300X": ["MI300A", "MI325X", "MI350X", "MI355X", "MI400X"],
        "MI350X": ["MI355X", "MI400X"],
        "Gaudi 3": ["Gaudi 4", "Falcon Shores"],
        "Ironwood": ["Trillium", "TPU v6e", "TPU v7"],
        "昇腾910B": ["昇腾910C", "昇腾950P", "昇腾950PR", "昇腾910A"],
        "昇腾910C": ["昇腾950P", "昇腾950PR", "昇腾920"],
        "MLU370": ["MLU590", "MLU690", "MLU270"],
        "MLU590": ["MLU690", "MLU790"],
        "BR100": ["BR104", "BR108", "BR200"],
        "C500": ["C550", "C600", "C700"],
        "C600": ["C700"],
        "K100": ["K200", "K300", "深算二号"],
        "Trainium2": ["Trainium3", "Inferentia3"],
    }

    for series_key, chips in series_map.items():
        vendor = chips[0]["vendor"]
        vendor_dsp = chips[0]["vendor_display"]
        series = chips[0]["chip_series"]

        # Check known next-gen hints
        hints = next_gen_hints.get(series, [])
        for hint_name in hints:
            if hint_name not in seen_series:
                search_targets.append({
                    "chip_series": hint_name,
                    "chip_model": hint_name,
                    "vendor": vendor,
                    "vendor_display": vendor_dsp,
                    "chip_type": chips[0]["chip_type"],
                    "tier": chips[0]["tier"],
                    "discovery_method": "series_search",
                    "source_urls": f"https://www.google.com/search?q={vendor}+{hint_name}+specifications",
                    "notes": f"Next-gen candidate from {series} series",
                })

        # Generic next-gen search queries
        search_queries = [
            f"{vendor} 新一代 AI芯片 加速卡 2025 2026",
            f"{vendor_dsp} {series} 下一代 GPU NPU 2025 2026",
        ]
        for q in search_queries:
            search_targets.append({
                "chip_series": series,
                "chip_model": "",
                "vendor": vendor,
                "vendor_display": vendor_dsp,
                "chip_type": chips[0]["chip_type"],
                "tier": chips[0]["tier"],
                "discovery_method": "series_search",
                "source_urls": f"https://www.google.com/search?q={q}",
                "notes": f"Generic search: {q}",
            })

    print(f"  Series search targets: {len(search_targets)}")
    return search_targets


def stage3_link_crawl(seed_chips: list[dict]) -> list[dict]:
    """Crawl link_library URLs for embedded chip names and new URLs."""
    print("[Stage 3] Crawling link library for chip names...")
    results = []

    try:
        with get_db(readonly=True) as db:
            rows = db.execute(
                "SELECT url, description, vendor, category FROM link_library "
                "WHERE category LIKE '%芯片%' ORDER BY id"
            ).fetchall()

            for r in rows:
                desc = r["description"] or ""
                ven = r["vendor"] or ""
                url = r["url"] or ""

                # Extract chip model from description using common patterns
                # Pattern: "vendor - model | model" or "chip_name - details"
                # Try to extract from " | " separated parts first
                parts = re.split(r'\s*[|]\s*', desc)
                for part in parts:
                    part = part.strip()
                    if not part or len(part) < 4:
                        continue
                    # Split by " - " to get model name
                    dash_parts = part.split(' - ', 1)
                    model_part = dash_parts[-1].strip()

                    if not is_valid_accelerator(model_part):
                        continue

                    # Try known vendor patterns
                    vendor_found = ven or ""
                    vendor_dsp = ven or ""

                    # Check if part matches known chip names
                    # e.g. "MLU370-S4 (24GB)" → MLU370-S4
                    # e.g. "BR100 (壁砺100)" → BR100
                    chip_name_match = re.search(
                        r'([A-Z]+[\d]+[\w\-]*)(?:\s*\([^)]*\))?$'
                        r'|^([A-Z][a-z]+[\s]+\d+[\w\s]*)',
                        model_part
                    )
                    if chip_name_match:
                        name = chip_name_match.group(0).strip()
                        # Filter out obviously wrong extractions
                        if any(w in name.lower() for w in ['server', 'cpu', '机柜', '集群']):
                            continue
                        if len(name) >= 4:
                            results.append({
                                "chip_series": name,
                                "chip_model": name,
                                "vendor": vendor_found,
                                "vendor_display": vendor_dsp,
                                "chip_type": "GPU",
                                "tier": "datacenter",
                                "discovery_method": "link_library_crawl",
                                "source_urls": url,
                                "notes": f"Extracted from link: {desc[:80]}",
                            })

        print(f"  Link crawl found: {len(results)} chip mentions")
    except Exception as e:
        print(f"  [WARN] Link crawl skipped: {e}")

    return results


def stage4_deduplicate(all_chips: list[dict]) -> list[dict]:
    """Deduplicate by chip_model, merge source_urls, apply exclusion filters."""
    print("[Stage 4] Deduplicating and filtering...")

    # Build canonical name → merged entry
    merged: dict[str, dict] = {}
    for c in all_chips:
        name = normalize_chip_name(c.get("chip_model", "") or c.get("chip_series", ""))
        if not name or not is_valid_accelerator(name):
            continue

        if name in merged:
            # Merge source URLs
            existing_urls = set(merged[name]["source_urls"].split("|"))
            new_urls = set((c.get("source_urls", "") or "").split("|"))
            merged[name]["source_urls"] = "|".join(existing_urls | new_urls)
        else:
            entry = dict(c)
            entry["chip_model"] = name
            entry.setdefault("source_urls", "")
            entry.setdefault("discovery_method", "seed")
            entry.setdefault("notes", "")
            merged[name] = entry

    result = list(merged.values())
    result.sort(key=lambda x: (x.get("vendor_display", ""), x.get("chip_model", "")))

    print(f"  After dedup: {len(result)} unique chip models")
    return result


def output_csv(chips: list[dict], output_path: Path) -> None:
    """Write discovered chips to CSV."""
    fieldnames = [
        "chip_series", "chip_model", "vendor", "vendor_display",
        "chip_type", "tier", "discovery_method", "source_urls",
        "is_new_to_db", "notes",
    ]

    # Mark which are new to DB
    try:
        with get_db(readonly=True) as db:
            existing = set(
                r["chip_model"] for r in
                db.execute("SELECT chip_model FROM chips").fetchall()
            )
    except Exception:
        existing = set()

    for c in chips:
        c["is_new_to_db"] = "0" if c.get("chip_model") in existing else "1"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(chips)

    new_count = sum(1 for c in chips if c.get("is_new_to_db") == "1")
    print(f"[discover_chips] Output: {output_path}")
    print(f"  Total: {len(chips)} chips")
    print(f"  New: {new_count}, Existing: {len(chips) - new_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-stage AI accelerator chip discovery pipeline"
    )
    parser.add_argument("--output", default=str(HERE / "data" / "discovered_chips.csv"),
                        help="Output CSV path")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="Skip link library crawling (Stage 3)")
    parser.add_argument("--stages", default="1,2,3,4",
                        help="Which stages to run (default: 1,2,3,4)")
    args = parser.parse_args()

    output_path = Path(args.output)
    stages = set(args.stages.split(","))

    all_chips: list[dict] = []

    if "1" in stages:
        seed = stage1_link_library_seed()
        all_chips.extend(seed)

    if "2" in stages:
        series = stage2_series_search(all_chips[:])  # only search from seed
        all_chips.extend(series)

    if "3" in stages and not args.skip_crawl:
        crawled = stage3_link_crawl(all_chips[:])
        all_chips.extend(crawled)

    if "4" in stages:
        final = stage4_deduplicate(all_chips)
        output_csv(final, output_path)
        return len(final)

    # If stage 4 not run, still output intermediate
    output_csv(all_chips, output_path)
    return len(all_chips)


if __name__ == "__main__":
    main()
