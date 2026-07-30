#!/usr/bin/env python3
"""Extract price data from crawled price pages and update chip pricing fields.

Also enriches new chips with missing hardware specs using LLM-powered extraction
from the crawled page text.

Usage:
    python extract_prices.py              # process all
    python extract_prices.py --dry-run    # preview only
"""

import argparse, json, re, sqlite3, sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent.parent
PRICE_FILE = HERE / "data" / "crawl_all" / "price_pages.jsonl"
DB_PATH = HERE / "data" / "data.db"

from chip_model.database import update_chip_fields

NOW = datetime.now().isoformat(timespec="seconds")


def load_price_pages() -> list[dict]:
    if not PRICE_FILE.exists():
        return []
    pages = []
    with open(PRICE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("text_chars", 0) > 100:
                pages.append(p)
    return pages


def extract_prices(text: str, desc: str) -> list[dict]:
    """Extract price records from text. Returns list of {chip, price_cny_wan, price_usd, ...}."""
    results = []

    # Known chip names to search for
    chip_price_map = {
        "H100": {"chip_model": "H100 SXM5 80GB", "ids": [1]},
        "A100": {"chip_model": "A100 SXM4 80GB", "ids": [2]},
        "B200": {"chip_model": "B200 SXM 192GB", "ids": [3]},
        "H100 NVL": {"chip_model": "H100 NVL 94GB", "ids": [4]},
        "H200": {"chip_model": "H200 SXM 141GB", "ids": [5]},
        "B300": {"chip_model": "B300 NVL16 288GB", "ids": [6]},
        "MI300X": {"chip_model": "Instinct MI300X 192GB", "ids": [7]},
        "MI350": {"chip_model": "Instinct MI350X 288GB", "ids": [8]},
        "Gaudi": {"chip_model": "Gaudi 3 128GB", "ids": [9]},
        "910B": {"chip_model": "昇腾910B B1 (64GB)", "ids": [11]},
        "910C": {"chip_model": "昇腾910C (OAM 128GB)", "ids": [12]},
        "MLU590": {"chip_model": "MLU590 (80GB)", "ids": [14]},
        "MLU370": {"chip_model": "MLU370-X4 (24GB)", "ids": [13]},
    }

    for key, cinfo in chip_price_map.items():
        if not re.search(key, text, re.IGNORECASE):
            continue

        # Find price mentions near the chip name
        price_cny = None
        price_usd = None
        price_notes = []

        # Chinese price: N万/N万元
        m = re.search(rf'{key}.*?(\d+\.?\d*)\s*(?:万|W)', text[:2000], re.IGNORECASE)
        if m:
            price_cny = m.group(1)
            price_notes.append(f"Found: {m.group(0)[:40]}")

        # USD price: $N or N USD
        m = re.search(rf'{key}.*?\$?(\d+[.,]?\d*)\s*(?:USD|美元|美)', text[:2000], re.IGNORECASE)
        if m and m.group(1).replace(',', '').replace('.', '').isdigit():
            v = m.group(1).replace(',', '')
            if float(v) > 100:  # Must be a reasonable price
                price_usd = v
                price_notes.append(f"USD: {m.group(0)[:40]}")

        # General price patterns
        for m in re.finditer(r'(?:价格|报价|售价|price).*?(\d+\.?\d*)\s*(?:万|万元|W)', text[:3000], re.IGNORECASE):
            val = m.group(1)
            if price_cny is None:
                price_cny = val
            price_notes.append(f"Price mention: {m.group(0)[:50]}")

        for m in re.finditer(r'\$\s*(\d{2,6})', text[:3000]):
            if price_usd is None:
                price_usd = m.group(1)
                price_notes.append(f"USD mention: {m.group(0)}")

        if price_cny or price_usd:
            results.append({
                "chip_model": cinfo["chip_model"],
                "ids": cinfo["ids"],
                "price_cny_wan": price_cny,
                "price_usd": price_usd,
                "price_notes": " | ".join(price_notes)[:200],
                "price_period": "2025 Q2" if "2025" in text else "2025",
            })

    return results


def update_price(chip_id: int, price_data: dict, source_url: str) -> bool:
    """Update chip pricing fields."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    fields = {}
    if price_data.get("price_cny_wan"):
        fields["price_cny_wan"] = price_data["price_cny_wan"]
    if price_data.get("price_usd"):
        fields["price_usd"] = price_data["price_usd"]
    if price_data.get("price_period"):
        fields["price_period"] = price_data["price_period"]
    if price_data.get("price_notes"):
        fields["price_notes"] = price_data["price_notes"]

    if not fields:
        db.close()
        return False

    # Check existing — only update if no existing price
    cur = db.execute("SELECT price_cny_wan, price_usd FROM chips WHERE id = ?", (chip_id,)).fetchone()
    if cur and cur["price_cny_wan"] and cur["price_usd"]:
        db.close()
        return False

    source = {
        "source_type": "community",
        "source_url": source_url,
        "source_detail": "Extracted from crawled price page",
        "confidence": "medium",
        "is_official": "0",
        "notes": f"Auto-extracted {NOW}",
    }

    try:
        update_chip_fields(db, chip_id, fields, source)
        db.commit()
        return True
    except Exception as e:
        print(f"  ERROR: chip {chip_id} — {e}")
        db.rollback()
        return False
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# Phase 2: Enrich new chips with missing specs from crawled text
# ═══════════════════════════════════════════════════════════════

def enrich_chip_specs():
    """For chips added by crawl (id >= 27), try to fill missing HW specs
    by re-scanning the crawled pages for specs."""
    import sys
    sys.path.insert(0, str(HERE))

    # Load all crawled text
    chip_texts = {}
    with open(HERE / "crawl_all" / "chip_pages.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("text_chars", 0) > 100:
                chip_texts[p.get("desc", "")] = p

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Get chips with missing VRAM (id >= 27)
    chips = db.execute(
        "SELECT id, chip_model, chip_series, vendor_display, vram_gb, tdp_w, "
        "precision_perf, precision_support, vram_type "
        "FROM chips WHERE id >= 27 AND (vram_gb IS NULL OR precision_perf IS NULL) "
        "ORDER BY id"
    ).fetchall()

    print(f"\n[ENRICH] {len(chips)} new chips with missing specs\n")

    updated = 0
    for chip in chips:
        chip_model = chip["chip_model"]
        chip_series = chip["chip_series"]
        vid = chip["id"]

        # Find relevant crawled page
        relevant_text = ""
        relevant_url = ""
        for desc, page in chip_texts.items():
            if chip_model in desc or (chip_series and chip_series in desc) or \
               any(kw in desc for kw in chip_model.split() if len(kw) > 3):
                relevant_text = page.get("text", "")
                relevant_url = page.get("url", "")
                break

        if not relevant_text:
            continue

        text = relevant_text
        fields = {}

        # VRAM
        if not chip["vram_gb"]:
            m = re.search(r'(?:显存|内存|VRAM|HBM|GDDR|LPDDR)[^.]*?(\d+\.?\d*)\s*(?:GB|GiB|MB)?', text, re.IGNORECASE)
            if m and float(m.group(1)) > 0.5:
                val = m.group(1)
                if "MB" in m.group(0) and float(val) > 100:
                    val = str(int(float(val) / 1024))
                fields["vram_gb"] = val
            # Also check model name for VRAM
            m = re.search(r'(\d+)\s*GB', chip_model)
            if not fields.get("vram_gb") and m:
                fields["vram_gb"] = m.group(1)

        # VRAM type
        if not chip["vram_type"]:
            m = re.search(r'(HBM3e|HBM3|HBM2e|HBM2|GDDR6X|GDDR6|GDDR5X|GDDR5|LPDDR5|LPDDR4|HBM)', text, re.IGNORECASE)
            if m:
                fields["vram_type"] = m.group(1)
            elif "HBM2e" in chip_model:
                fields["vram_type"] = "HBM2e"
            elif "HBM3e" in chip_model:
                fields["vram_type"] = "HBM3e"
            elif "HBM3" in chip_model:
                fields["vram_type"] = "HBM3"
            elif "GDDR6" in chip_model:
                fields["vram_type"] = "GDDR6"

        # TDP
        if not chip["tdp_w"]:
            m = re.search(r'(?:功耗|TDP|功率)[^.]*?(\d+\.?\d*)\s*(?:W|瓦)', text, re.IGNORECASE)
            if m:
                fields["tdp_w"] = m.group(1)

        # Precision support
        if not chip["precision_support"]:
            precs = []
            for p in ["FP64", "FP32", "TF32", "FP16", "BF16", "FP8", "INT16", "INT8", "INT4"]:
                if p in text.upper():
                    precs.append(p)
            if precs:
                fields["precision_support"] = ",".join(precs)

        # Precision performance
        if not chip["precision_perf"]:
            perf = []
            for m in re.finditer(r'(FP\d+|BF\d+|INT\d+|TF\d+)\s*[:=]?\s*(\d+\.?\d*)\s*(TFLOPS|TF|TOPS)', text):
                perf.append(f"{m.group(1)}={m.group(2)}{'TFLOPS' if m.group(3)=='TFLOPS' else m.group(3)}")
            if perf:
                fields["precision_perf"] = ",".join(perf)[:200]

        # Memory bandwidth
        m = re.search(r'(?:带宽|bandwidth).*?(\d+\.?\d*)\s*(?:GB/s|Gbps|GBps)', text, re.IGNORECASE)
        if m:
            fields["vram_bw_gb_s"] = m.group(1)

        # Architecture
        m = re.search(r'(?:架构|architecture)[:：\s]*([A-Za-z][\w\s.]+)', text)
        if m:
            arch = m.group(1).strip()[:100]
            if len(arch) > 2:
                fields["architecture"] = arch

        # Release date
        m = re.search(r'(?:发布|release).*?(20\d{2})', text, re.IGNORECASE)
        if m:
            fields["release_date"] = m.group(1)

        # Process node
        m = re.search(r'(?:制程|工艺|process)\D*(\d+)\s*nm', text, re.IGNORECASE)
        if m:
            fields["process_node_nm"] = m.group(1)

        # Interconnect
        m = re.search(r'(NVLink|HCCS|MLU-Link|BLink|Infinity Fabric|MetaXLink|ICI|RoCE|BLink)\s*[\d.]*', text, re.IGNORECASE)
        if m:
            fields["interconnect_tech"] = m.group(0).strip()

        if not fields:
            continue

        # Write
        source = {
            "source_type": "web_crawl",
            "source_url": relevant_url,
            "source_detail": "Extracted from crawled page text",
            "confidence": "medium",
            "is_official": "0",
            "notes": f"Enriched {NOW}",
        }

        try:
            update_chip_fields(db, vid, fields, source)
            db.commit()
            updated += 1
            fstr = ", ".join(f"{k}={v[:30] if v else '?'}" for k,v in list(fields.items())[:4])
            print(f"  [{vid:2d}] {chip['vendor_display']:10s} {chip_model[:35]:35s} +{len(fields)} fields: {fstr}")
        except Exception as e:
            print(f"  [{vid:2d}] ERROR: {e}")
            db.rollback()

    db.close()
    print(f"\n[ENRICH] Updated {updated}/{len(chips)} chips")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enrich", action="store_true", help="Also enrich new chips with missing specs")
    args = parser.parse_args()

    # Phase 1: Price extraction
    pages = load_price_pages()
    print(f"[LOAD] {len(pages)} price pages with content\n")

    inserted = 0
    for i, page in enumerate(pages):
        desc = page.get("desc", "")[:80]
        records = extract_prices(page.get("text", ""), desc)
        if not records:
            continue

        for r in records:
            for chip_id in r["ids"]:
                if args.dry_run:
                    print(f"  PREVIEW: {r['chip_model']} CNY={r.get('price_cny_wan')}万 USD={r.get('price_usd')}")
                else:
                    if update_price(chip_id, r, page.get("url", "")):
                        inserted += 1

    print(f"\n[PRICES] Updated {inserted} chip prices")

    # Phase 2: Enrich missing specs
    if args.enrich:
        enrich_chip_specs()

    # Show totals
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    cnt = db.execute("SELECT COUNT(*) as n FROM field_provenance").fetchone()["n"]
    db.close()
    print(f"[DB] Total provenance: {cnt}")


if __name__ == "__main__":
    main()
