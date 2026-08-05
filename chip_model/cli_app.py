#!/usr/bin/env python3
"""Parse1 CLI — AISHPerf 算力选型知识图谱查询工具 (V4).

12 commands, 5 groups — one per database table:
    chip       search / profile / recommend
    model      search / profile
    benchmark  search
    compat     search
    provenance show / stats
    db         status
    config     show / set

Schema: schema.sql (V2, 5 tables, all TEXT, field-level provenance)
"""

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# Fix Windows console encoding for Chinese characters
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import typer

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
    add_chip,
    update_chip_fields,
    add_model,
    update_model_fields,
    add_benchmark,
    add_compat,
    get_chip_benchmarks_for_model,
    get_chip_benchmark_mfu,
    get_chip_benchmark_tps,
    get_chip_model_compat_count,
    search_links,
    LinkFilters,
    add_link,
    upsert_link,
    import_links_csv,
    export_links_csv,
    get_link_library_stats,
)
from chip_model.scoring import (  # v2.0 scoring engine
    parse_fp16,
    round_up_pow2,
    RecommendContext,
    TRAIN_WEIGHTS,
    INFERENCE_WEIGHTS,
    aggregate_score,
    scoring_result_to_dict,
)

from chip_model.config import load_config, set_config

# ── App ──

app = typer.Typer(
    name="parse1",
    help="AISHPerf 算力选型智能助手 — Chip & Model CLI (V4)",
    no_args_is_help=True,
)

chip_app = typer.Typer(help="芯片查询与管理", no_args_is_help=True)
model_app = typer.Typer(help="模型查询与管理", no_args_is_help=True)
benchmark_app = typer.Typer(help="评测数据查询", no_args_is_help=True)
compat_app = typer.Typer(help="兼容性查询", no_args_is_help=True)
provenance_app = typer.Typer(help="来源追溯查询", no_args_is_help=True)
db_app = typer.Typer(help="数据库管理", no_args_is_help=True)
config_app = typer.Typer(help="配置管理", no_args_is_help=True)

link_app = typer.Typer(help="信息来源链接库管理", no_args_is_help=True)

app.add_typer(chip_app, name="chip")
app.add_typer(model_app, name="model")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(compat_app, name="compat")
app.add_typer(provenance_app, name="provenance")
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")
app.add_typer(link_app, name="link")


# ══════════════════════════════════════════════════════════════
# Global callback
# ══════════════════════════════════════════════════════════════

@app.callback()
def main(
    db_path: Optional[str] = typer.Option(
        None, "--db-path", help="SQLite 数据库路径"
    ),
    version: bool = typer.Option(False, "--version", help="显示版本"),
    server: bool = typer.Option(False, "--server", help="启动 FastAPI 服务器模式"),
    host: str = typer.Option("0.0.0.0", "--host", help="服务器 host（仅 --server）"),
    port: int = typer.Option(8000, "--port", help="服务器端口（仅 --server）"),
):
    if version:
        print("parse1 v0.4.0 — AISHPerf 算力选型智能助手 (V5)")
        raise typer.Exit()
    if db_path:
        set_db_path(db_path)
    if server:
        from server import run_server
        run_server(db_path=db_path, host=host, port=port)
        raise typer.Exit()


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _print_json(data) -> None:
    try:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(data, ensure_ascii=True, indent=2))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# ══════════════════════════════════════════════════════════════
# Write helpers — source validation + DB connection
# ══════════════════════════════════════════════════════════════

VALID_SOURCE_TYPES = [
    "official_datasheet", "official_news", "paper",
    "community", "vendor_claim", "benchmark_suite",
    "web_crawl", "llm_curated",
]
VALID_CONFIDENCE = ["high", "medium", "low"]


def _parse_source(raw: str) -> dict:
    """Parse and validate --source JSON.  Returns validated source dict
    or prints error and exits."""
    try:
        src = json.loads(raw)
    except json.JSONDecodeError as e:
        _err(f"[ERROR] --source JSON 解析失败: {e}")
        raise typer.Exit(1)

    if not isinstance(src, dict):
        _err("[ERROR] --source 必须是一个 JSON 对象")
        raise typer.Exit(1)

    # Required fields
    st = src.get("source_type", "")
    if not st:
        _err(f"[ERROR] --source 缺少必填字段 source_type，可选值: {', '.join(VALID_SOURCE_TYPES)}")
        raise typer.Exit(1)
    if st not in VALID_SOURCE_TYPES:
        _err(f"[ERROR] source_type='{st}' 不合法，可选值: {', '.join(VALID_SOURCE_TYPES)}")
        raise typer.Exit(1)

    url = src.get("source_url", "")
    if not url:
        _err("[ERROR] --source 缺少必填字段 source_url（来源 URL）")
        raise typer.Exit(1)

    cf = src.get("confidence", "medium")
    if cf not in VALID_CONFIDENCE:
        _err(f"[ERROR] confidence='{cf}' 不合法，可选值: {', '.join(VALID_CONFIDENCE)}")
        raise typer.Exit(1)

    return {
        "source_type": st,
        "source_url": url,
        "source_detail": src.get("source_detail", ""),
        "confidence": cf,
        "is_official": "1" if src.get("is_official", False) else "0",
        "notes": src.get("notes", ""),
    }


def _parse_data(raw: str) -> dict:
    """Parse --data JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _err(f"[ERROR] --data JSON 解析失败: {e}")
        raise typer.Exit(1)
    if not isinstance(data, dict):
        _err("[ERROR] --data 必须是一个 JSON 对象")
        raise typer.Exit(1)
    if not data:
        _err("[ERROR] --data 不能为空")
        raise typer.Exit(1)
    return data


def _get_write_db():
    """Open a writable DB connection."""
    path = str(get_db_path())
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ══════════════════════════════════════════════════════════════
# CHIP search
# ══════════════════════════════════════════════════════════════

@chip_app.command(name="search")
def chip_search(
    search: Optional[str] = typer.Option(
        None, "--search", "-s", help="模糊搜索（vendor / chip_model / chip_series / architecture）"
    ),
    vendor: Optional[str] = typer.Option(
        None, "--vendor", "-v", help="厂商过滤"
    ),
    region: Optional[str] = typer.Option(
        None, "--region", "-r", help="地区: domestic | foreign"
    ),
    usage: Optional[str] = typer.Option(
        None, "--usage", "-u", help="用途: train | inference | both"
    ),
    vram_min: Optional[float] = typer.Option(
        None, "--vram-min", help="最小显存(GB)"
    ),
    vram_max: Optional[float] = typer.Option(
        None, "--vram-max", help="最大显存(GB)"
    ),
    tdp_max: Optional[float] = typer.Option(
        None, "--tdp-max", help="最大 TDP(W)"
    ),
    price_max: Optional[float] = typer.Option(
        None, "--price-max", help="最高单价（万元/片）"
    ),
    interconnect_min: Optional[float] = typer.Option(
        None, "--interconnect-min", help="最小互联带宽(GB/s)"
    ),
    tier: Optional[str] = typer.Option(
        None, "--tier", help="级别: datacenter | consumer | all"
    ),
    for_model: Optional[str] = typer.Option(
        None, "--for-model", "-m", help="根据模型自动估算显存需求"
    ),
    scenario: Optional[str] = typer.Option(
        None, "--scenario", help="train | inference（配合 --for-model，默认 inference）"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """搜索芯片（模糊匹配 + 多条件筛选 + 模型驱动推算）"""
    filters = ChipFilters(
        search=search,
        vendor=vendor,
        region=region,
        usage=usage,
        vram_min=vram_min,
        vram_max=vram_max,
        tdp_max=tdp_max,
        price_max=price_max,
        interconnect_min=interconnect_min,
        tier=tier,
        for_model=for_model,
        scenario=scenario,
    )
    result = search_chips(filters, limit=limit, offset=offset)
    if result["count"] == 0:
        _err("[INFO] 0 chips matched. Try relaxing constraints.")
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# CHIP profile
# ══════════════════════════════════════════════════════════════

@chip_app.command(name="profile")
def chip_profile(
    names: list[str] = typer.Argument(..., help="芯片名称 / ID（支持多个，空格分隔。纯数字=ID查询）"),
):
    """获取芯片完整画像（按名称或ID查询，支持批量）

    Examples:
      parse1 chip profile 1                     # ID 查询
      parse1 chip profile "H100 SXM5 80GB"      # 名称查询
      parse1 chip profile 1 2 3 "A100 SXM4 80GB"  # 批量查询
    """
    if len(names) == 1:
        # Single — direct call
        result = get_chip_profile(names[0])
        if result is None:
            _err(f"[ERROR] Chip not found: {names[0]}")
            raise typer.Exit(1)
        _print_json(result)
    else:
        # Batch
        results = get_chip_profiles_batch(names)
        missing = sum(1 for r in results if r is None)
        output = {
            "count": len(results),
            "found": len(results) - missing,
            "missing": missing,
            "profiles": [r for r in results if r is not None],
        }
        _print_json(output)


# ══════════════════════════════════════════════════════════════
# CHIP recommend
# ══════════════════════════════════════════════════════════════

@chip_app.command(name="recommend")
def chip_recommend(
    model_name: str = typer.Option(
        ..., "--model", "-m", help="模型名称（模糊匹配）"
    ),
    scenario: str = typer.Option(
        "train", "--scenario", "-s", help="train | inference"
    ),
    training_days: Optional[float] = typer.Option(
        None, "--training-days", "-d", help="期望训练天数"
    ),
    sla_tps: Optional[float] = typer.Option(
        None, "--sla-tps", help="目标推理吞吐(tok/s)"
    ),
    tier: Optional[str] = typer.Option(
        "datacenter", "--tier", help="芯片级别: datacenter | all"
    ),
    training_tokens: Optional[float] = typer.Option(
        None, "--training-tokens", help="训练数据量 (T tokens)，不设则自动推算"
    ),
    max_cards: Optional[int] = typer.Option(
        None, "--max-cards", help="最大允许卡数（硬排除）"
    ),
    min_cards: Optional[int] = typer.Option(
        None, "--min-cards", help="最小允许卡数（硬下限，自动取2的幂次方）"
    ),
    max_price: Optional[float] = typer.Option(
        None, "--max-price", help="最高单价 万元/片（硬排除）"
    ),
    prefer_domestic: bool = typer.Option(
        False, "--domestic", help="优先国产芯片"
    ),
    prefer_vendor: Optional[str] = typer.Option(
        None, "--prefer-vendor", help="优先厂商（NVIDIA / AMD / Huawei / ...）"
    ),
    limit: int = typer.Option(5, "--limit", "-n", help="返回候选数量"),
):
    """推荐芯片方案（模型 × 场景 × 约束 → 多维度评分排序）

    评分维度 v3.0:
      算力 显存充裕度 价格经济性 能效比 互联扩展性
      生态成熟度(无主观评分) SLA满足度 生产就绪度 实测验证度
    """
    # 1. Find model
    model_result = search_models(ModelFilters(search=model_name), limit=1)
    if model_result["count"] == 0:
        _err(f"[ERROR] Model not found: {model_name}")
        raise typer.Exit(1)
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
        model_name, scenario=scenario, tier=tier or "datacenter",
        prefer_domestic=prefer_domestic,
    )

    if not candidates:
        _err(f"[WARN] No chip meets VRAM >= {min_vram_total:.0f}GB for {model_name}")
        raise typer.Exit(2)

    # 4. Score
    assumed_tokens_T = 0.005 if scenario == "train" else 0.0
    scored: list[dict] = []
    for chip in candidates:
        chip_dict = dict(chip)
        vram = float(chip_dict.get("vram_gb", 1))
        price_wan = float(chip_dict.get("price_cny_wan", 0) or 0)
        cloud = int(float(chip_dict.get("cloud_available", 0) or 0))

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

        # Min cards floor (round up min_cards to pow2, then enforce)
        if min_cards:
            min_cards_pow2 = _round_up_pow2(min_cards)
            if recommended_cards < min_cards_pow2:
                recommended_cards = _round_up_pow2(min_cards_pow2)

        # Hard exclude
        if max_cards and recommended_cards > max_cards:
            continue
        if max_price and price_wan and price_wan > max_price:
            continue

        meets_sla = True
        if training_days:
            if estimated_days and estimated_days > training_days:
                meets_sla = False
            elif estimated_days is None and fp16_val <= 0:
                meets_sla = False

        score = _score_chip(
            chip_dict, recommended_cards, estimated_days, training_days,
            prefer_domestic, prefer_vendor, fp16_val, price_wan, cloud,
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
        _err("[WARN] 0 chips passed hard constraints")
        raise typer.Exit(2)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:limit]

    result = {
        "model": model_summary,
        "requirements": {
            "scenario": scenario,
            "min_vram_gb": round(min_vram_total, 1),
            "target_training_days": training_days,
            "target_tokens_per_sec": sla_tps,
            "max_cards": max_cards,
            "min_cards": min_cards,
            "max_price_wan": max_price,
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

    _print_json(result)


# ── Scoring helpers ──

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
    """Extract BF16/FP16 TFLOPS from precision_perf string."""
    text = str(perf_str)
    for tag in ("BF16", "FP16"):
        m = re.search(rf"{tag}\s*=\s*([\d.]+)\s*T", text)
        if m:
            return float(m.group(1))
    m = re.search(r"INT8\s*=\s*([\d.]+)\s*T", text)
    if m:
        return float(m.group(1)) * 0.5
    return 0.0


def _score_chip(
    chip: dict,
    cards: int,
    est_days,
    target_days: Optional[float],
    prefer_domestic: bool,
    prefer_vendor: Optional[str],
    fp16: float,
    price_wan: float,
    cloud: int,
) -> float:
    """9-dimension CLI scoring, 0-50+. (v3.0: no maturity)"""
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

    # Ecosystem maturity (10%, v3.0: cloud + compat only, no subjective maturity)
    if cloud:
        score += 3.0
    compat_verified = int(float(chip.get("_compat_count", 0) or 0))
    score += min(compat_verified * 0.4, 2.0)

    # Interconnect quality (10%)
    if interconnect_bw > 0:
        score += min(interconnect_bw / 200.0, 3.0)
    if has_interconnect:
        score += 1.0

    # SLA satisfaction (10%)
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


# ══════════════════════════════════════════════════════════════
# CHIP add / update / delete
# ══════════════════════════════════════════════════════════════

@chip_app.command(name="add")
def chip_add(
    data: str = typer.Option(
        ..., "--data", "-d",
        help='芯片字段 JSON，如 \'{"vendor":"NVIDIA","vram_gb":"80",...}\''
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON，必填 source_type, source_url。可选 source_detail, confidence(default:medium), is_official, notes'
    ),
):
    """新增芯片 — 必须提供溯源信息。数据字段见 schema.sql chips 表（78列）。

    --source 必填字段:
      source_type: official_datasheet|official_news|community|vendor_claim|benchmark_suite|web_crawl|llm_curated
      source_url:  来源 URL
      source_detail: 来源中的具体位置（可选但推荐）
      confidence:  high|medium|low（默认 medium）
      is_official: true/false（默认 false）
      notes:       备注（可选）

    Example:
      cli.py chip add -d '{"vendor":"NVIDIA","chip_model":"H200 SXM 141GB","vram_gb":"141"}' \\
        -s '{"source_type":"official_datasheet","source_url":"https://...","source_detail":"NVIDIA H200 spec page","confidence":"high","is_official":true}'
    """
    fields = _parse_data(data)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        chip_id = add_chip(db, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "add", "chip_id": chip_id, "fields": list(fields.keys())})
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 新增芯片失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@chip_app.command(name="update")
def chip_update(
    chip_id: int = typer.Option(..., "--id", help="芯片 ID"),
    data: str = typer.Option(
        ..., "--data", "-d",
        help='要更新的字段 JSON，如 \'{"vram_gb":"141","tdp_w":"700"}\''
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（同 add 命令）'
    ),
):
    """更新芯片字段 — 自动读取旧值写入 field_provenance。

    Example:
      cli.py chip update --id 3 -d '{"vram_gb":"141"}' \\
        -s '{"source_type":"official_datasheet","source_url":"https://...","confidence":"high"}'
    """
    fields = _parse_data(data)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        update_chip_fields(db, chip_id, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "update", "chip_id": chip_id, "fields": list(fields.keys())})
    except ValueError as e:
        db.rollback()
        _err(f"[ERROR] {e}")
        raise typer.Exit(1)
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 更新芯片失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@chip_app.command(name="delete")
def chip_delete(
    chip_id: int = typer.Option(..., "--id", help="芯片 ID"),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（记录删除原因）。必填 source_type, source_url, notes（删除原因）'
    ),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认，直接删除"),
):
    """删除芯片 — 级联删除关联的评测/兼容/溯源记录。

    WARNING: 此操作不可逆！

    Example:
      cli.py chip delete --id 99 \\
        -s '{"source_type":"community","source_url":"","notes":"误添加的重复芯片"}'
    """
    src = _parse_source(source)
    if not src.get("notes"):
        _err("[ERROR] --source 中必须提供 notes 字段说明删除原因")
        raise typer.Exit(1)

    db = _get_write_db()
    try:
        # Verify chip exists
        chip = db.execute("SELECT id, chip_model FROM chips WHERE id = ?", (chip_id,)).fetchone()
        if chip is None:
            _err(f"[ERROR] 芯片 id={chip_id} 不存在")
            raise typer.Exit(1)
        chip_model = chip["chip_model"]

        if not force:
            # Count related records
            benchmarks = db.execute(
                "SELECT COUNT(*) as c FROM chip_model_benchmarks WHERE chip_model LIKE ?",
                (f"%{chip_model}%",)
            ).fetchone()["c"]
            compats = db.execute(
                "SELECT COUNT(*) as c FROM chip_model_compatibility WHERE chip_model LIKE ?",
                (f"%{chip_model}%",)
            ).fetchone()["c"]
            provenances = db.execute(
                "SELECT COUNT(*) as c FROM field_provenance WHERE table_name='chips' AND row_id=?",
                (str(chip_id),)
            ).fetchone()["c"]

            typer.echo(f"即将删除芯片 [{chip_id}] {chip_model}")
            typer.echo(f"  关联评测: {benchmarks} 条")
            typer.echo(f"  关联兼容: {compats} 条")
            typer.echo(f"  关联溯源: {provenances} 条")
            typer.echo(f"  删除原因: {src['notes']}")
            confirm = typer.confirm("确认删除?", default=False)
            if not confirm:
                typer.echo("已取消")
                raise typer.Exit(0)

        # Delete related records
        db.execute("DELETE FROM chip_model_benchmarks WHERE chip_model LIKE ?", (f"%{chip_model}%",))
        db.execute("DELETE FROM chip_model_compatibility WHERE chip_model LIKE ?", (f"%{chip_model}%",))
        db.execute("DELETE FROM field_provenance WHERE table_name='chips' AND row_id=?", (str(chip_id),))
        db.execute("DELETE FROM chips WHERE id = ?", (chip_id,))
        db.commit()
        _print_json({"status": "ok", "action": "delete", "chip_id": chip_id, "chip_model": chip_model})
    except typer.Exit:
        raise
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 删除芯片失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# MODEL search
# ══════════════════════════════════════════════════════════════

@model_app.command(name="search")
def model_search(
    search: Optional[str] = typer.Option(
        None, "--search", "-s", help="模糊搜索（model_id / author）"
    ),
    architecture: Optional[str] = typer.Option(
        None, "--architecture", help="dense | moe"
    ),
    params_min: Optional[float] = typer.Option(
        None, "--params-min", help="最小参数量(B)"
    ),
    params_max: Optional[float] = typer.Option(
        None, "--params-max", help="最大参数量(B)"
    ),
    for_chip: Optional[str] = typer.Option(
        None, "--for-chip", help="按芯片反查兼容模型"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """搜索模型（名称/架构/参数量 + 按芯片反查）"""
    filters = ModelFilters(
        search=search,
        architecture=architecture,
        params_min=params_min,
        params_max=params_max,
        for_chip=for_chip,
    )
    result = search_models(filters, limit=limit, offset=offset)
    if result["count"] == 0 and for_chip:
        _err(f"[INFO] No compatible models found for chip: {for_chip}")
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# MODEL profile
# ══════════════════════════════════════════════════════════════

@model_app.command(name="profile")
def model_profile(
    names: list[str] = typer.Argument(..., help="模型名称 / ID（支持多个，空格分隔。纯数字=ID查询）"),
):
    """获取模型完整画像（按名称或ID查询，支持批量）

    Examples:
      parse1 model profile 5                        # ID 查询
      parse1 model profile "Qwen/Qwen2.5-7B"        # 名称查询
      parse1 model profile 1 2 "meta-llama/Llama-3.1-8B"  # 批量查询
    """
    if len(names) == 1:
        result = get_model_profile(names[0])
        if result is None:
            _err(f"[ERROR] Model not found: {names[0]}")
            raise typer.Exit(1)
        _print_json(result)
    else:
        results = get_model_profiles_batch(names)
        missing = sum(1 for r in results if r is None)
        output = {
            "count": len(results),
            "found": len(results) - missing,
            "missing": missing,
            "profiles": [r for r in results if r is not None],
        }
        _print_json(output)


# ══════════════════════════════════════════════════════════════
# MODEL add / update
# ══════════════════════════════════════════════════════════════

@model_app.command(name="add")
def model_add(
    data: str = typer.Option(
        ..., "--data", "-d",
        help='模型字段 JSON，必填 model_id'
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（同 chip add）'
    ),
):
    """新增模型 — 必须提供溯源信息。

    Example:
      cli.py model add -d '{"model_id":"org/model-name","author":"org","total_params_b":"7"}' \\
        -s '{"source_type":"community","source_url":"https://huggingface.co/org/model-name","confidence":"high"}'
    """
    fields = _parse_data(data)
    if "model_id" not in fields:
        _err("[ERROR] --data 中必须包含 model_id 字段")
        raise typer.Exit(1)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        model_id = add_model(db, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "add", "model_id": model_id, "fields": list(fields.keys())})
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 新增模型失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@model_app.command(name="update")
def model_update(
    model_row_id: int = typer.Option(..., "--id", help="模型 ID（数据库 row id）"),
    data: str = typer.Option(
        ..., "--data", "-d",
        help='要更新的字段 JSON'
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（同 chip update）'
    ),
):
    """更新模型字段 — 自动读取旧值写入 field_provenance。

    Example:
      cli.py model update --id 5 -d '{"total_params_b":"7.0"}' \\
        -s '{"source_type":"official_datasheet","source_url":"https://...","confidence":"high"}'
    """
    fields = _parse_data(data)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        update_model_fields(db, model_row_id, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "update", "model_id": model_row_id, "fields": list(fields.keys())})
    except ValueError as e:
        db.rollback()
        _err(f"[ERROR] {e}")
        raise typer.Exit(1)
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 更新模型失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# BENCHMARK search
# ══════════════════════════════════════════════════════════════

@benchmark_app.command(name="search")
def benchmark_search(
    chip: Optional[str] = typer.Option(
        None, "--chip", help="chip_model 模糊匹配"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="model_id 模糊匹配"
    ),
    workload: Optional[str] = typer.Option(
        None, "--workload", help="inference | training"
    ),
    suite: Optional[str] = typer.Option(
        None, "--suite", help="MLPerf | vendor_doc | community"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """搜索评测数据（芯片×模型 推理/训练实测）"""
    filters = BenchmarkFilters(
        chip=chip, model=model, workload=workload, suite=suite,
    )
    result = search_benchmarks(filters, limit=limit, offset=offset)
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# BENCHMARK add
# ══════════════════════════════════════════════════════════════

@benchmark_app.command(name="add")
def benchmark_add(
    data: str = typer.Option(
        ..., "--data", "-d",
        help='评测字段 JSON，必填 chip_model, model_id, workload_type, suite_name'
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（同 chip add）'
    ),
):
    """新增评测记录 — 必须提供溯源信息。

    Example:
      cli.py benchmark add -d '{"chip_model":"H100 SXM5 80GB","model_id":"meta-llama/Llama-3.1-8B","workload_type":"inference","suite_name":"community","throughput_tok_s":"1850","precision":"FP8"}' \\
        -s '{"source_type":"community","source_url":"https://...","confidence":"medium"}'
    """
    fields = _parse_data(data)
    for required in ["chip_model", "model_id", "workload_type", "suite_name"]:
        if required not in fields:
            _err(f"[ERROR] --data 中必须包含 {required} 字段")
            raise typer.Exit(1)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        bm_id = add_benchmark(db, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "add", "benchmark_id": bm_id})
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 新增评测失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# COMPAT search
# ══════════════════════════════════════════════════════════════

@compat_app.command(name="search")
def compat_search(
    chip: Optional[str] = typer.Option(
        None, "--chip", help="chip_model 模糊匹配"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", help="model_id 模糊匹配"
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="verified | vendor_claimed | community | unsupported"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """搜索兼容性数据（芯片×模型 适配状态）"""
    filters = CompatFilters(chip=chip, model=model, status=status)
    result = search_compat(filters, limit=limit, offset=offset)
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# COMPAT add
# ══════════════════════════════════════════════════════════════

@compat_app.command(name="add")
def compat_add(
    data: str = typer.Option(
        ..., "--data", "-d",
        help='兼容性字段 JSON，必填 chip_model, model_id, compat_status'
    ),
    source: str = typer.Option(
        ..., "--source", "-s",
        help='溯源信息 JSON（同 chip add）'
    ),
):
    """新增兼容记录 — 必须提供溯源信息。

    compat_status: verified | vendor_claimed | community | unknown | unsupported

    Example:
      cli.py compat add -d '{"chip_model":"H100 SXM5 80GB","model_id":"deepseek-ai/DeepSeek-V3","compat_status":"verified","framework":"vLLM","precision":"FP8"}' \\
        -s '{"source_type":"community","source_url":"https://...","confidence":"high"}'
    """
    fields = _parse_data(data)
    for required in ["chip_model", "model_id", "compat_status"]:
        if required not in fields:
            _err(f"[ERROR] --data 中必须包含 {required} 字段")
            raise typer.Exit(1)
    valid_status = ["verified", "vendor_claimed", "community", "unknown", "unsupported"]
    if fields["compat_status"] not in valid_status:
        _err(f"[ERROR] compat_status='{fields['compat_status']}' 不合法，可选值: {', '.join(valid_status)}")
        raise typer.Exit(1)
    src = _parse_source(source)

    db = _get_write_db()
    try:
        comp_id = add_compat(db, fields, src)
        db.commit()
        _print_json({"status": "ok", "action": "add", "compat_id": comp_id})
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 新增兼容记录失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# PROVENANCE show
# ══════════════════════════════════════════════════════════════

@provenance_app.command(name="show")
def provenance_show(
    table: Optional[str] = typer.Option(
        None, "--table", "-t",
        help="目标表: chips | models | chip_model_benchmarks | chip_model_compatibility"
    ),
    row_id: Optional[str] = typer.Option(
        None, "--row-id", help="目标行 id"
    ),
    field: Optional[str] = typer.Option(
        None, "--field", "-f", help="字段名（模糊匹配）"
    ),
    source_type: Optional[str] = typer.Option(
        None, "--source-type",
        help="official_datasheet | official_news | paper | community | vendor_claim | benchmark_suite"
    ),
    confidence: Optional[str] = typer.Option(
        None, "--confidence", help="high | medium | low"
    ),
    is_official: Optional[str] = typer.Option(
        None, "--is-official", help="0 | 1"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """查询字段级来源追溯记录"""
    filters = ProvenanceFilters(
        table_name=table,
        row_id=row_id,
        field_name=field,
        source_type=source_type,
        confidence=confidence,
        is_official=is_official,
    )
    result = search_provenance(filters, limit=limit, offset=offset)
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# PROVENANCE stats
# ══════════════════════════════════════════════════════════════

@provenance_app.command(name="stats")
def provenance_stats(
    table: Optional[str] = typer.Option(
        None, "--table", "-t",
        help="限定表: chips | models | chip_model_benchmarks | chip_model_compatibility"
    ),
):
    """来源追溯统计（按表、来源类型、置信度、官方/社区聚合）"""
    result = get_provenance_stats(table=table)
    _print_json(result)


# ══════════════════════════════════════════════════════════════
# DB status
# ══════════════════════════════════════════════════════════════

@db_app.command(name="status")
def db_status():
    """显示数据库统计信息"""
    path = str(get_db_path())
    stats = get_db_stats()
    _print_json({"database": path, "tables": stats})


# ══════════════════════════════════════════════════════════════
# CONFIG show / set
# ══════════════════════════════════════════════════════════════

@config_app.command(name="show")
def config_show():
    """显示当前配置"""
    _print_json(load_config())


@config_app.command(name="set")
def config_set_cmd(
    key: str = typer.Argument(..., help="配置项（如 db.path）"),
    value: str = typer.Argument(..., help="配置值"),
):
    """设置配置项"""
    try:
        set_config(key, value)
        _print_json({"key": key, "value": value, "status": "ok"})
    except Exception as e:
        _err(f"[ERROR] Cannot write config: {e}")
        raise typer.Exit(1)


# ══════════════════════════════════════════════════════════════
# LINK search / add / import-csv / export-csv
# ══════════════════════════════════════════════════════════════

@link_app.command(name="search")
def link_search(
    search: Optional[str] = typer.Option(
        None, "--search", "-s", help="模糊搜索（url / description / vendor）"
    ),
    vendor: Optional[str] = typer.Option(
        None, "--vendor", "-v", help="厂商过滤"
    ),
    category: Optional[str] = typer.Option(
        None, "--category", "-c", help="分类过滤"
    ),
    accessible: Optional[str] = typer.Option(
        None, "--accessible", help="可访问: 是 | 否"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="返回上限"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """搜索信息来源链接库"""
    filters = LinkFilters(
        search=search,
        vendor=vendor,
        category=category,
        accessible=accessible,
    )
    result = search_links(filters, limit=limit, offset=offset)
    if result["count"] == 0:
        _err("[INFO] 0 links matched.")
    _print_json(result)


@link_app.command(name="add")
def link_add(
    data: str = typer.Option(
        ..., "--data", "-d",
        help='链接字段 JSON: {"url":"...","description":"...","vendor":"...","category":"...","access_method":"...","accessible":"...","needs_proxy":"..."}'
    ),
):
    """新增链接"""
    fields = _parse_data(data)
    if "url" not in fields:
        _err("[ERROR] --data 中必须包含 url 字段")
        raise typer.Exit(1)

    db = _get_write_db()
    try:
        link_id = add_link(db, fields)
        db.commit()
        _print_json({"status": "ok", "action": "add", "link_id": link_id})
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 新增链接失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@link_app.command(name="import-csv")
def link_import_csv(
    csv_path: str = typer.Option(
        "data/信息来源链接库_final.csv", "--csv",
        help="CSV 文件路径"
    ),
    force: bool = typer.Option(False, "--force", help="强制导入（即使库中已有数据）"),
):
    """从 CSV 导入链接库"""
    db = _get_write_db()
    try:
        count = import_links_csv(db, csv_path, force=force)
        db.commit()
        _print_json({"status": "ok", "imported": count, "csv": csv_path})
    except FileNotFoundError:
        _err(f"[ERROR] CSV 文件不存在: {csv_path}")
        raise typer.Exit(1)
    except Exception as e:
        db.rollback()
        _err(f"[ERROR] 导入失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@link_app.command(name="export-csv")
def link_export_csv(
    output: str = typer.Option("data/links_export.csv", "--output", "-o", help="输出 CSV 路径"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="按分类过滤"),
    vendor: Optional[str] = typer.Option(None, "--vendor", "-v", help="按厂商过滤"),
):
    """导出链接库到 CSV"""
    filters = LinkFilters(category=category, vendor=vendor)
    db = _get_write_db()
    try:
        count = export_links_csv(db, output, filters=filters)
        _print_json({"status": "ok", "exported": count, "output": output})
    except Exception as e:
        _err(f"[ERROR] 导出失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


@link_app.command(name="auto-discover")
def link_auto_discover(
    url: str = typer.Option(
        ..., "--url", help="发现的链接 URL"
    ),
    description: str = typer.Option(
        "", "--description", "-d", help="链接描述"
    ),
    vendor: str = typer.Option(
        "", "--vendor", "-v", help="关联厂商"
    ),
    category: str = typer.Option(
        "芯片信息综合", "--category", "-c", help="链接分类（默认: 芯片信息综合）"
    ),
    access_method: str = typer.Option(
        "web_search", "--access-method", help="获取方式: web_search | web_crawl | llm_knowledge | manual"
    ),
    accessible: str = typer.Option(
        "是", "--accessible", help="可访问: 是 | 否"
    ),
    needs_proxy: str = typer.Option(
        "否", "--needs-proxy", help="需要代理: 是 | 否"
    ),
    batch: Optional[str] = typer.Option(
        None, "--batch", "-b", help="批量导入: 指向 JSON Lines 文件"
    ),
):
    """自动发现新链接并写入 link_library（URL 去重 upsert）。

    适用于爬取/搜索过程中发现的新信息来源。如果 URL 已存在，则更新非空字段；
    如果 URL 不存在，则新增记录。

    Examples:
      parse1 link auto-discover --url "https://www.nvidia.com/en-us/data-center/h100/" \\
        --description "NVIDIA H100 官方产品页" --vendor "NVIDIA" --category "芯片官方页面"

      parse1 link auto-discover --batch data/new_links.jsonl
    """
    db = _get_write_db()
    try:
        results = []

        if batch:
            import json as _json
            batch_path = Path(batch)
            if not batch_path.exists():
                _err(f"[ERROR] 文件不存在: {batch}")
                raise typer.Exit(1)
            with open(batch_path, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            for line in lines:
                try:
                    fields = _json.loads(line)
                except _json.JSONDecodeError as e:
                    _err(f"[WARN] 跳过无效 JSON 行: {e}")
                    continue
                if "url" not in fields:
                    _err("[WARN] 跳过缺少 url 的行")
                    continue
                link_id, action = upsert_link(db, fields)
                results.append({"url": fields["url"], "action": action, "link_id": link_id})
        else:
            fields = {
                "url": url,
                "description": description,
                "vendor": vendor,
                "category": category,
                "access_method": access_method,
                "accessible": accessible,
                "needs_proxy": needs_proxy,
            }
            link_id, action = upsert_link(db, fields)
            results = [{"url": url, "action": action, "link_id": link_id}]

        inserted = sum(1 for r in results if r["action"] == "insert")
        updated = sum(1 for r in results if r["action"] == "update")

        _print_json({
            "status": "ok",
            "total": len(results),
            "inserted": inserted,
            "updated": updated,
            "results": results[:20],  # limit to 20 in output
        })
    except Exception as e:
        _err(f"[ERROR] auto-discover 失败: {e}")
        raise typer.Exit(1)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app()
