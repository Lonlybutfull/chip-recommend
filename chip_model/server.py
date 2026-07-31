"""AISHPerf Knowledge Graph — FastAPI Server.

Usage:
    python server.py                    # standalone
    python cli.py --server --db-path data.db   # via CLI flag
"""

import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from scalar_fastapi import get_scalar_api_reference, Theme

from chip_model.database import (
    get_db_path,
    set_db_path,
    get_db_stats,
    search_chips,
    ChipFilters,
    get_chip_profile,
    get_chip_profiles_batch,
    get_chip_recommend_candidates,
    search_models,
    ModelFilters,
    get_model_profile,
    get_model_profiles_batch,
    search_benchmarks,
    BenchmarkFilters,
    search_compat,
    CompatFilters,
    search_provenance,
    ProvenanceFilters,
    get_provenance_stats,
    chip_recommend_candidate,
    get_chip_benchmarks_for_model,
    get_chip_benchmark_mfu,
    get_chip_benchmark_tps,
    get_chip_model_compat_count,
)
from chip_model.scoring import (  # v2.0 scoring engine
    parse_fp16,
    round_up_pow2,
    RecommendContext,
    ScoringWeights,
    TRAIN_WEIGHTS,
    INFERENCE_WEIGHTS,
    DimensionResult,
    ScoringResult,
    aggregate_score,
    scoring_result_to_dict,
    DIMENSION_META,
)

# ── Lifespan ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: set DB path if env var provided."""
    import os
    env_path = os.environ.get("DATA_DB_PATH")
    if env_path:
        set_db_path(env_path)
    yield


# ── App ─────────────────────────────────────────────────────────

app = FastAPI(
    title="AISHPerf Knowledge Graph",
    description="AI Chip × Model knowledge graph query API",
    version="0.4.0",
    lifespan=lifespan,
    docs_url=None,   # disabled — replaced by Scalar at /docs
    redoc_url=None,  # disabled
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Scalar API reference (modern interactive docs) ──

from scalar_fastapi import get_scalar_api_reference, Theme

@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title="AISHPerf API",
        theme=Theme.DEEP_SPACE,
        show_sidebar=True,
        hide_download_button=False,
    )


# ═══════════════════════════════════════════════════════════════
# Batch request models
# ═══════════════════════════════════════════════════════════════

class BatchRequest(BaseModel):
    identifiers: list[str]


class ChatRequest(BaseModel):
    messages: list[dict]
    model: str | None = None


# ═══════════════════════════════════════════════════════════════
# Chat Agent (DeepSeek streaming)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/v1/chat")
async def api_chat(body: ChatRequest):
    """Streaming chat with DeepSeek + AISHPerf tool calling."""
    from chip_model.chat_agent import chat_stream, DEEPSEEK_MODEL

    async def event_generator():
        async for chunk in chat_stream(body.messages, body.model or DEEPSEEK_MODEL):
            yield chunk
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ═══════════════════════════════════════════════════════════════
# Chips
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/chips")
def api_chip_search(
    search: Optional[str] = Query(None, description="Fuzzy search (vendor / model / series / architecture)"),
    keyword: Optional[str] = Query(None, description="Alias for 'search' parameter"),
    vendor: Optional[str] = Query(None),
    region: Optional[str] = Query(None, description="domestic | foreign"),
    usage: Optional[str] = Query(None, description="train | inference | both"),
    vram_min: Optional[float] = Query(None, description="Min VRAM (GB)"),
    vram_max: Optional[float] = Query(None, description="Max VRAM (GB)"),
    tdp_max: Optional[float] = Query(None, description="Max TDP (W)"),
    price_max: Optional[float] = Query(None, description="Max unit price (万元/片)"),
    interconnect_min: Optional[float] = Query(None, description="Min interconnect BW (GB/s)"),
    tier: Optional[str] = Query(None, description="datacenter | consumer | all"),
    min_maturity: Optional[int] = Query(None, description="Min ecosystem maturity (0-5)"),
    for_model: Optional[str] = Query(None, description="Auto-estimate VRAM for this model"),
    scenario: Optional[str] = Query(None, description="train | inference (with for_model)"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    include_provenance: bool = Query(False, description="Include per-chip field provenance summary"),
):
    """Search chips with multi-condition filtering."""
    # Accept 'keyword' as alias for 'search'
    effective_search = search or keyword
    filters = ChipFilters(
        search=effective_search, vendor=vendor, region=region, usage=usage,
        vram_min=vram_min, vram_max=vram_max, tdp_max=tdp_max,
        price_max=price_max, interconnect_min=interconnect_min,
        tier=tier, min_maturity=min_maturity,
        for_model=for_model, scenario=scenario,
    )
    return search_chips(filters, limit=limit, offset=offset,
                        include_provenance=include_provenance)


@app.get("/api/v1/chips/recommend")
def api_chip_recommend(
    model: str = Query(..., description="Model name (fuzzy match)"),
    scenario: str = Query("train", description="train | inference"),
    training_days: Optional[float] = Query(None, description="Target training days"),
    training_tokens: Optional[float] = Query(None, description="Training data volume (T tokens), auto-estimated if unset"),
    sla_tps: Optional[float] = Query(None, description="Target inference throughput (tok/s)"),
    tier: Optional[str] = Query("datacenter", description="datacenter | all"),
    max_cards: Optional[int] = Query(None, description="Hard exclude: max cards"),
    min_cards: Optional[int] = Query(None, description="Hard floor: min cards (round up to pow2)"),
    max_price: Optional[float] = Query(None, description="Hard exclude: max unit price (万元)"),
    min_maturity: Optional[int] = Query(None, description="Hard exclude: min maturity 0-5"),
    prefer_domestic: bool = Query(False, description="Prefer domestic chips"),
    prefer_vendor: Optional[str] = Query(None, description="Prefer vendor"),
    tco_weight: float = Query(0.0, ge=0.0, le=0.3, description="TCO dimension weight 0.0-0.3"),
    limit: int = Query(5, le=20),
):
    """Recommend chips for a model × scenario × constraints.  v2.0 10-dimension scoring."""
    # 1. Find model
    model_result = search_models(ModelFilters(search=model), limit=1)
    if model_result["count"] == 0:
        raise HTTPException(404, f"未找到模型: {model}，请检查模型名称是否正确（支持模糊匹配，如 Qwen2.5-7B、Llama-3.1-8B）")
    model_data = model_result["models"][0]

    total_params = float(model_data.get("total_params_b", 0) or 0)
    model_id = str(model_data.get("model_id", "") or "")
    arch_family = str(model_data.get("architecture_family", "") or "")

    # 2. Auto-estimate training tokens if unset (Chinchilla: tokens ≈ 20×params, use 50% = 10×params)
    if training_tokens is None:
        training_tokens_val = max(0.1, min(100.0, total_params * 10.0))
    else:
        training_tokens_val = training_tokens

    model_summary = (
        f"{model_id} | {arch_family} | {total_params}B params"
    )

    # 3. Calculate VRAM + FLOPs requirements (with quantized model support)
    import json as _json
    config_raw = model_data.get("config_json", "") or ""
    is_quantized = False
    quant_bits = None
    try:
        cfg = _json.loads(config_raw) if config_raw else {}
        is_quantized = cfg.get("is_quantized", False)
        quant_bits = cfg.get("quant_bits")
    except Exception:
        pass

    if scenario == "train" and is_quantized:
        raise HTTPException(400, f"量化模型 {model_id} 只能用于推理场景，不支持训练")

    if scenario == "train":
        min_vram_total = total_params * 12 * 1.3
        total_flops = 6 * (total_params * 1e9) * (training_tokens_val * 1e12)
    else:
        # Inference: adjust VRAM for quantized models
        if is_quantized and quant_bits:
            bytes_per_param = quant_bits / 8.0
        else:
            bytes_per_param = 2.0  # FP16 default
        min_vram_total = total_params * bytes_per_param * 1.25
        total_flops = 0.0

    # 4. Get candidates
    _, candidates = get_chip_recommend_candidates(
        model, scenario=scenario, tier=tier or "datacenter",
        prefer_domestic=prefer_domestic,
    )

    if not candidates:
        raise HTTPException(404, f"没有芯片满足 {model} 的VRAM需求 (≥{min_vram_total:.0f}GB)，请尝试其他模型或放宽约束")

    # 5. Scoring loop
    weights = TRAIN_WEIGHTS if scenario == "train" else INFERENCE_WEIGHTS
    scored: list[dict] = []
    for chip in candidates:
        chip_dict = dict(chip)
        vram = float(chip_dict.get("vram_gb", 1))
        maturity = int(float(chip_dict.get("maturity_level", 0) or 0))
        price_wan = float(chip_dict.get("price_cny_wan", 0) or 0)
        chip_model_name = str(chip_dict.get("chip_model", "") or "")

        # ── Card estimation ──
        vram_cards = max(1, int(min_vram_total / vram) + 1)
        vram_cards = round_up_pow2(vram_cards)
        compute_cards = vram_cards
        deadline_cards = vram_cards
        estimated_days = None

        fp16_val = parse_fp16(chip_dict.get("precision_perf", ""))

        if scenario == "train" and fp16_val > 0:
            # Get measured MFU or use default 0.30
            bench_mfu = get_chip_benchmark_mfu(chip_model_name)
            mfu_target = (bench_mfu / 100.0) if bench_mfu else 0.30
            effective_per_card_day = fp16_val * 1e12 * mfu_target * 86400
            if effective_per_card_day > 0 and training_days:
                raw_compute = int(total_flops / (effective_per_card_day * training_days)) + 1
                compute_cards = round_up_pow2(max(vram_cards, raw_compute))
                deadline_cards = compute_cards
                estimated_days = round(
                    total_flops / (effective_per_card_day * deadline_cards), 1
                )

        recommended_cards = deadline_cards

        # Min cards floor
        if min_cards:
            min_cards_pow2 = round_up_pow2(min_cards)
            if recommended_cards < min_cards_pow2:
                recommended_cards = round_up_pow2(min_cards_pow2)

        # Hard exclude
        if max_cards and recommended_cards > max_cards:
            continue
        if max_price and price_wan and price_wan > max_price:
            continue
        if min_maturity is not None and maturity < min_maturity:
            continue

        meets_sla = True
        if training_days and estimated_days is not None and estimated_days > training_days:
            meets_sla = False

        # ── Benchmark data for scoring ──
        bench_records = get_chip_benchmarks_for_model(
            chip_model_name, model_id, total_params,
        )
        bench_mfu_val = get_chip_benchmark_mfu(chip_model_name)
        bench_tps_val = get_chip_benchmark_tps(chip_model_name)
        compat_verified = get_chip_model_compat_count(chip_model_name)

        # ── v2.0 Scoring ──
        ctx = RecommendContext(
            chip=chip_dict,
            model_params_B=total_params,
            scenario=scenario,
            min_vram_total=min_vram_total,
            vram_cards=vram_cards,
            recommended_cards=recommended_cards,
            fp16_tflops=fp16_val,
            training_tokens_T=training_tokens_val,
            target_training_days=training_days,
            target_tps=sla_tps,
            estimated_training_days=estimated_days,
            benchmark_count=len(bench_records),
            max_benchmark_mfu=bench_mfu_val,
            max_benchmark_tps=bench_tps_val,
            compat_verified_count=compat_verified,
        )

        scoring_result = aggregate_score(
            ctx, weights,
            prefer_domestic=prefer_domestic,
            prefer_vendor=prefer_vendor,
            tco_weight_override=tco_weight,
        )

        scored.append({
            "chip": chip_dict,
            "vram_cards": vram_cards,
            "recommended_cards": recommended_cards,
            "estimated_training_days": estimated_days,
            "meets_sla": meets_sla,
            "price_wan": price_wan,
            "score": scoring_result.total_score,
            "scoring": scoring_result_to_dict(scoring_result),
        })

    if not scored:
        raise HTTPException(404, "所有候选芯片均被硬约束排除，请放宽最大卡数/最小卡数、最高单价或最低成熟度限制")

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:limit]

    return {
        "model": model_summary,
        "requirements": {
            "scenario": scenario,
            "min_vram_gb": round(min_vram_total, 1),
            "training_tokens_T": round(training_tokens_val, 1),
            "target_training_days": training_days,
            "target_tokens_per_sec": sla_tps,
            "max_cards": max_cards,
            "min_cards": min_cards,
            "max_price_wan": max_price,
            "min_maturity": min_maturity,
        },
        "scoring_meta": {
            "version": "2.0.0",
            "scenario_weights": weights.__dict__,
            "dimensions": DIMENSION_META,
        },
        "candidates": [
            chip_recommend_candidate_v2(
                chip=s["chip"],
                vram_cards=s["vram_cards"],
                recommended_cards=s["recommended_cards"],
                estimated_training_days=s["estimated_training_days"],
                meets_sla=s["meets_sla"],
                total_cost_wan=(
                    round(s["price_wan"] * s["recommended_cards"], 1)
                    if s["price_wan"] else None
                ),
                score=s["score"],
                scoring=s["scoring"],
            )
            for s in top
        ],
        "rejected": len(scored) - len(top),
    }


# ── v2.0 chip_recommend_candidate (with scoring breakdown) ──

def chip_recommend_candidate_v2(
    chip: dict,
    vram_cards: int,
    recommended_cards: int,
    estimated_training_days: float | None,
    meets_sla: bool,
    total_cost_wan: float | None,
    score: float,
    scoring: dict,
) -> dict:
    """Format one scored chip for v2 recommend output (includes dimension breakdown)."""
    # Reuse base candidate shape but add scoring
    base = chip_recommend_candidate(
        chip=chip,
        vram_cards=vram_cards,
        recommended_cards=recommended_cards,
        estimated_training_days=estimated_training_days,
        meets_sla=meets_sla,
        total_cost_wan=total_cost_wan,
        score=score,
    )
    base["scoring"] = scoring
    return base


# ── Methodology endpoint ──

@app.get("/api/v1/methodology")
def api_methodology():
    """Return scoring methodology documentation for the UI."""
    return {
        "version": "2.0.0",
        "description": "AISHPerf 芯片推荐引擎 — 10维量化评分方法",
        "card_estimation": {
            "vram_train": "总参数量(B) × 12 × 1.3 → 按单卡显存分摊 → 取2幂次方",
            "vram_inference": "总参数量(B) × 2 × 1.25 → 按单卡显存分摊 → 取2幂次方",
            "compute_train": "6ND FLOPs (N=参数量, D=训练数据量tokens) / (单卡有效算力 × 训练天数) → 取2幂次方",
            "mfu_default": 0.30,
            "mfu_prefer_benchmark": "优先使用 chip_model_benchmarks 表的实测 MFU",
            "inference_throughput_formula": "min(compute_bound, memory_bound) × 0.30 效率因子",
        },
        "scoring_dimensions": DIMENSION_META,
        "total_score_formula": "Σ(维度得分 × 权重) × 10 → 0~100 分制",
        "hard_constraints": [
            "max_cards: 推荐卡数超过此值 → 排除",
            "min_cards: 推荐卡数低于此值 → 拉高到 min_cards (2幂次方)",
            "max_price: 单价超过此值 → 排除",
            "min_maturity: 成熟度低于此值 → 排除",
        ],
    }




@app.get("/api/v1/chips/{identifier}")
def api_chip_profile(identifier: str):
    """Full chip profile.  identifier = ID (pure digits) or name (fuzzy)."""
    result = get_chip_profile(identifier)
    if result is None:
        raise HTTPException(404, f"Chip not found: {identifier}")
    return result


@app.post("/api/v1/chips/batch")
def api_chip_batch(body: BatchRequest):
    """Batch chip profiles."""
    results = get_chip_profiles_batch(body.identifiers)
    missing = sum(1 for r in results if r is None)
    return {
        "count": len(results),
        "found": len(results) - missing,
        "missing": missing,
        "profiles": [r for r in results if r is not None],
    }


# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/models")
def api_model_search(
    search: Optional[str] = Query(None, description="Fuzzy search (model_id / author)"),
    architecture: Optional[str] = Query(None, description="dense | moe"),
    params_min: Optional[float] = Query(None, description="Min params (B)"),
    params_max: Optional[float] = Query(None, description="Max params (B)"),
    for_chip: Optional[str] = Query(None, description="Find models compatible with this chip"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    include_provenance: bool = Query(False, description="Include per-model field provenance summary"),
):
    """Search models."""
    filters = ModelFilters(
        search=search, architecture=architecture,
        params_min=params_min, params_max=params_max, for_chip=for_chip,
    )
    return search_models(filters, limit=limit, offset=offset,
                         include_provenance=include_provenance)


@app.get("/api/v1/models/{identifier}")
def api_model_profile(identifier: str):
    """Full model profile.  identifier = ID (pure digits) or name (fuzzy)."""
    result = get_model_profile(identifier)
    if result is None:
        raise HTTPException(404, f"Model not found: {identifier}")
    return result


@app.post("/api/v1/models/batch")
def api_model_batch(body: BatchRequest):
    """Batch model profiles."""
    results = get_model_profiles_batch(body.identifiers)
    missing = sum(1 for r in results if r is None)
    return {
        "count": len(results),
        "found": len(results) - missing,
        "missing": missing,
        "profiles": [r for r in results if r is not None],
    }


# ═══════════════════════════════════════════════════════════════
# Benchmarks
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/benchmarks")
def api_benchmark_search(
    chip: Optional[str] = Query(None, description="Filter by chip model name"),
    chip_model: Optional[str] = Query(None, description="Alias for 'chip' parameter"),
    model: Optional[str] = Query(None),
    workload: Optional[str] = Query(None, description="inference | training"),
    suite: Optional[str] = Query(None, description="MLPerf | vendor_doc | community"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    include_provenance: bool = Query(False, description="Include per-benchmark field provenance summary"),
):
    """Search benchmark records."""
    effective_chip = chip or chip_model
    filters = BenchmarkFilters(chip=effective_chip, model=model, workload=workload, suite=suite)
    return search_benchmarks(filters, limit=limit, offset=offset,
                             include_provenance=include_provenance)


# ═══════════════════════════════════════════════════════════════
# Compatibility
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/compat")
def api_compat_search(
    chip: Optional[str] = Query(None, description="Filter by chip model name"),
    chip_model: Optional[str] = Query(None, description="Alias for 'chip' parameter"),
    model: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="verified | vendor_claimed | community | unsupported"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    include_provenance: bool = Query(False, description="Include per-compat field provenance summary"),
):
    """Search compatibility records."""
    effective_chip = chip or chip_model
    filters = CompatFilters(chip=effective_chip, model=model, status=status)
    return search_compat(filters, limit=limit, offset=offset,
                         include_provenance=include_provenance)


# ═══════════════════════════════════════════════════════════════
# Provenance
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/provenance")
def api_provenance_search(
    table: Optional[str] = Query(None, description="chips | models | chip_model_benchmarks | chip_model_compatibility"),
    row_id: Optional[str] = Query(None),
    field: Optional[str] = Query(None, description="Field name (fuzzy)"),
    source_type: Optional[str] = Query(None),
    confidence: Optional[str] = Query(None, description="high | medium | low"),
    is_official: Optional[str] = Query(None, description="0 | 1"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """Search field-level provenance records."""
    filters = ProvenanceFilters(
        table_name=table, row_id=row_id, field_name=field,
        source_type=source_type, confidence=confidence, is_official=is_official,
    )
    return search_provenance(filters, limit=limit, offset=offset)


@app.get("/api/v1/provenance/stats")
def api_provenance_stats(
    table: Optional[str] = Query(None, description="Filter by table name"),
):
    """Provenance aggregation stats."""
    return get_provenance_stats(table=table)


# ═══════════════════════════════════════════════════════════════
# DB status
# ═══════════════════════════════════════════════════════════════

@app.get("/api/v1/db/status")
def api_db_status():
    """Database info."""
    return {"database": str(get_db_path()), "tables": get_db_stats()}


# ═══════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════

@app.api_route("/api/v1/health", methods=["GET", "HEAD"])
def api_health():
    return {"status": "ok", "version": "0.4.0"}


# ═══════════════════════════════════════════════════════════════
# Static files — must be last so API routes take priority
# ═══════════════════════════════════════════════════════════════

import os as _os
_static_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "static")
if _os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


# ═══════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════

def run_server(db_path: str | None = None, host: str = "0.0.0.0", port: int = 8000):
    """Launch uvicorn.  Called from cli.py --server or directly."""
    import uvicorn
    if db_path:
        set_db_path(db_path)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="AISHPerf FastAPI Server")
    ap.add_argument("--db-path", default=None, help="SQLite database path")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    run_server(db_path=args.db_path, host=args.host, port=args.port)
