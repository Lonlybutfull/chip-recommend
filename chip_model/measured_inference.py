"""Comparable, model-specific inference measurements for recommendation scoring.

Only rows with a real throughput and the same reference workload can affect
ranking.  A model mention or a benchmark for a different model is not a
performance measurement for the selected model.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from chip_model.database import get_db


REFERENCE_INPUT_TOKENS = 1024
REFERENCE_OUTPUT_TOKENS = 1024
REFERENCE_CONCURRENCY = 1


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def get_comparable_measurements(
    model_id: str, chip_models: list[str], db_path: str | Path | None = None,
) -> dict[str, dict]:
    """Return a median observation per chip for one fixed reference workload.

    Throughput is normalized by physical card count.  Latencies remain the
    measured request latencies, not divided by cards.  Rows with missing card
    count, invalid throughput, or different prompt/output/concurrency are
    excluded rather than silently compared.
    """
    if not chip_models:
        return {}
    with get_db(db_path, readonly=True) as db:
        rows = [dict(row) for row in db.execute(
            "SELECT id, chip_model, model_id, suite_name, hardware_config, "
            "chip_count, framework, precision, input_seq_length, "
            "output_seq_length, concurrency, throughput_tok_s, "
            "time_to_first_token_ms, tpot_ms, test_date, notes "
            "FROM chip_model_benchmarks "
            "WHERE model_id = ? AND workload_type = 'inference' "
            "AND input_seq_length = ? AND output_seq_length = ? "
            "AND concurrency = ?",
            (model_id, str(REFERENCE_INPUT_TOKENS),
             str(REFERENCE_OUTPUT_TOKENS), str(REFERENCE_CONCURRENCY)),
        ).fetchall()]

    grouped: dict[str, list[dict]] = {}
    names = set(chip_models)
    for row in rows:
        chip_name = str(row.get("chip_model") or "")
        cards = _positive(row.get("chip_count"))
        throughput = _positive(row.get("throughput_tok_s"))
        if chip_name not in names or cards is None or throughput is None:
            continue
        grouped.setdefault(chip_name, []).append({
            **row,
            "throughput_per_card": throughput / cards,
            "ttft": _positive(row.get("time_to_first_token_ms")),
            "tpot": _positive(row.get("tpot_ms")),
        })

    result: dict[str, dict] = {}
    for chip_name, observations in grouped.items():
        # Multiple runs of the same fixed workload cannot be cherry-picked.
        per_card = statistics.median(r["throughput_per_card"] for r in observations)
        ttfts = [r["ttft"] for r in observations if r["ttft"] is not None]
        tpots = [r["tpot"] for r in observations if r["tpot"] is not None]
        sources = []
        for observation in observations:
            try:
                notes = json.loads(observation.get("notes") or "{}")
            except (ValueError, TypeError):
                notes = {}
            if isinstance(notes, dict) and notes.get("source_sheet"):
                sources.append({
                    "record_id": observation["id"],
                    "workbook": notes.get("source_workbook"),
                    "sheet": notes.get("source_sheet"),
                    "range": notes.get("source_range"),
                    "url": notes.get("source_url"),
                    "nodes": notes.get("nodes"),
                    "hardware_config": observation.get("hardware_config"),
                    "framework": observation.get("framework"),
                    "precision": observation.get("precision"),
                    "total_output_throughput_tok_s": _positive(observation.get("throughput_tok_s")),
                })
        result[chip_name] = {
            "throughput_per_card": per_card,
            "ttft_ms": statistics.median(ttfts) if ttfts else None,
            "tpot_ms": statistics.median(tpots) if tpots else None,
            "record_ids": [r["id"] for r in observations],
            "card_counts": sorted({int(float(r["chip_count"])) for r in observations}),
            "frameworks": sorted({str(r["framework"]) for r in observations if r["framework"]}),
            "precision": sorted({str(r["precision"]) for r in observations if r["precision"]}),
            "sources": sources,
        }
    return result


def score_comparable_measurements(measurements: dict[str, dict]) -> dict[str, dict]:
    """Score per-card throughput (60%), TTFT (20%), and TPOT (20%).

    A missing latency simply redistributes weight to the available metrics;
    missing *all* measurements returns no scores, so benchmark scores stay
    identical and neutral rather than inventing relative performance.
    """
    if not measurements:
        return {}
    if len(measurements) == 1:
        chip_name, m = next(iter(measurements.items()))
        return {chip_name: {
            **m,
            "score": 70.0,
            "reference": {
                "input_tokens": REFERENCE_INPUT_TOKENS,
                "output_tokens": REFERENCE_OUTPUT_TOKENS,
                "concurrency": REFERENCE_CONCURRENCY,
            },
            "formula": "仅一款芯片有可比实测，暂记70分；待第二款芯片录入后才计算相对性能分",
        }}
    max_tps = max(m["throughput_per_card"] for m in measurements.values())
    min_ttft = min((m["ttft_ms"] for m in measurements.values() if m["ttft_ms"]), default=None)
    min_tpot = min((m["tpot_ms"] for m in measurements.values() if m["tpot_ms"]), default=None)
    scored = {}
    for chip_name, m in measurements.items():
        pieces = [(0.60, 100 * m["throughput_per_card"] / max_tps)]
        if min_ttft and m["ttft_ms"]:
            pieces.append((0.20, 100 * min_ttft / m["ttft_ms"]))
        if min_tpot and m["tpot_ms"]:
            pieces.append((0.20, 100 * min_tpot / m["tpot_ms"]))
        weight = sum(w for w, _ in pieces)
        scored[chip_name] = {
            **m,
            "score": round(sum(w * value for w, value in pieces) / weight, 2),
            "reference": {
                "input_tokens": REFERENCE_INPUT_TOKENS,
                "output_tokens": REFERENCE_OUTPUT_TOKENS,
                "concurrency": REFERENCE_CONCURRENCY,
            },
            "formula": "同场景单卡吞吐60% + TTFT20% + TPOT20%；缺失延迟时按已有指标重归一化",
        }
    return scored
