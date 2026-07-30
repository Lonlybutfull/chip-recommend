#!/usr/bin/env python3
"""Chip spec extractor — feeds crawled page text through Claude to extract
structured chip specs, then writes them to the database via add_chip / update_chip_fields.

Usage:
    python extract_chips.py                       # extract all new chips from chip_pages.jsonl
    python extract_chips.py --dry-run              # preview without writing
    python extract_chips.py --chip "MLU370-X4"    # extract a specific chip
    python extract_chips.py --resume               # skip chips already in DB
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
CRAWL_FILE = HERE / "data" / "crawl_all" / "chip_pages.jsonl"
DB_PATH = HERE / "data" / "parse1.db"

from chip_model.database import add_chip, update_chip_fields, get_db

NOW = __import__('datetime').datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
# LLM-powered extraction (uses this Claude session)
# ═══════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM = """You are a chip hardware extraction engine. Given raw web page text about an AI accelerator chip, extract structured specs.

## Rules
1. ONLY extract values that appear verbatim in the text. Never guess, infer, or fabricate.
2. If a field is not mentioned in the text, leave it null.
3. All numeric values are TEXT strings. Use the exact units shown (GB, W, nm, MHz, TFLOPS, etc).
4. For precision_perf, format as "BF16=96TF,FP16=96TF,INT8=256TOPS" (comma-separated TAG=VALUE pairs).
5. For precision_support, format as "FP32,FP16,BF16,INT16,INT8,INT4" (comma-separated).
6. vendor_region: "domestic" for Chinese vendors, "foreign" otherwise.
7. chip_type: "GPU", "NPU", "DCU", "TPU", "ASIC", "MLU", "IPU", "LPU".
8. usage: "训推一体" for training+inference, "推理" for inference-only, "训练" for training-only.
9. tier: "datacenter", "consumer", or "edge".
10. is_released: "1" if product is released/mass-produced, "0" if pre-release/rumored.
11. Try to identify: vendor, vendor_display, chip_series, chip_model, chip_type.

Output a flat JSON object with these keys (all TEXT strings, null for missing):

identity:
  vendor, vendor_display, vendor_region, chip_series, chip_model, chip_type, usage, tier

architecture:
  architecture, arch_codename, generation, process_node_nm, foundry, die_size_mm2,
  transistors_b, package_type, is_chiplet

memory:
  vram_gb, vram_type, vram_bus_bit, vram_bw_gb_s, vram_clock_mhz

compute_units:
  compute_units, tensor_cores, rt_cores, shading_units, sm_count

cache:
  l1_cache_kb, l2_cache_mb, on_chip_sram_mb

precision:
  precision_support, precision_perf

clock_power_physical:
  base_clock_mhz, boost_clock_mhz, tdp_w, max_power_w, psu_w, power_connector,
  board_length_mm, board_width_mm, slot_width, form_factor, bus_interface

interconnect:
  interconnect_bw_gb_s, interconnect_tech, network_interface

software:
  software_stack, compatible_frameworks

pricing:
  price_usd, price_cny_wan, price_period, price_notes

lifecycle:
  release_date, production_status, eol_date, target_market, is_released,
  expected_release_date, known_specs, unconfirmed_items

description:
  description, highlights, limitations, target_workloads, typical_deployment, competitor_comparison

ecosystem:
  ecosystem_notes, maturity_level, framework_compat, sw_stack, cuda_compat,
  cloud_available, cluster_scale, key_strength, key_weakness

Return ONLY valid JSON. No markdown, no explanation."""


def extract_specs_from_text(text: str, desc: str, vendor_hint: str) -> dict:
    """Send text to this Claude agent for extraction. Returns structured dict.

    Since we can't call Claude from Python directly, this function creates
    a prompt file and the calling agent reads it.
    """
    # This function is called from the agent context — the agent itself
    # reads the prompt and returns extracted specs.
    prompt = f"""Extract AI chip specs from this web page text.

Page description: {desc}
Vendor hint: {vendor_hint}

--- WEB PAGE TEXT START ---
{text[:8000]}
--- WEB PAGE TEXT END ---

Return ONLY a JSON object with the chip specs. Fields not found = null."""

    return {
        "_extraction_prompt": prompt,
        "_source_desc": desc,
        "_source_vendor": vendor_hint,
    }


# ═══════════════════════════════════════════════════════════════
# Chip deduplication + identity extraction (can run without LLM)
# ═══════════════════════════════════════════════════════════════

def get_existing_chip_models() -> set[str]:
    """Return set of chip_model values already in DB."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    existing = {r["chip_model"] for r in db.execute("SELECT chip_model FROM chips")}
    db.close()
    return existing


def load_crawl_pages() -> list[dict]:
    """Load all crawled chip pages with content."""
    if not CRAWL_FILE.exists():
        print(f"[ERROR] Crawl file not found: {CRAWL_FILE}")
        return []
    pages = []
    with open(CRAWL_FILE, "r", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("text_chars", 0) > 100:
                pages.append(p)
    return pages


def filter_new_chips(pages: list[dict], existing: set[str]) -> list[dict]:
    """Filter pages to those that likely describe chips not in DB."""
    known_patterns = [
        r'H100', r'A100', r'B200', r'B300', r'H200', r'MI300', r'MI350',
        r'Gaudi', r'Ironwood', r'Maia 200', r'Trainium2',
        r'昇腾910B', r'昇腾910C', r'MLU590', r'MLU370-X4', r'MLU370-X8',
        r'MLU290', r'BR100', r'BR104', r'C500', r'C600', r'S4000',
        r'昆仑芯P800', r'天垓100', r'JM9200', r'K100 AI', r'深算三号',
    ]
    skip_kw = [
        'Atlas 800', 'Atlas 900', 'SuperPoD', 'CloudMatrix', 'REX1032',
        '鲲鹏', '飞腾', '龙芯', '服务器', 'Pod', '机柜', 'RISC-V', 'SG2380',
        'CPU', '智能座舱', '征程', '华山', '黑芝麻', '地平线',
        '智能驾驶', '自动驾驶', '全志', 'Mobile', '芯原', 'VIP', 'IP授权',
        'FPGA', 'C86-4G', 'C86-5G', 'ARM', 'SoC', '芯擎', '龙鹰',
        '灵汐', '类脑', '鲲云', '亿智', '爱芯',
    ]
    new = []
    for p in pages:
        desc = p.get("desc", "")
        vendor = p.get("vendor", "")
        # Skip server/CPU/edge/IP products
        if any(kw in desc for kw in skip_kw):
            continue
        # Skip known chips already in DB
        if any(re.search(pat, desc) for pat in known_patterns):
            continue
        # Must have decent content
        if len(p.get("text", "")) < 50:
            continue
        new.append(p)
    return new


# ═══════════════════════════════════════════════════════════════
# Direct extraction from structured text (regex-based for known patterns)
# ═══════════════════════════════════════════════════════════════

def attempt_direct_extraction(page: dict) -> dict | None:
    """Try to extract chip specs directly from well-structured pages using regex.

    This handles pages that follow a key-value table format (like the Cambricon
    product pages, Chinese datasheets). Returns a dict ready for add_chip().
    """
    text = page.get("text", "")
    desc = page.get("desc", "")
    vendor = page.get("vendor", "").split(";")[0].strip()

    # ── Identify vendor and chip name from description ──
    vendor_map = {
        "Cambricon": {"vendor": "Cambricon", "vendor_display": "寒武纪", "vendor_region": "domestic"},
        "Huawei": {"vendor": "Huawei", "vendor_display": "华为(昇腾)", "vendor_region": "domestic"},
        "NVIDIA": {"vendor": "NVIDIA", "vendor_display": "NVIDIA", "vendor_region": "foreign"},
        "AMD": {"vendor": "AMD", "vendor_display": "AMD", "vendor_region": "foreign"},
        "Intel": {"vendor": "Intel", "vendor_display": "Intel", "vendor_region": "foreign"},
        "Google": {"vendor": "Google", "vendor_display": "Google", "vendor_region": "foreign"},
        "AWS": {"vendor": "AWS", "vendor_display": "AWS", "vendor_region": "foreign"},
        "Microsoft": {"vendor": "Microsoft", "vendor_display": "Microsoft", "vendor_region": "foreign"},
        "Meta": {"vendor": "Meta", "vendor_display": "Meta", "vendor_region": "foreign"},
        "Biren": {"vendor": "Biren", "vendor_display": "壁仞科技", "vendor_region": "domestic"},
        "MetaX": {"vendor": "MetaX", "vendor_display": "沐曦股份", "vendor_region": "domestic"},
        "Hygon": {"vendor": "Hygon", "vendor_display": "海光信息", "vendor_region": "domestic"},
        "Iluvatar": {"vendor": "Iluvatar", "vendor_display": "天数智芯", "vendor_region": "domestic"},
        "MooreThreads": {"vendor": "MooreThreads", "vendor_display": "摩尔线程", "vendor_region": "domestic"},
        "Kunlunxin": {"vendor": "Kunlunxin", "vendor_display": "昆仑芯(百度)", "vendor_region": "domestic"},
        "JingjiaMicro": {"vendor": "JingjiaMicro", "vendor_display": "景嘉微", "vendor_region": "domestic"},
        "Groq": {"vendor": "Groq", "vendor_display": "Groq", "vendor_region": "foreign"},
        "Cerebras": {"vendor": "Cerebras", "vendor_display": "Cerebras", "vendor_region": "foreign"},
        "Graphcore": {"vendor": "Graphcore", "vendor_display": "Graphcore", "vendor_region": "foreign"},
        "SambaNova": {"vendor": "SambaNova", "vendor_display": "SambaNova", "vendor_region": "foreign"},
        "Enflame": {"vendor": "Enflame", "vendor_display": "燧原科技", "vendor_region": "domestic"},
        "寒武纪": {"vendor": "Cambricon", "vendor_display": "寒武纪", "vendor_region": "domestic"},
        "华为(昇腾)": {"vendor": "Huawei", "vendor_display": "华为(昇腾)", "vendor_region": "domestic"},
        "海光信息": {"vendor": "Hygon", "vendor_display": "海光信息", "vendor_region": "domestic"},
        "沐曦股份": {"vendor": "MetaX", "vendor_display": "沐曦股份", "vendor_region": "domestic"},
        "壁仞科技": {"vendor": "Biren", "vendor_display": "壁仞科技", "vendor_region": "domestic"},
        "摩尔线程": {"vendor": "MooreThreads", "vendor_display": "摩尔线程", "vendor_region": "domestic"},
        "昆仑芯(百度)": {"vendor": "Kunlunxin", "vendor_display": "昆仑芯(百度)", "vendor_region": "domestic"},
        "燧原科技": {"vendor": "Enflame", "vendor_display": "燧原科技", "vendor_region": "domestic"},
        "天数智芯": {"vendor": "Iluvatar", "vendor_display": "天数智芯", "vendor_region": "domestic"},
        "景嘉微": {"vendor": "JingjiaMicro", "vendor_display": "景嘉微", "vendor_region": "domestic"},
        "清微智能": {"vendor": "TsingMicro", "vendor_display": "清微智能", "vendor_region": "domestic"},
        "中昊芯英": {"vendor": "ZhongHao", "vendor_display": "中昊芯英", "vendor_region": "domestic"},
        "登临科技": {"vendor": "Denglin", "vendor_display": "登临科技", "vendor_region": "domestic"},
        "芯动科技": {"vendor": "Innosilicon", "vendor_display": "芯动科技", "vendor_region": "domestic"},
        "平头哥(阿里)": {"vendor": "THead", "vendor_display": "平头哥(阿里)", "vendor_region": "domestic"},
        "算能科技": {"vendor": "Sophgo", "vendor_display": "算能科技", "vendor_region": "domestic"},
    }

    vinfo = vendor_map.get(vendor, vendor_map.get(
        vendor.split(";")[0].strip() if ";" in vendor else vendor,
        {"vendor": vendor, "vendor_display": vendor, "vendor_region": "domestic" if any(
            kw in desc for kw in ["国产", "寒武纪", "华为", "海光", "壁仞", "沐曦", "天数", "燧原", "昆仑",
                                  "景嘉微", "摩尔线程", "登临", "平头哥", "算能", "清微", "芯动", "中昊"]
        ) else "foreign"}
    ))

    # ── Extract chip_model and chip_series from description ──
    chip_model = None
    chip_series = None

    # Pattern: "硬件规格 - CHIP_MODEL | ..."
    m = re.search(r'硬件规格\s*[-—]\s*([^|]+)', desc)
    if m:
        chip_model = m.group(1).strip()
        # Try to extract series from model
        series_map = {
            "MLU370": "思元370", "MLU290": "思元290", "MLU270": "思元270",
            "MLU220": "思元220", "MLU590": "思元590", "MLU690": "思元690",
            "昇腾910": "昇腾910", "昇腾950": "昇腾950", "昇腾310": "昇腾310",
            "BR100": "壁砺100", "BR104": "壁砺104",
            "曦云C500": "曦云C500", "曦云C600": "曦云C600", "曦思N100": "曦思N100",
            "MTT S4000": "MTT S4000", "MTT S5000": "MTT S5000",
            "昆仑芯": "昆仑芯", "天垓": "天垓", "风华": "风华",
            "深算": "深算", "邃思": "邃思", "云燧": "云燧",
            "JM1100": "JM11", "JM9200": "JM9", "JM7200": "JM7",
            "TX510": "TX510", "TX81": "TX81", "TX82": "TX82",
            "Goldwasser": "Goldwasser", "刹那": "刹那",
            "真武": "真武", "GroqCard": "GroqCard",
            "WSE-3": "WSE-3", "Colossus": "Colossus",
            "SN40L": "SN40L", "Inferentia": "Inferentia",
        }
        for key, val in series_map.items():
            if key in chip_model:
                chip_series = val
                break

    if not chip_model and "MTIA" in desc:
        chip_model = "MTIA v2" if "v2" in desc else "MTIA"
        chip_series = "MTIA"
        vinfo = vendor_map.get("Meta", vinfo)

    if not chip_model:
        # Try to extract from description more broadly
        m = re.search(r'\| (.+?) 硬件', desc)
        if m:
            chip_model = m.group(1).strip()

    if not chip_model:
        return None  # Can't identify the chip

    # ── Extract hardware specs from text ──
    specs: dict = {}

    # Memory / VRAM
    m = re.search(r'(?:显存|内存|VRAM|HBM|HBM2e|HBM3|HBM3e|GDDR|LPDDR).*?(\d+\.?\d*)\s*(?:GB|GiB)', text, re.IGNORECASE)
    if m:
        specs["vram_gb"] = m.group(1)
    m = re.search(r'(HBM3e|HBM3|HBM2e|HBM2|GDDR6X|GDDR6|GDDR5|LPDDR5|LPDDR4|HBM)', text, re.IGNORECASE)
    if m:
        specs["vram_type"] = m.group(1)
    # Memory bandwidth
    m = re.search(r'(?:带宽|bandwidth).*?(\d+\.?\d*)\s*(?:GB/s|Gbps|GBps)', text, re.IGNORECASE)
    if m:
        specs["vram_bw_gb_s"] = m.group(1)

    # Process node
    m = re.search(r'(?:制程|工艺|process).*?(\d+)\s*nm', text, re.IGNORECASE)
    if m:
        specs["process_node_nm"] = m.group(1)

    # Architecture
    m = re.search(r'(?:架构|architecture)\s*[:：]?\s*([A-Za-z]+\s*[\w.]*)', text)
    if m:
        specs["architecture"] = m.group(1).strip()
    m = re.search(r'([A-Z][a-z]+)\s*(?:架构|architecture)', text)
    if m and m.group(1) not in ("Reference", "Data", "Base", "Super"):
        specs["architecture"] = m.group(1)

    # TDP / Power
    m = re.search(r'(?:功耗|TDP|功率).*?(\d+\.?\d*)\s*(?:W|瓦)', text, re.IGNORECASE)
    if m:
        specs["tdp_w"] = m.group(1)
    m = re.search(r'(?:MAX|最大).*?(\d+\.?\d*)\s*(?:W|瓦)', text, re.IGNORECASE)
    if m:
        specs["max_power_w"] = m.group(1)

    # Precision support
    precisions = set()
    for prec in ["FP64", "FP32", "TF32", "FP16", "BF16", "FP8", "INT16", "INT8", "INT4", "INT2", "INT1"]:
        if prec in text.upper():
            precisions.add(prec)
    if precisions:
        specs["precision_support"] = ",".join(sorted(precisions, key=lambda x: (
            x.startswith("F"), 0 if "32" in x else (1 if "16" in x else (2 if "8" in x else 3))
        )))

    # Precision performance
    perf_parts = []
    for prec, unit_pattern in [("FP32", r"(\d+\.?\d*)\s*TFLOPS.*?FP32"), ("FP16", r"FP16.*?(\d+\.?\d*)\s*T"),
                               ("BF16", r"BF16.*?(\d+\.?\d*)\s*T"), ("INT8", r"INT8.*?(\d+\.?\d*)\s*T"),
                               ("INT4", r"INT4.*?(\d+\.?\d*)\s*T")]:
        m = re.search(unit_pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1)
            perf_parts.append(f"{prec}={val}{'TF' if 'FLOPS' in m.group(0) else 'TOPS'}")
    if not perf_parts:
        # Generic pattern
        for m in re.finditer(r'(FP\d+|BF\d+|INT\d+|TF\d+)\s*[:=]?\s*(\d+\.?\d*)\s*(TFLOPS|TF|TOPS)', text):
            perf_parts.append(f"{m.group(1)}={m.group(2)}{'TFLOPS' if m.group(3)=='TFLOPS' else m.group(3)}")
    if perf_parts:
        specs["precision_perf"] = ",".join(perf_parts)[:200]

    # Form factor / bus
    m = re.search(r'(PCIe\s*[\d.]+|SXM\d*|OAM|FHFL|HHHL|SXM)', text, re.IGNORECASE)
    if m:
        specs["form_factor"] = m.group(1)
        specs["bus_interface"] = m.group(1) if "PCIe" in m.group(1) else None

    # Interconnect
    m = re.search(r'(NVLink|HCCS|MLU-Link|BLink|Infinity Fabric|MetaXLink|ICI|RoCE)\s*[\d.]*', text, re.IGNORECASE)
    if m:
        specs["interconnect_tech"] = m.group(0).strip()
    m = re.search(r'(?:互联|interconnect).*?(\d+\.?\d*)\s*(?:GB/s|Gbps)', text, re.IGNORECASE)
    if m:
        specs["interconnect_bw_gb_s"] = m.group(1)

    # Transistors
    m = re.search(r'(\d+\.?\d*)\s*[亿百]?\s*(?:晶体管|transistor)', text, re.IGNORECASE)
    if m:
        specs["transistors_b"] = m.group(1) + ("" if "亿" in m.group(0) else ("00" if "百" not in m.group(0) else "0"))

    # Die size
    m = re.search(r'(\d+\.?\d*)\s*mm[²2]', text)
    if m:
        specs["die_size_mm2"] = m.group(1)

    # Release date
    m = re.search(r'(?:发布|量产|release).*?(20\d{2}[-./年]\d{1,2})', text, re.IGNORECASE)
    if m:
        specs["release_date"] = m.group(1).replace("年", "-").replace("/", "-").replace(".", "-")

    # Production status
    if "已量产" in text or "量产" in text:
        specs["production_status"] = "已量产"
        specs["is_released"] = "1"
    elif "已发布" in text or "发布" in text:
        specs["production_status"] = "已发布"
        specs["is_released"] = "1"
    elif "规划中" in desc or "待发布" in desc:
        specs["production_status"] = "待发布"
        specs["is_released"] = "0"

    # Description
    desc_match = re.search(r'(?:描述|概述|简介|Description).*?[:：]\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if desc_match:
        specs["description"] = desc_match.group(1).strip()[:500]
    else:
        # Use first non-empty meaningful line
        lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 30]
        if lines:
            specs["description"] = lines[0][:500]

    # Software stack
    m = re.search(r'(?:软件栈|SDK|Software|stack).*?[:：]\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        specs["software_stack"] = m.group(1).strip()[:200]

    # Chip type detection
    if "GPU" in desc or "GPU" in text[:500]:
        specs["chip_type"] = "GPU"
    elif "NPU" in desc or "NPU" in text[:500]:
        specs["chip_type"] = "NPU"
    elif "MLU" in desc or "MLU" in chip_model:
        specs["chip_type"] = "MLU"
    elif "TPU" in desc or "TPU" in chip_model:
        specs["chip_type"] = "TPU/ASIC"
    elif "DCU" in desc or "DCU" in chip_model or "深算" in desc:
        specs["chip_type"] = "DCU"
    elif "LPU" in desc or "LPU" in chip_model:
        specs["chip_type"] = "LPU"
    elif "IPU" in desc or "IPU" in chip_model:
        specs["chip_type"] = "IPU"
    elif "ASIC" in desc or "ASIC" in chip_model:
        specs["chip_type"] = "ASIC"
    else:
        specs["chip_type"] = "GPU"  # default

    # Usage
    if "训推" in text or ("训练" in text and "推理" in text):
        specs["usage"] = "训推一体"
    elif "推理" in text and "训练" not in text:
        specs["usage"] = "推理"
    elif "训练" in text and "推理" not in text:
        specs["usage"] = "训练"
    else:
        specs["usage"] = "训推一体"

    specs["tier"] = "datacenter"

    # Build final fields dict
    fields = {
        **vinfo,
        "chip_series": chip_series or chip_model,
        "chip_model": chip_model,
        "chip_type": specs.pop("chip_type", "GPU"),
        "usage": specs.pop("usage", "训推一体"),
        "tier": specs.pop("tier", "datacenter"),
        **specs,
    }

    # Don't return if we only have identity and nothing else useful
    hw_fields = {k: v for k, v in specs.items()
                 if k not in ("chip_type", "usage", "tier", "description")}
    if not hw_fields and len(fields) < 12:
        # Only identity fields — likely not a real chip page
        pass

    return fields


# ═══════════════════════════════════════════════════════════════
# Insert logic
# ═══════════════════════════════════════════════════════════════

def insert_chip(fields: dict, source_url: str) -> bool:
    """Insert a new chip into DB. Returns True if inserted, False if skipped."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    chip_model = fields.get("chip_model", "")
    existing = db.execute(
        "SELECT id FROM chips WHERE chip_model = ?", (chip_model,)
    ).fetchone()
    if existing:
        db.close()
        return False

    source = {
        "source_type": "web_crawl",
        "source_url": source_url,
        "source_detail": f"Extracted via regex from crawled page",
        "confidence": "medium",
        "is_official": "1" if "官网" in source_url or "official" in source_url.lower() else "0",
        "notes": f"Auto-extracted {NOW}",
    }

    try:
        rid = add_chip(db, fields, source)
        db.commit()
        print(f"  ADDED [{rid}] {fields.get('vendor_display', '')} — {chip_model}")
        return True
    except Exception as e:
        print(f"  ERROR: {chip_model} — {e}")
        db.rollback()
        return False
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Extract chip specs from crawled pages")
    parser.add_argument("--dry-run", action="store_true", help="Preview extraction without writing")
    parser.add_argument("--chip", type=str, help="Extract a specific chip")
    parser.add_argument("--resume", action="store_true", help="Skip chips already in DB")
    parser.add_argument("--max", type=int, default=0, help="Max chips to process")
    args = parser.parse_args()

    existing = get_existing_chip_models()
    pages = load_crawl_pages()

    if args.chip:
        pages = [p for p in pages if args.chip.lower() in p.get("desc", "").lower()]
        if not pages:
            print(f"[ERROR] No page found matching: {args.chip}")
            return

    new_pages = filter_new_chips(pages, existing)
    print(f"[SCAN] {len(new_pages)} new chip pages to process")
    print(f"  (filtered from {len(pages)} pages, {len(existing)} chips already in DB)")
    print()

    if args.max > 0:
        new_pages = new_pages[:args.max]

    inserted = 0
    skipped = 0
    failed = 0

    for i, page in enumerate(new_pages):
        desc = page.get("desc", "")[:60]
        print(f"[{i+1:3d}/{len(new_pages)}] {desc}...", end=" ", flush=True)

        fields = attempt_direct_extraction(page)
        if fields is None:
            print("SKIP (no extractable chip)")
            skipped += 1
            continue

        chip_model = fields.get("chip_model", "?")

        if args.dry_run:
            hw_fields = {k: v for k, v in fields.items()
                         if k not in ("vendor", "vendor_display", "vendor_region",
                                      "chip_series", "chip_model", "chip_type", "usage", "tier")}
            print(f"PREVIEW: {chip_model} ({len(hw_fields)} HW fields)")
            for k, v in sorted(hw_fields.items()):
                print(f"    {k}: {v}")
            skipped += 1
            continue

        if args.resume and chip_model in existing:
            print(f"SKIP (already in DB)")
            skipped += 1
            continue

        success = insert_chip(fields, page.get("url", ""))
        if success:
            inserted += 1
            existing.add(chip_model)
        else:
            skipped += 1

    print()
    print(f"[DONE] inserted={inserted}  skipped={skipped}  failed={failed}")
    print(f"[DB] Total chips now: {len(get_existing_chip_models())}")


if __name__ == "__main__":
    main()
