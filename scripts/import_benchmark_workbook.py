"""Archive every nonempty row in the 20260713 evaluation workbook.

Inference observations are also normalized.  Only sheets with explicit Flash
model evidence and verified physical card counts are imported as model-specific
benchmarks; unknown conditions stay in the archive instead of being invented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import add_benchmark, get_db, get_db_path, init_db
from scripts.import_deepseek_v4_flash_benchmarks import import_reference

WORKBOOK = ROOT / "推理测试明细数据_20260713.xlsx"
SOURCE_URL = "https://www.kdocs.cn/l/cls1VM1kgBsC"
FLASH = "deepseek-ai/DeepSeek-V4-Flash"
MANIFEST = ROOT / "data" / "deepseek_v4_flash_reference_20260713.json"

ALIASES = {
    "input": ("输入长度", "输入", "input len", "target_input_len", "target_n_input"),
    "output": ("输出长度", "输出", "output len", "target_output_len", "target_n_output"),
    "concurrency": ("concurrency", "并发", "target_concurrency"),
    "throughput": ("输出token吞吐/s", "输出tok/s", "output tok/s", "output_throughput_tok_s", "输出token吞吐", "输出token/s"),
    "ttft": ("ttft", "mean_ttft_ms"),
    "tpot": ("tpot", "mean_tpot_ms"),
    "requests": ("request_count", "target_num_prompts", "target_num_prompts", "请求数"),
    "req_s": ("每秒完成请求数", "每秒请求数", "request_throughput_req_s", "req/s", "请求吞吐"),
    "total_tps": ("总token吞吐/s", "总tok/s", "total tok/s", "total_throughput_tok_s"),
}


def _cell(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _norm(value):
    return re.sub(r"\s+", "", str(value or "").lower())


def _headers(values):
    best, best_score = {}, 0
    for row in values[:7]:
        found = {}
        for i, heading in enumerate(row):
            label = _norm(heading)
            for key, variants in ALIASES.items():
                if any(label == _norm(v) or label.startswith(_norm(v) + "(") for v in variants):
                    found.setdefault(key, i)
        score = sum(key in found for key in ("input", "output", "concurrency", "throughput"))
        if score > best_score:
            best, best_score = found, score
    return best if best_score >= 3 else {}


def _number(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _observations(values, row, headers):
    if not all(k in headers for k in ("input", "output", "concurrency", "throughput")):
        return None
    picked = {key: row[index] if index < len(row) else None for key, index in headers.items()}
    if not all(_number(picked.get(k)) for k in ("input", "output", "concurrency", "throughput")):
        return None
    return picked


def _sheet_hints(sheet, manifest, adapter_cards):
    for item in manifest["measurements"]:
        if item["source_sheet"] == sheet:
            return {
                "chip_model": item["chip_model"], "model_id": FLASH,
                "physical_cards": item["physical_cards"], "nodes": item["nodes"],
                "deployment": item.get("hardware_config"), "precision": item.get("precision"),
                "confidence": item.get("confidence", "medium"),
            }
    if "寒武纪MLU590-M9DK性能测试" == sheet:
        return {"model_id": FLASH, "chip_model": "MLU590 80GB",
                "physical_cards": adapter_cards["寒武纪MLU590-M9DK性能测试"],
                "model_evidence": "适配性测试!B2,AO2", "card_evidence": "适配性测试!AQ4:AR4",
                "confidence": "medium", "deployment": "vLLM 0.1.dev MLU; TP8 MP; MTP spec=1",
                "precision": "W8A8"}
    if "寒武纪MLU590-M9DK dsv4-pro性能测试" == sheet:
        return {"model_id": "deepseek-ai/DeepSeek-V4-Pro",
                "chip_model": "MLU590 80GB", "physical_cards": 32, "nodes": 4,
                "deployment": "vLLM TP32 EP32 Ray", "precision": "W4A8"}
    if "昇腾910C（4物理卡）" == sheet:
        return {"chip_model": "昇腾910C", "physical_cards": 4, "model_id": FLASH,
                "model_evidence": "适配性测试!B2,AC2", "card_evidence": "工作表标题", "confidence": "medium"}
    if "昇腾910C(8物理卡)" == sheet:
        return {"chip_model": "昇腾910C", "physical_cards": 8, "model_id": FLASH,
                "model_evidence": "适配性测试!B2,AC2", "card_evidence": "工作表标题", "confidence": "medium"}
    chip_hints = {
        "PPUZW810E性能测试": "真武810E (PPU)",
        "海光BW1000性能测试": "深算三号 BW1000/BW100 (2025)",
        "910B性能测试": "昇腾910B",
        "H200性能测试": "H200 (具体形态未注明)",
        "沐曦C550性能测试": "C550",
        "沐曦C550(浪潮)性能测试": "C550",
        "摩尔S5000性能测试": "MTT S5000 (OAM)",
        "PPUZW80E_多机多卡": "真武810E (PPU)（型号待核实）",
        "昆仑芯P800性能测试": "P800 (OAM)",
        "Qwen3.8-27B 910B压测": "昇腾910B",
        "Qwen3.8-27B 910B MTP": "昇腾910B",
        "910B能耗测试": "昇腾910B",
    }
    if sheet in chip_hints:
        hints = {"chip_model": chip_hints[sheet]}
        if sheet in adapter_cards:
            hints.update({"model_id": FLASH, "physical_cards": adapter_cards[sheet],
                          "model_evidence": "适配性测试!B2及对应芯片列",
                          "card_evidence": "适配性测试!第4行（整机输出吞吐÷单卡输出吞吐）",
                          "confidence": "medium"})
        if sheet.startswith("Qwen3.8-27B"):
            hints["model_id"] = "Qwen3.8-27B（表内名称，未关联模型库）"
        return hints
    return {}


def _kind(sheet, observation):
    if observation:
        return "inference_measurement"
    if "适配性" in sheet:
        return "compatibility_or_quality"
    if "能耗" in sheet:
        return "energy_measurement_or_note"
    if "对比" in sheet:
        return "comparison_or_note"
    return "note_or_header"


def import_workbook(workbook=WORKBOOK, db_path=None, dry_run=False):
    workbook = Path(workbook)
    sha = hashlib.sha256(workbook.read_bytes()).hexdigest()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if workbook.name == WORKBOOK.name and sha != manifest["source_sha256"]:
        raise ValueError("workbook differs from the reviewed source; inspect before importing")
    wb = load_workbook(workbook, read_only=True, data_only=True)
    formula_wb = load_workbook(workbook, read_only=True, data_only=False)
    adapter = list(wb["适配性测试"].values)
    assert _norm(adapter[1][1]) == "deepseek-v4-flash"
    adapter_output_columns = {
        "PPUZW810E性能测试": 6, "910B性能测试": 10,
        "H200性能测试": 14, "海光BW1000性能测试": 18,
        "沐曦C550(浪潮)性能测试": 22, "沐曦C550性能测试": 26,
        "摩尔S5000性能测试": 34, "昆仑芯P800性能测试": 38,
        "寒武纪MLU590-M9DK性能测试": 42,
    }
    adapter_cards = {}
    for sheet, col in adapter_output_columns.items():
        candidates = []
        for row in adapter[3:]:
            total, per_card = _number(row[col]), _number(row[col + 1])
            if total and per_card and abs(total / per_card - round(total / per_card)) <= 0.02:
                candidates.append(round(total / per_card))
        if not candidates or len(set(candidates)) != 1:
            raise ValueError(f"adapter card evidence is inconsistent: {sheet}")
        adapter_cards[sheet] = candidates[0]
    parsed = []
    by_sheet = {}
    for sheet in wb.worksheets:
        values = [[_cell(v) for v in row] for row in sheet.values]
        formula_values = [[_cell(v) for v in row] for row in formula_wb[sheet.title].values]
        headers = _headers(values)
        hints = _sheet_hints(sheet.title, manifest, adapter_cards)
        count = 0
        for index, row in enumerate(values, 1):
            original = formula_values[index - 1]
            formulas = {get_column_letter(i + 1): v for i, v in enumerate(original)
                        if isinstance(v, str) and v.startswith("=")}
            if not any(v is not None for v in row) and not formulas:
                continue
            observation = _observations(values, row, headers)
            parsed.append((sheet.title, index, row, formulas, observation, hints))
            if observation:
                count += 1
        by_sheet[sheet.title] = count
    wb.close()
    formula_wb.close()
    if dry_run:
        return {"dry_run": True, "sheets": len(by_sheet), "archived_rows": len(parsed),
                "inference_observations": sum(by_sheet.values()), "by_sheet": by_sheet}

    init_db(db_path or get_db_path())
    reference_result = import_reference(db_path=db_path or get_db_path())
    inserted, existing, normalized, normalized_flash = 0, 0, 0, 0
    with get_db(db_path or get_db_path()) as db:
        db.execute("BEGIN IMMEDIATE")
        for sheet, index, row, formulas, obs, hints in parsed:
            cells = json.dumps(row, ensure_ascii=False, default=str)
            metrics = json.dumps(obs or {}, ensure_ascii=False, default=str)
            formula_json = json.dumps(formulas, ensure_ascii=False)
            fields = {
                "source_sha256": sha, "source_workbook": workbook.name,
                "source_url": SOURCE_URL, "source_sheet": sheet, "source_row": index,
                "row_kind": _kind(sheet, obs), "chip_model": hints.get("chip_model"),
                "model_id": hints.get("model_id"),
                "physical_cards": hints.get("physical_cards"), "nodes": hints.get("nodes"),
                "deployment": hints.get("deployment"), "precision": hints.get("precision"),
                "input_tokens": obs.get("input") if obs else None,
                "output_tokens": obs.get("output") if obs else None,
                "concurrency": obs.get("concurrency") if obs else None,
                "output_throughput_tok_s": obs.get("throughput") if obs else None,
                "ttft_ms": obs.get("ttft") if obs else None,
                "tpot_ms": obs.get("tpot") if obs else None,
                "metrics_json": metrics, "cells_json": cells, "formulas_json": formula_json,
            }
            columns = ", ".join(fields)
            params = ", ".join("?" for _ in fields)
            cur = db.execute(f"INSERT OR IGNORE INTO benchmark_workbook_rows ({columns}) VALUES ({params})",
                             tuple(str(v) if v is not None and k not in ("source_row", "metrics_json", "cells_json", "formulas_json") else v
                                   for k, v in fields.items()))
            if cur.rowcount:
                inserted += 1
                db.execute("INSERT INTO field_provenance (table_name,row_id,field_name,field_label,"
                           "new_value,source_type,source_url,source_detail,confidence,is_official,updated_at,notes) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)",
                           ("benchmark_workbook_rows", str(cur.lastrowid), "cells_json", "评测表原始行",
                            cells, "user_measured_benchmark", SOURCE_URL,
                            f"{workbook.name} | {sheet}!{index}:{index}", "high", "0",
                            "原始单元格归档；结构化字段不代表具备排序可比性"))
            else:
                existing += 1
                old = db.execute(
                    "SELECT * FROM benchmark_workbook_rows WHERE source_sha256=? AND source_sheet=? AND source_row=?",
                    (sha, sheet, index),
                ).fetchone()
                changed = {k: v for k, v in fields.items() if k not in
                           ("source_sha256", "source_sheet", "source_row") and
                           str(old[k] or "") != str(v or "")}
                if changed:
                    db.execute(
                        "UPDATE benchmark_workbook_rows SET " +
                        ", ".join(f"{k}=?" for k in changed) + " WHERE id=?",
                        (*[str(v) if v is not None else None for v in changed.values()], old["id"]),
                    )
                    for key, value in changed.items():
                        db.execute(
                            "INSERT INTO field_provenance (table_name,row_id,field_name,field_label,"
                            "old_value,new_value,source_type,source_url,source_detail,confidence,is_official,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                            ("benchmark_workbook_rows", str(old["id"]), key, key,
                             str(old[key]) if old[key] is not None else None,
                             str(value) if value is not None else None,
                             "user_measured_benchmark", SOURCE_URL,
                             f"{workbook.name} | {sheet}!{index}:{index}",
                             hints.get("confidence", "medium"), "0"),
                        )
            row_id = cur.lastrowid if cur.rowcount else old["id"]
            traced = {r[0] for r in db.execute(
                "SELECT field_name FROM field_provenance WHERE table_name=? AND row_id=?",
                ("benchmark_workbook_rows", str(row_id)),
            )}
            for key, value in fields.items():
                if value is None or key in traced or key == "source_url":
                    continue
                db.execute(
                    "INSERT INTO field_provenance (table_name,row_id,field_name,field_label,"
                    "new_value,source_type,source_url,source_detail,confidence,is_official,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    ("benchmark_workbook_rows", str(row_id), key, key, str(value),
                     "user_measured_benchmark", SOURCE_URL,
                     f"{workbook.name} | {sheet}!{index}:{index}",
                     hints.get("confidence", "medium"), "0"),
                )
            # All recognizable inference observations are searchable as benchmarks.
            # Unknown model/card fields remain empty; the three curated Flash rows
            # are already in the benchmark table and are not duplicated.
            if not obs:
                continue
            if hints.get("model_id") == FLASH and any(m["source_sheet"] == sheet and
                   int(re.search(r"(\d+)$", m["source_range"]).group(1)) == index
                   for m in manifest["measurements"]):
                continue
            suite = f"User workbook inference {sha[:12]}"
            locator = f"{sheet}!{index}"
            prior = db.execute("SELECT * FROM chip_model_benchmarks WHERE suite_name = ? AND scenario = ?",
                               (suite, locator)).fetchone()
            notes = {"source_workbook": workbook.name, "source_sha256": sha,
                     "source_sheet": sheet,
                     "source_range": f"A{index}:{get_column_letter(len(row))}{index}",
                     "source_url": SOURCE_URL, "physical_cards_verified": bool(hints.get("physical_cards")),
                     "ranking_eligible": bool(hints.get("model_id") == FLASH and hints.get("physical_cards")),
                     "raw_row_kind": "inference_measurement", "all_metrics": obs,
                     "model_evidence": hints.get("model_evidence"),
                     "card_evidence": hints.get("card_evidence"),
                     "mapping_confidence": hints.get("confidence")}
            benchmark = {
                "chip_model": hints.get("chip_model") or "",
                "model_id": hints.get("model_id") or "", "suite_name": suite,
                "workload_type": "inference",
                "scenario": locator, "task": "serving",
                "hardware_config": hints.get("deployment") or "",
                "chip_count": str(hints.get("physical_cards") or ""),
                "precision": hints.get("precision") or "",
                "input_seq_length": str(int(float(obs["input"]))),
                "output_seq_length": str(int(float(obs["output"]))),
                "concurrency": str(int(float(obs["concurrency"]))),
                "throughput_tok_s": str(obs["throughput"]),
                "decode_throughput": str(obs["throughput"]),
                "throughput_samples_s": str(obs.get("req_s") or ""),
                "time_to_first_token_ms": str(obs.get("ttft") or ""),
                "tpot_ms": str(obs.get("tpot") or ""),
                "notes": json.dumps(notes, ensure_ascii=False),
            }
            source = {"source_type": "user_measured_benchmark", "source_url": SOURCE_URL,
                      "source_detail": f"{workbook.name} | {locator}",
                      "confidence": hints.get("confidence", "medium"), "is_official": "0"}
            if prior:
                changed = {k: v for k, v in benchmark.items()
                           if str(prior[k] or "") != str(v or "")}
                if changed:
                    db.execute("UPDATE chip_model_benchmarks SET " +
                               ", ".join(f"{k}=?" for k in changed) + " WHERE id=?",
                               (*changed.values(), prior["id"]))
                    for key, value in changed.items():
                        db.execute(
                            "INSERT INTO field_provenance (table_name,row_id,field_name,field_label,"
                            "old_value,new_value,source_type,source_url,source_detail,confidence,is_official,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                            ("chip_model_benchmarks", str(prior["id"]), key, key,
                             str(prior[key]) if prior[key] is not None else None, str(value),
                             *[source[k] for k in ("source_type", "source_url", "source_detail",
                                                  "confidence", "is_official")]),
                        )
            else:
                add_benchmark(db, benchmark, source)
                normalized += 1
                if hints.get("model_id") == FLASH:
                    normalized_flash += 1
        db.commit()
    return {"dry_run": False, "sheets": len(by_sheet), "archived_rows_inserted": inserted,
            "archived_rows_existing": existing, "inference_observations": sum(by_sheet.values()),
            "reference_rows_inserted": len(reference_result["inserted"]),
            "inference_benchmarks_inserted": normalized,
            "flash_benchmarks_inserted": normalized_flash, "by_sheet": by_sheet}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workbook", type=Path, default=WORKBOOK)
    p.add_argument("--db-path", type=Path)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(json.dumps(import_workbook(args.workbook, args.db_path, args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
