#!/usr/bin/env python3
"""
parse1.db → chip-recommend data.db migration script.

Migrates: chips, models, benchmarks, model_chip_compat
Source: E:\BUPT_PS\P_0\芯片+模型\parse11\parse1\data\parse1.db
Target: e:\BUPT_PS\P_0\chip-recommend\data\data.db

Chip selection:
  - All datacenter chips
  - Consumer chips with release_date >= 2018
  - Consumer chips with benchmark data (regardless of release year)
  - No tier=other unless they have benchmarks

Model selection:
  - All models (including quantized GGUF/GPTQ/AWQ)

Benchmark + compat: full import (with dedup)
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ═══════════════════ Config ═══════════════════
SOURCE_DB = r"E:\BUPT_PS\P_0\芯片+模型\parse11\parse1\data\parse1.db"
TARGET_DB = r"e:\BUPT_PS\P_0\chip-recommend\data\data.db"
BACKUP_DB = r"e:\BUPT_PS\P_0\chip-recommend\data\data.db.backup_20260730"
NOW = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# ═══════════════════ Helpers ═══════════════════

def safe_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=None):
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_str(val, default=""):
    if val is None:
        return default
    return str(val).strip()


def extract_year(release_date):
    """Try to extract year from release_date string."""
    if not release_date:
        return None
    s = str(release_date).strip()
    m = re.search(r"(20\d{2})", s)
    if m:
        return int(m.group(1))
    return None


def is_quantized_model(model_id):
    """Check if a model is a quantized variant."""
    mid = (model_id or "").lower()
    patterns = ["gguf", "gptq", "awq", "-int4", "-int8", "-q4", "-q8", "-q2",
                "-q3", "-q5", "-q6", ".int4", ".int8", "quantized", "quantization",
                "exl2", "hqq", "bnb-4bit", "bitsandbytes-4bit"]
    return any(p in mid for p in patterns)


def guess_quant_bits(model_id):
    """Guess quantization bit-width from model ID."""
    mid = (model_id or "").lower()
    if any(x in mid for x in ["q2", "int2"]):
        return 2
    if any(x in mid for x in ["q3", "int3"]):
        return 3
    if any(x in mid for x in ["q4", "int4", "awq", "bnb-4bit"]):
        return 4
    if any(x in mid for x in ["q5", "int5"]):
        return 5
    if any(x in mid for x in ["q6", "int6"]):
        return 6
    if any(x in mid for x in ["q8", "int8", "gptq"]):
        return 8
    return None


def build_precision_perf(chip_dict):
    """Build precision_perf string from parse1 individual precision columns."""
    parts = []
    fp16_val = safe_float(chip_dict.get("fp16_tflops"))
    bf16_val = safe_float(chip_dict.get("bf16_tflops"))
    fp32_val = safe_float(chip_dict.get("fp32_tflops"))
    fp64_val = safe_float(chip_dict.get("fp64_tflops"))
    fp8_val = safe_float(chip_dict.get("fp8_tflops"))
    fp4_val = safe_float(chip_dict.get("fp4_tflops"))
    int8_val = safe_float(chip_dict.get("int8_tops"))
    int4_val = safe_float(chip_dict.get("int4_tops"))

    if fp64_val:
        parts.append(f"FP64={fp64_val}TFLOPS")
    if fp32_val and fp32_val > 0:
        parts.append(f"FP32={fp32_val}TFLOPS")
    if fp16_val and fp16_val > 0:
        parts.append(f"FP16={fp16_val}TFLOPS")
    if bf16_val and bf16_val > 0:
        parts.append(f"BF16={bf16_val}TFLOPS")
    if fp8_val and fp8_val > 0:
        parts.append(f"FP8={fp8_val}TFLOPS")
    if fp4_val and fp4_val > 0:
        parts.append(f"FP4={fp4_val}TFLOPS")
    if int8_val and int8_val > 0:
        parts.append(f"INT8={int8_val}TOPS")
    if int4_val and int4_val > 0:
        parts.append(f"INT4={int4_val}TOPS")

    return "; ".join(parts)


def build_precision_support(chip_dict):
    """Build precision_support string from individual columns."""
    supported = []
    if safe_float(chip_dict.get("fp64_tflops")) and safe_float(chip_dict.get("fp64_tflops")) > 0:
        supported.append("FP64")
    if safe_float(chip_dict.get("fp32_tflops")) and safe_float(chip_dict.get("fp32_tflops")) > 0:
        supported.append("FP32")
    if safe_float(chip_dict.get("fp16_tflops")) and safe_float(chip_dict.get("fp16_tflops")) > 0:
        supported.append("FP16")
    if safe_float(chip_dict.get("bf16_tflops")) and safe_float(chip_dict.get("bf16_tflops")) > 0:
        supported.append("BF16")
    if safe_float(chip_dict.get("fp8_tflops")) and safe_float(chip_dict.get("fp8_tflops")) > 0:
        supported.append("FP8")
    if safe_float(chip_dict.get("fp4_tflops")) and safe_float(chip_dict.get("fp4_tflops")) > 0:
        supported.append("FP4")
    if safe_float(chip_dict.get("int8_tops")) and safe_float(chip_dict.get("int8_tops")) > 0:
        supported.append("INT8")
    if safe_float(chip_dict.get("int4_tops")) and safe_float(chip_dict.get("int4_tops")) > 0:
        supported.append("INT4")
    return "/".join(supported) if supported else ""


def determine_production_status(release_date, tier):
    """Infer production_status from release_date."""
    year = extract_year(release_date)
    if not year:
        return ""
    if year <= 2024:
        return "已量产"
    elif year == 2025:
        return "已发布"
    elif year >= 2026:
        return "待发布"
    return ""


def determine_is_released(release_date):
    """1 if release_date <= now."""
    year = extract_year(release_date)
    if not year:
        return "0"
    if year <= 2025:
        return "1"
    return "0"


def parse_chip_model_name(name):
    """Clean chip_model name for display."""
    if not name:
        return ""
    # Remove trailing metadata
    name = re.sub(r'\s*\([^)]*\d+[^)]*\)\s*$', '', name)
    return name.strip()


# ═══════════════════ Main Migration ═══════════════════

def main():
    print("=" * 60)
    print("parse1.db → chip-recommend data.db Migration")
    print("=" * 60)

    # ── Backup ──
    if os.path.exists(TARGET_DB) and not os.path.exists(BACKUP_DB):
        shutil.copy2(TARGET_DB, BACKUP_DB)
        print(f"[✓] Backed up to {BACKUP_DB}")
    else:
        print(f"[i] Skipping backup (already exists or no source)")

    src = sqlite3.connect(SOURCE_DB)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(TARGET_DB)
    dst.row_factory = sqlite3.Row

    # ═══════════════════════════════════════
    # PHASE 1: Identify chips to import
    # ═══════════════════════════════════════

    print("\n── Phase 1: Chip selection ──")

    # Get existing chips in target
    existing_chips = set(
        r["chip_model"].strip()
        for r in dst.execute("SELECT chip_model FROM chips").fetchall()
        if r["chip_model"]
    )
    print(f"  Existing chips in target: {len(existing_chips)}")

    # Get benchmark hardware_sku that are real chip names (not cluster configs)
    bench_rows = src.execute(
        "SELECT DISTINCT hardware_sku FROM benchmarks WHERE hardware_sku != ''"
    ).fetchall()
    bench_chip_names = set()
    cluster_pattern = re.compile(
        r'^\d+x|^[a-f0-9]{8,}|node|reference|cpu|ctuning|mlcommons|default_config|SuperPoD|Cluster$'
    )
    for r in bench_rows:
        name = r["hardware_sku"].strip()
        if name and not cluster_pattern.search(name.lower()):
            bench_chip_names.add(name)
    print(f"  Unique benchmark chip names: {len(bench_chip_names)}")

    # Select chips from parse1
    parse1_chips = src.execute(
        "SELECT * FROM chips WHERE chip_model IS NOT NULL AND chip_model != ''"
    ).fetchall()
    print(f"  Total parse1 chips: {len(parse1_chips)}")

    selected_chips = []
    selected_models = set()
    skipped_tiers = set()
    skip_count = 0

    # Build a fuzzy-match lookup for existing chips
    import unicodedata
    def _normalize(name):
        s = str(name or "").strip()
        s = re.sub(r'[()（）\s]+', '', s).lower()
        return s

    existing_norm = {_normalize(n): n for n in existing_chips}

    for row in parse1_chips:
        d = dict(row)
        model = safe_str(d.get("chip_model"))
        tier = safe_str(d.get("tier"))
        year = extract_year(d.get("release_date"))

        # Fuzzy match against existing chips
        model_norm = _normalize(model)
        if model_norm in existing_norm:
            continue
        # Also check: does the normalized name appear as substring in any existing?
        found_in_existing = False
        for en, en_orig in existing_norm.items():
            if model_norm in en or en in model_norm:
                # Also verify number overlap
                src_nums = re.findall(r'\d+', model)
                tgt_nums = re.findall(r'\d+', en_orig)
                if src_nums and tgt_nums and src_nums[0] == tgt_nums[0]:
                    found_in_existing = True
                    break
        if found_in_existing:
            continue

        # Selection rules
        keep = False
        reason = ""

        if tier == "datacenter":
            keep = True
            reason = "datacenter"
        elif tier in ("consumer", "consumer_high") and year and year >= 2018:
            keep = True
            reason = f"consumer_{year}"
        elif tier in ("consumer", "consumer_high") and model in bench_chip_names:
            keep = True
            reason = "consumer_with_benchmark"
        elif model in bench_chip_names:
            keep = True
            reason = "has_benchmark"
        elif tier == "other" and model in bench_chip_names:
            keep = True
            reason = "other_with_benchmark"

        if keep:
            selected_chips.append((d, reason))
            selected_models.add(model)
        else:
            skipped_tiers.add((tier, year or 0))
            skip_count += 1

    print(f"  Selected for import: {len(selected_chips)} chips")
    print(f"  Skipped: {skip_count}")
    print(f"  Reasons: {', '.join(set(r for _, r in selected_chips))}")

    # ═══════════════════════════════════════
    # PHASE 2: Import chips
    # ═══════════════════════════════════════

    print("\n── Phase 2: Importing chips ──")

    # Map parse1 columns → recommend columns
    # Recommend project specific columns (78 total)
    REC_CHIP_COLS = [
        "vendor", "vendor_display", "vendor_region", "chip_series", "chip_model",
        "chip_type", "usage", "tier",
        "architecture", "arch_codename", "generation", "process_node_nm",
        "foundry", "die_size_mm2", "transistors_b", "package_type", "is_chiplet",
        "vram_gb", "vram_type", "vram_bus_bit", "vram_bw_gb_s", "vram_clock_mhz",
        "compute_units", "tensor_cores", "rt_cores", "shading_units", "sm_count",
        "l1_cache_kb", "l2_cache_mb", "on_chip_sram_mb",
        "precision_support", "precision_perf",
        "base_clock_mhz", "boost_clock_mhz", "tdp_w", "max_power_w",
        "psu_w", "power_connector", "board_length_mm", "board_width_mm",
        "slot_width", "form_factor", "bus_interface",
        "interconnect_bw_gb_s", "interconnect_tech", "network_interface",
        "software_stack", "compatible_frameworks",
        "release_date", "production_status", "eol_date", "target_market",
        "is_released", "expected_release_date", "known_specs", "unconfirmed_items",
        "price_usd", "price_cny_wan", "price_period", "price_notes",
        "description", "highlights", "limitations",
        "target_workloads", "typical_deployment", "competitor_comparison",
        "ecosystem_notes", "maturity_level", "framework_compat", "sw_stack",
        "cuda_compat", "cloud_available", "cluster_scale",
        "key_strength", "key_weakness",
        "created_at", "updated_at",
    ]

    chip_insert_count = 0
    chip_errors = 0

    for chip_dict, reason in selected_chips:
        try:
            # Map fields
            mapped = {}
            for col in REC_CHIP_COLS:
                if col in chip_dict:
                    mapped[col] = safe_str(chip_dict.get(col))
                elif col == "precision_perf":
                    mapped[col] = build_precision_perf(chip_dict)
                elif col == "precision_support":
                    mapped[col] = build_precision_support(chip_dict)
                elif col == "production_status":
                    mapped[col] = determine_production_status(
                        chip_dict.get("release_date"), chip_dict.get("tier")
                    )
                elif col == "is_released":
                    mapped[col] = determine_is_released(chip_dict.get("release_date"))
                elif col == "vendor_region":
                    mapped[col] = safe_str(chip_dict.get("vendor_region", "foreign"))
                elif col == "created_at":
                    mapped[col] = safe_str(chip_dict.get("created_at", NOW))
                elif col == "updated_at":
                    mapped[col] = safe_str(chip_dict.get("updated_at", NOW))
                elif col == "price_cny_wan":
                    # Check chip_price table
                    mapped[col] = safe_str(chip_dict.get("price_cny_wan"))
                elif col == "description":
                    mapped[col] = safe_str(chip_dict.get("non_standard_info"))
                elif col == "usage":
                    usage = safe_str(chip_dict.get("usage"))
                    if not usage:
                        tier_val = safe_str(chip_dict.get("tier"))
                        if tier_val == "datacenter":
                            usage = "推理"
                    mapped[col] = usage
                elif col == "tier":
                    tier_val = safe_str(chip_dict.get("tier"))
                    if tier_val in ("consumer", "consumer_high"):
                        mapped[col] = "consumer"
                    elif tier_val == "other":
                        mapped[col] = "consumer"
                    else:
                        mapped[col] = tier_val
                elif col == "chip_type":
                    ctype = safe_str(chip_dict.get("chip_type"))
                    if not ctype:
                        ctype = "GPU"
                    mapped[col] = ctype
                else:
                    mapped[col] = ""

            # Build INSERT
            columns = list(mapped.keys())
            placeholders = ["?"] * len(columns)
            values = [mapped[c] for c in columns]

            sql = f'INSERT INTO chips ({", ".join(columns)}) VALUES ({", ".join(placeholders)})'
            dst.execute(sql, values)
            chip_insert_count += 1

        except Exception as e:
            chip_errors += 1
            if chip_errors <= 5:
                print(f"  [ERROR] Failed to insert {chip_dict.get('chip_model', '?')[:50]}: {e}")
            # Print first error in detail
            if chip_errors == 1:
                import traceback
                traceback.print_exc()

    dst.commit()
    print(f"  Inserted: {chip_insert_count}, Errors: {chip_errors}")

    # ═══════════════════════════════════════
    # PHASE 3: Import models (with quantized support)
    # ═══════════════════════════════════════

    print("\n── Phase 3: Importing models ──")

    existing_models = set(
        r["model_id"].strip()
        for r in dst.execute("SELECT model_id FROM models").fetchall()
        if r["model_id"]
    )
    print(f"  Existing models: {len(existing_models)}")

    parse1_models = src.execute(
        "SELECT * FROM models WHERE model_id IS NOT NULL AND model_id != ''"
    ).fetchall()
    print(f"  Parse1 models: {len(parse1_models)}")

    model_insert_count = 0
    quantized_count = 0
    model_skip_count = 0

    for row in parse1_models:
        md = dict(row)
        model_id = safe_str(md.get("model_id"))

        if model_id in existing_models:
            model_skip_count += 1
            continue

        is_quant = is_quantized_model(model_id)
        if is_quant:
            quantized_count += 1

        try:
            # Build training_methods: quantized models can't do full training
            training_methods_raw = safe_str(bd.get("training_methods") or "")
            if is_quant:
                train_methods = "inference_only"
            elif training_methods_raw:
                # parse1 stores as JSON array like ["full", "SFT", "LoRA"]
                train_methods = training_methods_raw.replace('[', '').replace(']', '').replace('"', '')
                if not train_methods.strip():
                    train_methods = "full,SFT,LoRA,QLoRA"
            else:
                train_methods = "full,SFT,LoRA,QLoRA"

            # Build framework list
            fw_labels = safe_str(bd.get("framework_labels") or "")
            if not fw_labels or fw_labels == "δ未注":
                fw_labels = "transformers"

            # Build tags
            tags_parts = []
            if fw_labels:
                tags_parts.append(fw_labels.replace(", ", ","))
            if is_quant:
                tags_parts.append(f"quantized,{guess_quant_bits(model_id) or '?'}bit")
            pipeline = safe_str(bd.get("pipeline_tag") or "text-generation")
            tags_parts.append(pipeline)
            tags = ",".join(tags_parts)

            # Determine architecture
            arch = safe_str(bd.get("architecture_family") or "")
            if not arch:
                model_type = safe_str(bd.get("model_type") or "")
                if model_type == "LLM":
                    arch = "Dense"
                elif model_type == "VLM":
                    arch = "Dense"
                elif model_type == "Embedding":
                    arch = "Dense"

            # Build config_json with parse1-specific fields
            config = {}
            config["hidden_size"] = safe_float(bd.get("hidden_size"))
            config["num_layers"] = safe_float(bd.get("num_layers"))
            config["num_heads"] = safe_float(bd.get("num_heads"))
            config["num_kv_heads"] = safe_float(bd.get("num_kv_heads"))
            config["context_length"] = safe_float(bd.get("context_length"))
            config["num_experts"] = safe_float(bd.get("num_experts"))
            config["is_quantized"] = is_quant
            config["quant_bits"] = guess_quant_bits(model_id) if is_quant else None
            config["can_train"] = not is_quant
            config["training_methods"] = train_methods
            config_json = json.dumps({k: v for k, v in config.items() if v is not None and v != ""},
                                     ensure_ascii=False)

            dst.execute(
                """INSERT INTO models (
                    model_id, author, pipeline_tag, library_name, tags,
                    downloads, likes, last_modified, private, gated,
                    architecture_family, total_params_b,
                    config_json, card_data_json, api_response_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_id,
                    safe_str(bd.get("author") or bd.get("family")),
                    pipeline,
                    "transformers",
                    tags,
                    safe_str(bd.get("downloads") or "0"),
                    safe_str(bd.get("likes") or "0"),
                    safe_str(bd.get("last_modified") or NOW),
                    "false",
                    "false",
                    arch,
                    safe_str(bd.get("total_params_b") or "0"),
                    config_json,
                    "{}",
                    json.dumps({"_source": "parse1_migration"}, ensure_ascii=False),
                    safe_str(bd.get("created_at", NOW)),
                    safe_str(bd.get("updated_at", NOW)),
                ),
            )
            model_insert_count += 1

        except Exception as e:
            if model_insert_count < 5:
                print(f"  [ERROR] Model {model_id[:60]}: {e}")

    dst.commit()
    print(f"  Inserted: {model_insert_count} (including {quantized_count} quantized)")
    print(f"  Skipped (already exist): {model_skip_count}")

    # ═══════════════════════════════════════
    # PHASE 4: Import benchmarks
    # ═══════════════════════════════════════

    print("\n── Phase 4: Importing benchmarks ──")

    existing_bench_ids = set()
    for r in dst.execute("SELECT chip_model, model_id, suite_name FROM chip_model_benchmarks").fetchall():
        rd = dict(r)
        key = (safe_str(rd["chip_model"]), safe_str(rd["model_id"]), safe_str(rd.get("suite_name") or ""))
        existing_bench_ids.add(key)

    parse1_benches = src.execute("SELECT * FROM benchmarks WHERE hardware_sku != '' AND model_id != ''").fetchall()
    print(f"  Parse1 benchmarks: {len(parse1_benches)}")

    bench_insert_count = 0
    bench_skip_count = 0

    for row in parse1_benches:
        bd = dict(row)
        hw = safe_str(bd["hardware_sku"])
        mid = safe_str(bd["model_id"])
        suite = safe_str(bd.get("source") or "community")
        key = (hw, mid, suite)

        if key in existing_bench_ids:
            bench_skip_count += 1
            continue

        try:
            dst.execute(
                """INSERT INTO chip_model_benchmarks (
                    chip_model, model_id, suite_name, workload_type, scenario, task,
                    hardware_config, chip_count, framework, precision,
                    batch_size, input_seq_length, output_seq_length, concurrency,
                    prefill_throughput, decode_throughput,
                    time_to_first_token_ms, inter_token_latency_ms,
                    memory_peak_mb, throughput_tok_s, throughput_samples_s, tpot_ms,
                    mfu_pct, gpu_hours, training_tokens_T,
                    training_gpu_count, training_workload_type,
                    test_date, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hw,  # chip_model
                    mid,  # model_id
                    suite,  # suite_name
                    safe_str(bd.get("workload_type") or "inference"),
                    safe_str(bd.get("scenario") or ""),
                    safe_str(bd.get("task") or ""),
                    safe_str(bd.get("hardware_sku") or ""),  # hardware_config
                    safe_str(bd.get("chip_count") or bd.get("gpu_count") or "1"),
                    safe_str(bd.get("framework") or ""),
                    safe_str(bd.get("precision") or ""),
                    safe_str(bd.get("batch_size") or ""),
                    safe_str(bd.get("input_tokens") or ""),
                    safe_str(bd.get("output_tokens") or ""),
                    safe_str(bd.get("concurrency") or ""),
                    "",  # prefill_throughput
                    "",  # decode_throughput
                    safe_str(bd.get("ttft_ms") or ""),
                    "",  # inter_token_latency_ms
                    "",  # memory_peak_mb
                    safe_str(bd.get("throughput_tok_s") or ""),
                    safe_str(bd.get("throughput_samples_s") or ""),
                    safe_str(bd.get("tpot_ms") or ""),
                    safe_str(bd.get("mfu_pct") or ""),
                    safe_str(bd.get("gpu_hours") or ""),
                    safe_str(bd.get("training_tokens_T") or ""),
                    safe_str(bd.get("training_gpu_count") or ""),
                    safe_str(bd.get("training_workload") or ""),
                    safe_str(bd.get("test_date") or ""),
                    safe_str(bd.get("notes") or safe_str(bd.get("citation") or "")),
                    safe_str(bd.get("created_at", NOW)),
                ),
            )
            bench_insert_count += 1
            existing_bench_ids.add(key)

        except Exception as e:
            if bench_insert_count < 5:
                print(f"  [ERROR] Benchmark {hw[:30]} x {mid[:30]}: {e}")

    dst.commit()
    print(f"  Inserted: {bench_insert_count}, Skipped: {bench_skip_count}")

    # ═══════════════════════════════════════
    # PHASE 5: Import compatibilities
    # ═══════════════════════════════════════

    print("\n── Phase 5: Importing compatibilities ──")

    existing_compat = set()
    for r in dst.execute("SELECT chip_model, model_id FROM chip_model_compatibility").fetchall():
        existing_compat.add((safe_str(r["chip_model"]), safe_str(r["model_id"])))

    parse1_compat = src.execute(
        "SELECT * FROM model_chip_compat WHERE chip_model != '' AND model_name != ''"
    ).fetchall()
    print(f"  Parse1 compat: {len(parse1_compat)}")

    compat_insert_count = 0
    compat_skip_count = 0

    for row in parse1_compat:
        cd = dict(row)
        cm = safe_str(cd["chip_model"])
        mn = safe_str(cd["model_name"])
        if (cm, mn) in existing_compat:
            compat_skip_count += 1
            continue

        try:
            dst.execute(
                """INSERT INTO chip_model_compatibility (
                    chip_model, model_id, compat_status, framework, precision,
                    verified_at, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cm,
                    mn,
                    safe_str(bd.get("compat_status") or "vendor_claimed"),
                    safe_str(bd.get("framework") or ""),
                    safe_str(bd.get("precision") or ""),
                    safe_str(bd.get("verified_at") or ""),
                    safe_str(bd.get("notes") or ""),
                    safe_str(bd.get("created_at", NOW)),
                ),
            )
            compat_insert_count += 1

        except Exception as e:
            pass

    dst.commit()
    print(f"  Inserted: {compat_insert_count}, Skipped: {compat_skip_count}")

    # ═══════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════

    print("\n" + "=" * 60)
    print("MIGRATION COMPLETE")
    print("=" * 60)

    chips_final = dst.execute("SELECT COUNT(*) FROM chips").fetchone()[0]
    models_final = dst.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    bench_final = dst.execute("SELECT COUNT(*) FROM chip_model_benchmarks").fetchone()[0]
    compat_final = dst.execute("SELECT COUNT(*) FROM chip_model_compatibility").fetchone()[0]

    print(f"  chips:     {chips_final}")
    print(f"  models:    {models_final}")
    print(f"  benchmarks:{bench_final}")
    print(f"  compat:    {compat_final}")

    src.close()
    dst.close()

    print("\nDone! Restart the server to see changes.")


if __name__ == "__main__":
    main()
