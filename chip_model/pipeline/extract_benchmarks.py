#!/usr/bin/env python3
"""Extract benchmark data from crawled benchmark pages and insert into DB.

Usage:
    python extract_benchmarks.py              # process all
    python extract_benchmarks.py --dry-run    # preview only
    python extract_benchmarks.py --max 5      # limit pages
"""

import argparse, json, re, sqlite3, sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent.parent
BENCH_FILE = HERE / "data" / "crawl_all" / "bench_pages.jsonl"
DB_PATH = HERE / "data" / "parse1.db"

from chip_model.database import add_benchmark

NOW = datetime.now().isoformat(timespec="seconds")


def load_bench_pages() -> list[dict]:
    if not BENCH_FILE.exists():
        return []
    pages = []
    with open(BENCH_FILE, "r", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("text_chars", 0) > 100:
                pages.append(p)
    return pages


def extract_benchmarks(page: dict) -> list[dict]:
    """Extract benchmark records from a crawled page. Returns list of dicts."""
    text = page.get("text", "")
    desc = page.get("desc", "")
    url = page.get("url", "")
    vendor = page.get("vendor", "")

    results = []

    # ── Identify chip-model pairs ──
    # Known chip patterns
    chip_map = {
        "H100": "H100 SXM5 80GB", "A100": "A100 SXM4 80GB",
        "B200": "B200 SXM 192GB", "H200": "H200 SXM 141GB",
        "MI300X": "Instinct MI300X 192GB", "MI300": "Instinct MI300X 192GB",
        "910B": "昇腾910B B1 (64GB)", "昇腾910B": "昇腾910B B1 (64GB)",
        "910C": "昇腾910C (OAM 128GB)", "昇腾910C": "昇腾910C (OAM 128GB)",
        "MLU590": "MLU590 (80GB)", "C500": "曦云C500 (OAM 64GB HBM2e)",
        "BR100": "BR100 (壁砺100) (64GB HBM2e)",
        "C600": "曦云C600 (144GB HBM3e)",
        "Gaudi": "Gaudi 3 128GB",
        "B300": "B300 NVL16 288GB",
        "MI350": "Instinct MI350X 288GB",
    }

    model_map = {
        "Qwen2.5-7B": "Qwen/Qwen2.5-7B-Instruct",
        "Qwen3-8B": "Qwen/Qwen3-8B",
        "Qwen3-32B": "Qwen/Qwen3-32B",
        "Qwen2.5-72B": "Qwen/Qwen2.5-72B-Instruct",
        "Llama-3.1-70B": "meta-llama/Llama-3.1-70B-Instruct",
        "Llama-3.1-8B": "meta-llama/Llama-3.1-8B-Instruct",
        "DeepSeek-R1": "deepseek-ai/DeepSeek-R1",
        "DeepSeek-V3": "deepseek-ai/DeepSeek-V3",
        "DeepSeek-V4": "deepseek-ai/DeepSeek-V4-Flash",
        "Llama-7B": "meta-llama/Llama-3.1-8B-Instruct",
        "Llama-2-70B": "meta-llama/Llama-3.1-70B-Instruct",
        "Gemma": "google/gemma-4-31B-it",
        "Mixtral-8x7B": "mistralai/Mixtral-8x22B-Instruct-v0.1",
        "GPT-OSS-120B": "openai/gpt-oss-120b",
    }

    # Find mentioned chips
    found_chips = []
    for key, chip_model in chip_map.items():
        if re.search(key, desc + " " + text[:2000], re.IGNORECASE):
            found_chips.append(chip_model)

    # Find mentioned models
    found_models = []
    for key, model_id in model_map.items():
        if re.search(key, desc + " " + text[:2000], re.IGNORECASE):
            found_models.append(model_id)

    if not found_chips or not found_models:
        # Try generic match
        chip_match = re.findall(r'(H100|A100|B200|H200|B300|910B|910C|MI300X|MI350)', desc + " " + text[:500], re.IGNORECASE)
        model_match = re.findall(r'(Qwen[\w.-]+|Llama[\w.-]+|DeepSeek[\w.-]+|Gemma[\w.-]+|Mixtral[\w.-]+)', desc + " " + text[:500], re.IGNORECASE)
        for c in chip_match:
            if c in chip_map:
                found_chips.append(chip_map[c])
        for m in model_match:
            if m in model_map:
                found_models.append(model_map[m])

    found_chips = list(dict.fromkeys(found_chips))  # dedup
    found_models = list(dict.fromkeys(found_models))

    if not found_chips or not found_models:
        return []

    # ── Extract metrics ──
    def find(text, pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1) if m else None

    throughput = find(text, r'(?:throughput|吞吐).*?(\d+\.?\d*)\s*(?:tok|token)/s') or \
                 find(text, r'(\d+\.?\d*)\s*(?:tok|token)/s')

    ttft = find(text, r'(?:TTFT|首token|first.*?token).*?(\d+\.?\d*)\s*(?:ms|秒)')
    tpot = find(text, r'(?:TPOT|inter.*?token|per.*?token).*?(\d+\.?\d*)\s*(?:ms)')
    mfu = find(text, r'(?:MFU).*?(\d+\.?\d*)\s*%')
    memory = find(text, r'(?:显存|memory|VRAM).*?(\d+\.?\d*)\s*(?:MB|GB)')

    # Determine workload and test suite
    if "training" in desc.lower() or "训练" in desc or "train" in desc.lower():
        workload = "training"
        suite = "community"
    elif "MLPerf" in desc or "mlperf" in url:
        workload = "inference" if "inference" in desc.lower() else "training"
        suite = "MLPerf"
    else:
        workload = "inference"
        suite = "community"

    # Detect scenario
    if "serving" in desc.lower() or "interactive" in desc.lower() or "online" in desc.lower():
        scenario = "serving"
    elif "offline" in desc.lower():
        scenario = "offline"
    elif "training" in desc.lower() or "训练" in desc:
        scenario = "training"
    else:
        scenario = "serving"

    # Precision
    prec = "FP16"
    if "fp8" in desc.lower() or "FP8" in desc:
        prec = "FP8"
    elif "bf16" in desc.lower() or "BF16" in desc:
        prec = "BF16"
    elif "int8" in desc.lower() or "INT8" in desc:
        prec = "INT8"
    elif "fp4" in desc.lower() or "FP4" in desc:
        prec = "FP4"

    # Source type
    if "mlperf" in desc.lower() or "mlperf" in url:
        src_type = "benchmark_suite"
        confidence = "high"
        is_official = "1"
    elif "vendor" in desc.lower() or vendor in ("Huawei", "NVIDIA", "AMD", "Intel"):
        src_type = "vendor_doc"
        confidence = "medium"
        is_official = "1"
    else:
        src_type = "community"
        confidence = "medium"
        is_official = "0"

    for chip_model in found_chips:
        for model_id in found_models:
            bm = {
                "chip_model": chip_model,
                "model_id": model_id,
                "suite_name": suite,
                "workload_type": workload,
                "scenario": scenario,
                "chip_count": find(text, r'(\d+)\s*(?:卡|card|GPU|chip|device)'),
                "framework": "vLLM" if "vllm" in desc.lower() else (
                    "MindSpore" if "mindspore" in desc.lower() else (
                    "TensorRT-LLM" if "tensorrt" in desc.lower() else (
                    "PyTorch" if "pytorch" in desc.lower() else None))),
                "precision": prec,
                "batch_size": find(text, r'(?:batch|batch_size).*?(\d+)'),
                "input_seq_length": find(text, r'(?:input|输入).*?(\d+)\s*(?:token)?'),
                "output_seq_length": find(text, r'(?:output|输出).*?(\d+)\s*(?:token)?'),
                "throughput_tok_s": throughput,
                "time_to_first_token_ms": ttft,
                "inter_token_latency_ms": tpot,
                "tpot_ms": tpot,
                "mfu_pct": mfu,
                "memory_peak_mb": memory,
                "test_date": find(text, r'(20\d{2}[-/]\d{2}[-/]\d{2})'),
            }
            results.append(bm)

    return results


def insert_benchmark(bm: dict, source_url: str) -> bool:
    """Insert one benchmark record."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    source = {
        "source_type": "benchmark_suite" if bm["suite_name"] == "MLPerf" else (
            "vendor_doc" if bm["suite_name"] == "vendor_doc" else "community"),
        "source_url": source_url,
        "source_detail": f"Extracted from crawled benchmark page",
        "confidence": "high" if bm["suite_name"] == "MLPerf" else "medium",
        "is_official": "1" if bm["suite_name"] in ("MLPerf", "vendor_doc") else "0",
        "notes": f"Auto-extracted {NOW}",
    }

    # Deduplicate
    existing = db.execute(
        "SELECT id FROM chip_model_benchmarks WHERE chip_model = ? AND model_id = ? AND workload_type = ?",
        (bm["chip_model"], bm["model_id"], bm["workload_type"]),
    ).fetchone()
    if existing:
        db.close()
        return False

    try:
        rid = add_benchmark(db, bm, source)
        db.commit()
        print(f"  ADDED [{rid}] {bm['chip_model'][:25]} x {bm['model_id'][:30]} ({bm['workload_type']})")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max", type=int, default=0)
    args = parser.parse_args()

    pages = load_bench_pages()
    print(f"[LOAD] {len(pages)} benchmark pages with content\n")

    if args.max > 0:
        pages = pages[:args.max]

    inserted = 0
    skipped = 0
    for i, page in enumerate(pages):
        desc = page.get("desc", "")[:80]
        records = extract_benchmarks(page)
        if not records:
            continue

        print(f"[{i+1}/{len(pages)}] {desc}... ({len(records)} records)")
        for bm in records:
            if args.dry_run:
                print(f"  PREVIEW: {bm['chip_model'][:25]} x {bm['model_id'][:30]} "
                      f"throughput={bm.get('throughput_tok_s', '-')}")
                skipped += 1
            else:
                if insert_benchmark(bm, page.get("url", "")):
                    inserted += 1
                else:
                    skipped += 1

    print(f"\n[DONE] inserted={inserted}  skipped={skipped}")

    # Show totals
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    cnt = db.execute("SELECT COUNT(*) as n FROM chip_model_benchmarks").fetchone()["n"]
    print(f"[DB] Total benchmarks: {cnt}")
    db.close()


if __name__ == "__main__":
    main()
