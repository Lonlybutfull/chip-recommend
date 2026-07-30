#!/usr/bin/env python3
"""Master crawl script: process all 473 links from 信息来源链接库_final.csv.

Pipelines:
  Pipe A — 芯片硬件参数 + 芯片信息综合 + 芯片官方规格: web crawl → save text → LLM extraction
  Pipe B — 模型信息 + 模型信息(API): extract model_id → HF API batch fetch
  Pipe C — 训推测试 + 训推测试(国产) + 训推测试(MLPerf): web crawl → save text → LLM extraction
  Pipe D — 价格与出货量: web crawl → save to CSV later

Phase 1: Download all accessible URLs → JSONL
Phase 2: HF API batch fetch models
Phase 3: Extract chip specs + benchmarks from downloaded text
"""

import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Comment

HERE = Path(__file__).resolve().parent.parent.parent
CSV_PATH = HERE / "data" / "信息来源链接库_final.csv"
DB_PATH = HERE / "data" / "parse1.db"
OUTPUT_DIR = HERE / "data" / "crawl_all"
OUTPUT_DIR.mkdir(exist_ok=True)

from chip_model.database import add_chip, add_model, add_benchmark, add_compat

PROXY = "http://127.0.0.1:7897"
PROXIES = {"http": PROXY, "https": PROXY}
HF_API = "https://huggingface.co/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TIMEOUT = 15

# ── helpers ──

def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header",
                      "noscript", "iframe", "form", "button"]):
        tag.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    main = soup.find("main") or soup.find("article") or soup.find("body") or soup
    t = main.get_text(separator="\n")
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    return "\n".join(lines)


def fetch_one(url: str) -> dict:
    """Fetch one URL, return {status, text, final_url, error}."""
    try:
        resp = requests.get(url, headers=HEADERS, proxies=PROXIES,
                           timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        text = extract_text(resp.text)
        if len(text) > 12000:
            text = text[:12000] + "\n... [TRUNCATED]"
        return {"status": resp.status_code, "text": text,
                "final_url": resp.url, "error": ""}
    except Exception as e:
        return {"status": 0, "text": "", "final_url": "", "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Pipeline A: Chip hardware web crawl
# ═══════════════════════════════════════════════════════════════

def get_chip_pages(rows: list[dict]) -> list[dict]:
    """Extract chip hardware URLs from CSV rows."""
    chip_cats = {"芯片硬件参数", "芯片信息综合", "芯片官方规格", "芯片硬件参数(第三方)"}
    result = []
    seen_urls = set()
    for r in rows:
        cat = r["分类"]
        url = r["URL"].strip()
        acc = r.get("可访问", "")

        if cat not in chip_cats:
            continue
        if "否" in acc and acc.count("是") == 0:
            continue
        if any(b in acc for b in ("HTTP404", "HTTP500", "HTTP502", "HTTP403", "HTTP429")):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        result.append({
            "url": url, "desc": r["描述"].strip(), "vendor": r["涉及厂商"].strip(),
            "category": cat,
        })
    return result


def crawl_chip_pages(pages: list[dict]) -> str:
    """Download all chip pages, save to JSONL. Returns output path."""
    path = OUTPUT_DIR / "chip_pages.jsonl"
    results = []
    ok = 0
    for i, p in enumerate(pages):
        print(f"  [{i+1:3d}/{len(pages)}] {p['desc'][:50]}...", end=" ", flush=True)
        fr = fetch_one(p["url"])
        rec = {**p, **fr, "fetched_at": datetime.now().isoformat(),
               "text_chars": len(fr["text"])}
        if fr["text"]:
            print(f"OK ({len(fr['text'])}c)")
            ok += 1
        else:
            print(f"FAIL: {fr['error'][:50]}")
        results.append(rec)
        time.sleep(0.3)

    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  [CHIP PAGES] {ok}/{len(pages)} succeeded -> {path}")
    return str(path)


# ═══════════════════════════════════════════════════════════════
# Pipeline B: Model IDs from HF URLs
# ═══════════════════════════════════════════════════════════════

def get_model_ids(rows: list[dict]) -> list[str]:
    """Extract unique model IDs from CSV HF model URLs."""
    model_cats = {"模型信息", "模型信息(API)", "模型/测试", "模型信息(爬取入口)"}
    model_ids = set()

    for r in rows:
        cat = r["分类"]
        url = r["URL"].strip()
        desc = r["描述"].strip()

        if cat not in model_cats:
            # Also try to parse HF URLs from any category
            pass

        # Parse huggingface.co URLs
        m = re.match(r'https?://huggingface\.co/([^/]+(?:/[^/]+?))(?:\?|/|#|$)', url)
        if not m:
            # Try alternative patterns
            m = re.match(r'https?://huggingface\.co/([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)', url)
        if m:
            mid = m.group(1).rstrip("/")
            # Skip non-model paths
            if mid in ("api/models", "models", "datasets", "spaces", "docs"):
                continue
            if "?pipeline_tag" in mid or "&sort=" in mid:
                continue
            model_ids.add(mid)
            continue

        # Also parse HF model IDs from descriptions like "HF模型: xxx/yyy"
        dm = re.search(r'HF模型[：:]\s*([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)', desc)
        if dm:
            model_ids.add(dm.group(1))

    return sorted(model_ids)


def fetch_model_batch(model_ids: list[str]) -> list[dict]:
    """Batch fetch model metadata from HF API, saving results."""
    results = []
    ok = 0
    for i, mid in enumerate(model_ids):
        print(f"  [{i+1:3d}/{len(model_ids)}] {mid[:55]} ...", end=" ", flush=True)
        try:
            url = f"{HF_API}/models/{mid}"
            resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=TIMEOUT)
            if resp.status_code != 200:
                print(f"SKIP ({resp.status_code})")
                continue
            data = resp.json()

            # config.json
            config = {}
            try:
                cr = requests.get(f"https://huggingface.co/{mid}/raw/main/config.json",
                                  headers=HEADERS, proxies=PROXIES, timeout=10)
                if cr.status_code == 200:
                    config = cr.json()
            except Exception:
                pass

            model_data = {
                "model_id": mid,
                "author": mid.split("/")[0] if "/" in mid else "",
                "pipeline_tag": data.get("pipeline_tag", ""),
                "library_name": data.get("library_name", ""),
                "tags": ",".join(data.get("tags", [])[:15]),
                "downloads": str(data.get("downloads", 0)),
                "likes": str(data.get("likes", 0)),
                "last_modified": data.get("lastModified", ""),
                "private": str(data.get("private", False)).lower(),
                "gated": str(data.get("gated", False)).lower(),
                "architecture_family": "",
                "total_params_b": "",
                "config_json": json.dumps(config, ensure_ascii=False),
                "card_data_json": json.dumps(data.get("cardData", {}) or {}, ensure_ascii=False),
                "api_response_json": json.dumps(data, ensure_ascii=False),
            }

            # Architecture
            arch = "Dense"
            config_str = json.dumps(config).lower() if config else ""
            if "moe" in config_str or "moe" in str(data).lower() or "mixtral" in mid.lower():
                arch = "MoE"
            if config.get("architectures"):
                if "moe" in str(config["architectures"][0]).lower():
                    arch = "MoE"
            model_data["architecture_family"] = arch

            # Params
            num_params = data.get("num_parameters") or data.get("safetensors", {}).get("total", 0)
            if num_params:
                model_data["total_params_b"] = str(round(num_params / 1e9, 1))
            else:
                pm = re.search(r'(\d+\.?\d*)\s*[Bb]', mid)
                if pm:
                    model_data["total_params_b"] = pm.group(1)

            results.append(model_data)
            print(f"OK ({arch} {model_data['total_params_b']}B)")
            ok += 1
        except Exception as e:
            print(f"FAIL: {str(e)[:50]}")
        time.sleep(0.2)

    path = OUTPUT_DIR / "model_data.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  [MODELS] {ok}/{len(model_ids)} fetched -> {path}")
    return results


# ═══════════════════════════════════════════════════════════════
# Pipeline C: Benchmark pages
# ═══════════════════════════════════════════════════════════════

def get_bench_pages(rows: list[dict]) -> list[dict]:
    bench_cats = {"训推测试", "训推测试(国产)", "训推测试(MLPerf)"}
    result = []
    seen = set()
    for r in rows:
        if r["分类"] not in bench_cats:
            continue
        url = r["URL"].strip()
        acc = r.get("可访问", "")
        if "否" in acc and acc.count("是") == 0:
            continue
        if any(b in acc for b in ("HTTP404", "HTTP500", "HTTP502", "HTTP403", "HTTP429")):
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append({
            "url": url, "desc": r["描述"].strip(), "vendor": r["涉及厂商"].strip(),
            "category": r["分类"],
        })
    return result


def crawl_bench_pages(pages: list[dict]) -> str:
    path = OUTPUT_DIR / "bench_pages.jsonl"
    results = []
    ok = 0
    for i, p in enumerate(pages):
        print(f"  [{i+1:3d}/{len(pages)}] {p['desc'][:50]}...", end=" ", flush=True)
        fr = fetch_one(p["url"])
        rec = {**p, **fr, "fetched_at": datetime.now().isoformat(),
               "text_chars": len(fr["text"])}
        if fr["text"]:
            print(f"OK ({len(fr['text'])}c)")
            ok += 1
        else:
            print(f"FAIL: {fr['error'][:50]}")
        results.append(rec)
        time.sleep(0.3)

    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  [BENCH PAGES] {ok}/{len(pages)} succeeded -> {path}")
    return str(path)


# ═══════════════════════════════════════════════════════════════
# Pipeline D: Price pages
# ═══════════════════════════════════════════════════════════════

def get_price_pages(rows: list[dict]) -> list[dict]:
    price_cats = {"价格与出货量", "价格/出货量"}
    result = []
    seen = set()
    for r in rows:
        if r["分类"] not in price_cats:
            continue
        url = r["URL"].strip()
        acc = r.get("可访问", "")
        if "否" in acc and acc.count("是") == 0:
            continue
        if any(b in acc for b in ("HTTP404", "HTTP500", "HTTP502", "HTTP403")):
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append({
            "url": url, "desc": r["描述"].strip(), "vendor": r["涉及厂商"].strip(),
        })
    return result


def crawl_price_pages(pages: list[dict]) -> str:
    path = OUTPUT_DIR / "price_pages.jsonl"
    results = []
    ok = 0
    for i, p in enumerate(pages):
        print(f"  [{i+1:3d}/{len(pages)}] {p['desc'][:50]}...", end=" ", flush=True)
        fr = fetch_one(p["url"])
        rec = {**p, **fr, "fetched_at": datetime.now().isoformat(),
               "text_chars": len(fr["text"])}
        if fr["text"]:
            print(f"OK ({len(fr['text'])}c)")
            ok += 1
        else:
            print(f"FAIL: {fr['error'][:50]}")
        results.append(rec)
        time.sleep(0.3)

    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  [PRICE PAGES] {ok}/{len(pages)} succeeded -> {path}")
    return str(path)


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Master crawl: all 473 links")
    ap.add_argument("--pipe", choices=["chips", "models", "benchmarks", "prices", "all"], default="all")
    ap.add_argument("--max-chips", type=int, default=0, help="Max chip pages to crawl (0=all)")
    ap.add_argument("--max-models", type=int, default=0, help="Max models to fetch (0=all)")
    ap.add_argument("--max-bench", type=int, default=0, help="Max bench pages (0=all)")
    args = ap.parse_args()

    rows = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"[LOAD] {len(rows)} links from CSV\n")

    # Count what's already in DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    existing_chips = {r["chip_model"] for r in conn.execute("SELECT chip_model FROM chips")}
    existing_models = {r["model_id"] for r in conn.execute("SELECT model_id FROM models")}
    conn.close()
    print(f"  Existing DB: {len(existing_chips)} chips, {len(existing_models)} models\n")

    # ── Pipe A: Chip pages ──
    if args.pipe in ("chips", "all"):
        chip_pages = get_chip_pages(rows)
        print(f"\n{'='*60}\nPIPE A: CHIP HARDWARE PAGES ({len(chip_pages)} URLs)\n{'='*60}")
        if args.max_chips > 0:
            chip_pages = chip_pages[:args.max_chips]
        crawl_chip_pages(chip_pages)

    # ── Pipe B: HF Models ──
    if args.pipe in ("models", "all"):
        model_ids = get_model_ids(rows)
        new_ids = [m for m in model_ids if m not in existing_models]
        print(f"\n{'='*60}\nPIPE B: HF MODELS ({len(new_ids)} new, {len(model_ids)} total)\n{'='*60}")
        if args.max_models > 0:
            new_ids = new_ids[:args.max_models]
        results = fetch_model_batch(new_ids)

        # Write to DB
        if results:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            src = {"source_type": "official_datasheet", "source_url": "https://huggingface.co/api",
                   "source_detail": "HuggingFace API batch crawl",
                   "confidence": "high", "is_official": "1",
                   "notes": f"Crawled {datetime.now().isoformat()}"}
            inserted = 0
            for md in results:
                if md["model_id"] in existing_models:
                    continue
                try:
                    add_model(conn, md, src)
                    conn.commit()
                    existing_models.add(md["model_id"])
                    inserted += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  DB ERROR: {md['model_id']} - {e}")
            conn.close()
            print(f"  [DB] {inserted} new models written")

    # ── Pipe C: Benchmark pages ──
    if args.pipe in ("benchmarks", "all"):
        bench_pages = get_bench_pages(rows)
        print(f"\n{'='*60}\nPIPE C: BENCHMARK PAGES ({len(bench_pages)} URLs)\n{'='*60}")
        if args.max_bench > 0:
            bench_pages = bench_pages[:args.max_bench]
        crawl_bench_pages(bench_pages)

    # ── Pipe D: Price pages ──
    if args.pipe in ("prices", "all"):
        price_pages = get_price_pages(rows)
        print(f"\n{'='*60}\nPIPE D: PRICE PAGES ({len(price_pages)} URLs)\n{'='*60}")
        crawl_price_pages(price_pages)

    # Summary
    print(f"\n{'='*60}")
    print(f"DOWNLOAD COMPLETE")
    print(f"  Output dir: {OUTPUT_DIR}")
    for f in sorted(OUTPUT_DIR.glob("*")):
        size = f.stat().st_size
        print(f"    {f.name}: {size:,} bytes")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
