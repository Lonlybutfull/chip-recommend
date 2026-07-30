"""AISHPerf Knowledge Graph — FastAPI Server.

Usage:
    python server.py                    # standalone
    python cli.py --server --db-path data.db   # via CLI flag
"""

import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
    sla_tps: Optional[float] = Query(None, description="Target inference throughput (tok/s)"),
    tier: Optional[str] = Query("datacenter", description="datacenter | all"),
    max_cards: Optional[int] = Query(None, description="Hard exclude: max cards"),
    max_price: Optional[float] = Query(None, description="Hard exclude: max unit price (万元)"),
    min_maturity: Optional[int] = Query(None, description="Hard exclude: min maturity 0-5"),
    prefer_domestic: bool = Query(False, description="Prefer domestic chips"),
    prefer_vendor: Optional[str] = Query(None, description="Prefer vendor"),
    limit: int = Query(5, le=20),
):
    """Recommend chips for a model × scenario × constraints.  9-dimension scoring."""
    # 1. Find model
    model_result = search_models(ModelFilters(search=model), limit=1)
    if model_result["count"] == 0:
        raise HTTPException(404, f"未找到模型: {model}，请检查模型名称是否正确（支持模糊匹配，如 Qwen2.5-7B、Llama-3.1-8B）")
    model_data = model_result["models"][0]

    total_params = float(model_data.get("total_params_b", 0) or 0)
    model_summary = (
        f"{model_data.get('model_id', '')} | "
        f"{model_data.get('architecture_family', '')} | "
        f"{total_params}B params"
    )

    # 2. Calculate VRAM
    if scenario == "train":
        min_vram_total = total_params * 12 * 1.3
    else:
        min_vram_total = total_params * 2 * 1.25

    # 3. Get candidates
    _, candidates = get_chip_recommend_candidates(
        model, scenario=scenario, tier=tier or "datacenter",
        prefer_domestic=prefer_domestic,
    )

    if not candidates:
        raise HTTPException(404, f"没有芯片满足 {model} 的VRAM需求 (≥{min_vram_total:.0f}GB)，请尝试其他模型或放宽约束")

    # 4. Score (same logic as CLI)
    assumed_tokens_T = 0.005 if scenario == "train" else 0.0
    scored: list[dict] = []
    for chip in candidates:
        chip_dict = dict(chip)
        vram = float(chip_dict.get("vram_gb", 1))
        maturity = int(float(chip_dict.get("maturity_level", 0) or 0))
        price_wan = float(chip_dict.get("price_cny_wan", 0) or 0)

        vram_cards = max(1, int(min_vram_total / vram) + 1)
        vram_cards = _round_up_pow2(vram_cards)
        deadline_cards = vram_cards
        estimated_days = None

        fp16_val = _parse_fp16(chip_dict.get("precision_perf", ""))
        if scenario == "train" and training_days and fp16_val > 0:
            total_flops = 6 * (total_params * 1e9) * (assumed_tokens_T * 1e12)
            effective_per_card_day = fp16_val * 1e12 * 0.3 * 86400
            if effective_per_card_day > 0:
                deadline_cards = max(
                    vram_cards,
                    int(total_flops / (effective_per_card_day * training_days)) + 1,
                )
                deadline_cards = _round_up_pow2(deadline_cards)
                estimated_days = round(
                    total_flops / (effective_per_card_day * deadline_cards), 1
                )

        recommended_cards = deadline_cards

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

        score = _score_chip(
            chip_dict, recommended_cards, estimated_days, training_days,
            prefer_domestic, prefer_vendor, fp16_val, price_wan, maturity,
            int(float(chip_dict.get("cloud_available", 0) or 0)),
        )
        scored.append({
            "chip": chip_dict,
            "vram_cards": vram_cards,
            "recommended_cards": recommended_cards,
            "estimated_training_days": estimated_days,
            "meets_sla": meets_sla,
            "price_wan": price_wan,
            "score": score,
        })

    if not scored:
        raise HTTPException(404, "所有候选芯片均被硬约束排除，请放宽最大卡数、最高单价或最低成熟度限制")

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:limit]

    return {
        "model": model_summary,
        "requirements": {
            "scenario": scenario,
            "min_vram_gb": round(min_vram_total, 1),
            "target_training_days": training_days,
            "target_tokens_per_sec": sla_tps,
            "max_cards": max_cards,
            "max_price_wan": max_price,
            "min_maturity": min_maturity,
        },
        "candidates": [
            chip_recommend_candidate(
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
            )
            for s in top
        ],
        "rejected": len(scored) - len(top),
        "scoring_dimensions": [
            "compute_power", "card_efficiency", "price_efficiency",
            "power_efficiency", "ecosystem_maturity", "interconnect_quality",
            "sla_satisfaction", "data_quality", "production_readiness",
        ],
    }


def _next_pow2(n: int) -> int:
    """Return the smallest power of 2 >= n."""
    if n <= 1:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def _round_up_pow2(n: int) -> int:
    """Round n up to nearest power of 2 (for GPU cluster sizing)."""
    return _next_pow2(n)


def _parse_fp16(perf_str: str) -> float:
    text = str(perf_str)
    for tag in ("BF16", "FP16"):
        m = re.search(rf"{tag}\s*=\s*([\d.]+)\s*T", text)
        if m:
            return float(m.group(1))
    m = re.search(r"INT8\s*=\s*([\d.]+)\s*T", text)
    if m:
        return float(m.group(1)) * 0.5
    return 0.0


def _score_chip(chip, cards, est_days, target_days, prefer_domestic,
                prefer_vendor, fp16, price_wan, maturity, cloud):
    score = 0.0
    tdp = float(chip.get("tdp_w", 300) or 300)
    interconnect_bw = float(chip.get("interconnect_bw_gb_s", 0) or 0)
    has_interconnect = 1 if (chip.get("interconnect_tech") or "").strip() else 0

    # Compute power (25%)
    if fp16 > 0:
        score += fp16 / 100.0
        score += min(fp16 * cards / 500.0, 8.0)
    else:
        score -= 5.0

    # Card efficiency (15%)
    score += max(0, 6.0 - cards * 0.4)

    # Price efficiency (15%)
    if price_wan > 0 and fp16 > 0:
        score += min(fp16 / price_wan / 10.0, 5.0)
        total_cost = price_wan * cards
        if total_cost < 50:
            score += 2.5
        elif total_cost < 200:
            score += 1.5
        elif total_cost < 500:
            score += 0.5
        else:
            score -= 1.0
    elif fp16 > 0:
        score += 1.0

    # Power efficiency (10%)
    if fp16 > 0 and tdp > 0:
        score += min(fp16 * 1000 / tdp / 500.0, 4.0)

    # Ecosystem (10%)
    score += maturity * 0.8
    if cloud:
        score += 1.0

    # Interconnect (10%)
    if interconnect_bw > 0:
        score += min(interconnect_bw / 200.0, 3.0)
    if has_interconnect:
        score += 1.0

    # SLA (10%)
    if target_days and est_days is not None and est_days <= target_days:
        margin = (target_days - est_days) / max(target_days, 1)
        score += 3.0 + margin * 3.0

    # Production readiness (5%)
    status = str(chip.get("production_status", ""))
    if "量产" in status:
        score += 2.0
    elif "已发布" in status:
        score += 1.0

    # Preference
    if prefer_domestic and chip.get("vendor_region") == "domestic":
        score += 3.0
    if prefer_vendor and prefer_vendor.lower() in (chip.get("vendor", "") or "").lower():
        score += 8.0

    return round(score, 2)


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
