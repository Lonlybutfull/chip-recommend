"""Idempotently import the curated Flash serving reference from the workbook.

The JSON manifest keeps the exact source cells and deployment conditions.  It
is deliberately small: sheets without a verified model/chip/card mapping are
not silently converted into model-specific performance measurements.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chip_model.database import add_benchmark, get_db, get_db_path


DEFAULT_MANIFEST = ROOT / "data" / "deepseek_v4_flash_reference_20260713.json"
SUITE_NAME = "DeepSeek-V4-Flash 20260713 curated serving reference"
EXPECTED_MODEL = "deepseek-ai/DeepSeek-V4-Flash"


def _positive(value: object, label: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def import_reference(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    db_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("model_id") != EXPECTED_MODEL:
        raise ValueError("manifest must contain only DeepSeek-V4-Flash measurements")
    reference = manifest.get("reference") or {}
    if reference != {"input_tokens": 1024, "output_tokens": 1024, "concurrency": 1}:
        raise ValueError("manifest reference does not match ranking reference")
    records = manifest.get("measurements") or []
    if not records:
        raise ValueError("manifest contains no measurements")

    inserted, existing = [], []
    with get_db(db_path or get_db_path(), readonly=dry_run) as db:
        if not dry_run:
            db.execute("BEGIN IMMEDIATE")
        for row in records:
            chip_name = str(row.get("chip_model") or "")
            cards = int(_positive(row.get("physical_cards"), "physical_cards"))
            if cards != float(row["physical_cards"]):
                raise ValueError("physical_cards must be a whole number")
            nodes = int(_positive(row.get("nodes"), "nodes"))
            if nodes != float(row["nodes"]):
                raise ValueError("nodes must be a whole number")
            if nodes > cards:
                raise ValueError("nodes cannot exceed physical card count")
            output_tps = _positive(row.get("output_throughput_tok_s"), "output throughput")
            ttft = _positive(row.get("ttft_ms"), "TTFT")
            tpot = _positive(row.get("tpot_ms"), "TPOT")
            sheet = str(row.get("source_sheet") or "")
            cell_range = str(row.get("source_range") or "")
            if not chip_name or not sheet or not cell_range or not row.get("precision"):
                raise ValueError("chip, source cell range and precision are required")
            matches = db.execute(
                "SELECT COUNT(*) FROM chips WHERE chip_model = ?", (chip_name,)
            ).fetchone()[0]
            if matches != 1:
                raise ValueError(f"chip mapping is not unique: {chip_name} ({matches})")

            fields = {
                "chip_model": chip_name,
                "model_id": EXPECTED_MODEL,
                "suite_name": SUITE_NAME,
                "workload_type": "inference",
                "scenario": "reference_1024_input_1024_output_1_concurrency",
                "task": "serving",
                "hardware_config": str(row.get("hardware_config") or ""),
                "chip_count": str(cards),
                "framework": str(row.get("framework") or ""),
                "precision": str(row["precision"]),
                "input_seq_length": "1024",
                "output_seq_length": "1024",
                "concurrency": "1",
                "throughput_tok_s": str(output_tps),
                "throughput_samples_s": str(
                    _positive(row.get("request_throughput_req_s"), "request throughput")
                ),
                "decode_throughput": str(output_tps),
                "time_to_first_token_ms": str(ttft),
                "tpot_ms": str(tpot),
                "test_date": str(row.get("test_date") or ""),
                "notes": json.dumps({
                    "source_workbook": manifest["source_workbook"],
                    "source_sha256": manifest["source_sha256"],
                    "source_sheet": sheet,
                    "source_range": cell_range,
                    "source_url": manifest["source_url"],
                    "model_evidence": row.get("model_evidence"),
                    "hardware_evidence": row.get("hardware_evidence"),
                    "physical_cards": cards,
                    "nodes": nodes,
                    "request_count": row.get("request_count"),
                    "total_throughput_tok_s": row.get("total_throughput_tok_s"),
                    "metric_definition": manifest["metric_definition"],
                }, ensure_ascii=False, sort_keys=True),
            }
            prior = db.execute(
                "SELECT * FROM chip_model_benchmarks WHERE model_id = ? "
                "AND chip_model = ? AND suite_name = ? AND scenario = ?",
                (EXPECTED_MODEL, chip_name, SUITE_NAME, fields["scenario"]),
            ).fetchall()
            if prior:
                if len(prior) != 1 or any(
                    str(prior[0][key] or "") != str(value or "")
                    for key, value in fields.items()
                ):
                    raise ValueError(f"existing source record differs: {chip_name}")
                existing.append(chip_name)
                continue
            if not dry_run:
                source = {
                    "source_type": "user_measured_benchmark",
                    "source_url": manifest["source_url"],
                    "source_detail": f"{manifest['source_workbook']} | {sheet}!{cell_range}",
                    "confidence": str(row.get("confidence") or "medium"),
                    "is_official": "0",
                    "notes": "原始整机指标与卡数来自用户提供的评测表；不同量化/部署配置须同时展示。",
                }
                add_benchmark(db, fields, source)
            inserted.append(chip_name)
        if not dry_run:
            db.commit()
    return {"dry_run": dry_run, "inserted": inserted, "existing": existing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_reference(args.manifest, args.db_path, args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
