"""Tests for server.py — API endpoints via FastAPI TestClient."""

import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Override DB path before importing server modules
import os
os.environ["DATA_DB_PATH"] = str(Path(__file__).parent.parent / "data" / "data.db")

from fastapi.testclient import TestClient
from chip_model.server import app
from chip_model import server as server_module
from chip_model.database import get_db_path, set_db_path

client = TestClient(app)


def test_health_get():
    """GET /api/v1/health should return OK."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_health_head():
    """HEAD /api/v1/health should return 200 (not 404)."""
    resp = client.head("/api/v1/health")
    assert resp.status_code == 200, f"HEAD /health returned {resp.status_code}"


def test_spa_tab_urls_are_refresh_safe_and_public():
    """Primary SPA tabs should be directly accessible without a login overlay."""
    for path in ("/chips", "/models", "/recommend"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert 'id="main-nav"' in resp.text
        assert 'id="login-overlay"' not in resp.text


def test_primary_pages_support_embed_mode_without_a_second_service():
    """Primary pages share one SPA and can hide the platform bar in an iframe or via ?embed=1."""
    for path in ("/chips?embed=1", "/models?embed=1", "/recommend?embed=1"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "window.self!==window.top" in resp.text
        assert "document.documentElement.classList.add('embed-mode')" in resp.text
        assert "html.embed-mode header{display:none}" in resp.text
        assert "x-frame-options" not in resp.headers
        assert "frame-ancestors" not in resp.headers.get("content-security-policy", "").lower()


def test_db_status():
    """GET /api/v1/db/status should return table counts."""
    resp = client.get("/api/v1/db/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "tables" in data
    assert "chips" in data["tables"]


def test_status_page_includes_source_update_review_ui():
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "系统状态 &amp; 数据更新" in resp.text or "系统状态 & 数据更新" in resp.text
    assert 'id="source-updates-list"' in resp.text
    assert 'id="source-update-last-run"' in resp.text
    assert 'id="source-update-business-link"' in resp.text
    assert 'id="source-update-run-sources"' in resp.text
    assert "抓取开始时间" in resp.text
    assert "Hermes 心跳 · 每天北京时间 02:00" in resp.text
    assert "Gateway 与数据巡检 Skill 已启用" in resp.text
    assert "source-updates?limit=30" in resp.text


def test_source_updates_list_and_detail(tmp_path, monkeypatch):
    """The status page API exposes a bounded readable diff and candidate fields."""
    db_path = tmp_path / "updates.db"
    schema = (Path(__file__).parent.parent / "schema.sql").read_text(encoding="utf-8")
    diff_path = tmp_path / "data" / "source_snapshots" / "diffs" / "change.diff"
    diff_path.parent.mkdir(parents=True)
    diff_path.write_text("--- before\n+++ after\n-VRAM 64 GB\n+VRAM 96 GB\n", encoding="utf-8")
    with sqlite3.connect(db_path) as db:
        db.executescript(schema)
        db.execute(
            "INSERT INTO link_library (id, url, description, vendor, category) "
            "VALUES (1, 'https://vendor.example/spec', '官方规格页', 'Vendor', '芯片官方规格')"
        )
        run_id = db.execute(
            "INSERT INTO update_runs "
            "(run_type, mode, status, started_at, finished_at, counts_json, created_at) "
            "VALUES ('source_refresh', 'apply-state', 'partial', "
            "'2026-09-08T00:00:00Z', '2026-09-08T00:00:20Z', "
            "'{\"selected\": 19, \"unchanged\": 14, \"new\": 1, \"failed\": 4}', "
            "'2026-09-08T00:00:00Z')"
        ).lastrowid
        check_id = db.execute(
            "INSERT INTO source_checks "
            "(run_id, link_id, requested_url, final_url, outcome, http_status, checked_at) "
            "VALUES (?, 1, 'https://vendor.example/spec', 'https://vendor.example/spec', "
            "'changed', 200, '2026-09-08T00:00:00Z')",
            (run_id,),
        ).lastrowid
        db.execute(
            "INSERT INTO source_diffs "
            "(source_check_id, run_id, link_id, added_lines, removed_lines, diff_summary, "
            "diff_path, candidate_fields_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                check_id,
                run_id,
                1,
                1,
                1,
                "新增 1 行，删除 1 行",
                str(diff_path),
                json.dumps([{"field": "vram_gb", "label": "显存容量"}], ensure_ascii=False),
                "2026-09-08T00:00:00Z",
            ),
        )
        db.commit()

    original_path = get_db_path()
    set_db_path(db_path)
    monkeypatch.setattr(server_module, "get_project_root", lambda: tmp_path)
    try:
        listing = client.get("/api/v1/source-updates")
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["available"] is True
        assert payload["count"] == 1
        assert payload["latest_run"]["id"] == run_id
        assert payload["latest_run"]["status"] == "partial"
        assert payload["latest_run"]["counts"]["selected"] == 19
        assert payload["latest_run"]["counts"]["failed"] == 4
        assert payload["latest_run"]["duration_ms"] == 20000
        assert payload["latest_run"]["business_tables_modified"] is False
        assert len(payload["latest_run"]["sources"]) == 1
        assert payload["latest_run"]["sources"][0]["description"] == "官方规格页"
        assert payload["latest_run"]["sources"][0]["candidate_fields"][0]["field"] == "vram_gb"
        assert payload["updates"][0]["candidate_fields"][0]["field"] == "vram_gb"

        detail = client.get(f"/api/v1/source-updates/{payload['updates'][0]['id']}")
        assert detail.status_code == 200
        assert "+VRAM 96 GB" in detail.json()["diff_text"]
    finally:
        set_db_path(original_path)


def test_chips_search():
    """GET /api/v1/chips should return chip list."""
    resp = client.get("/api/v1/chips?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 0
    assert "chips" in data


def test_chips_search_with_keyword():
    """GET /api/v1/chips?keyword=H100 should work (alias for search)."""
    resp = client.get("/api/v1/chips?keyword=H100&limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] > 0


def test_chips_region_filter():
    """GET /api/v1/chips?region=domestic should filter."""
    resp = client.get("/api/v1/chips?region=domestic&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    for chip in data["chips"]:
        assert chip["vendor_region"] == "domestic"


def test_chip_profile():
    """GET /api/v1/chips/1 should return a valid chip profile."""
    resp = client.get("/api/v1/chips/1")
    assert resp.status_code == 200
    data = resp.json()
    assert "chip" in data
    # chip id=1 is seed-order dependent; just require a non-empty name
    assert data["chip"]["identity"]["chip_model"]


def test_chip_profile_not_found():
    """GET /api/v1/chips/99999 should return 404."""
    resp = client.get("/api/v1/chips/99999")
    assert resp.status_code == 404


def test_chip_recommend():
    """GET /api/v1/chips/recommend should return ranked candidates."""
    resp = client.get("/api/v1/chips/recommend?model=Qwen2.5-7B&scenario=inference&prefer_domestic=true&limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) <= 3


def test_scoring_meta_has_chinese_explanation_for_all_dimensions():
    """Every displayed scoring dimension should carry a plain Chinese explanation."""
    resp = client.get(
        "/api/v1/chips/recommend"
        "?model=Qwen2.5-7B&scenario=inference&limit=1"
    )
    assert resp.status_code == 200
    dimensions = resp.json()["scoring_meta"]["dimensions"]
    assert resp.json()["scoring_meta"]["version"] == "4.4.0"
    assert resp.json()["scoring_meta"]["category_weights"] == {
        "compute_power": 0.20,
        "cost_effectiveness": 0.10,
        "ecosystem_maturity": 0.40,
        "benchmark_evidence": 0.30,
    }
    assert len(dimensions) == 8
    assert {item["id"] for item in dimensions} == {
        "compute_perf", "bandwidth_adequacy", "power_efficiency",
        "server_count_efficiency", "framework_compat", "toolchain_compat",
        "source_credibility", "benchmark_evidence",
    }
    assert all(item.get("explain_cn", "").strip() for item in dimensions)


def test_scoring_trace_has_plain_chinese_explanations():
    """Formula, substituted values and source traces should be explained in Chinese."""
    resp = client.get("/recommend")
    assert resp.status_code == 200
    html = resp.text
    assert "SCORE_TRACE_EXPLAIN_CN" in html
    assert "dim-trace-explain\">↳ ${esc(traceCn.formula)}" in html
    assert "中文说明" not in html
    assert "💬 总结：" in html
    assert "💬 中文解释：" not in html
    assert "否（数据存在）" in html
    assert "芯片库的算力分位点分段线性评分" in html
    for dim_id in (
        "compute_perf", "bandwidth_adequacy", "power_efficiency",
        "server_count_efficiency", "framework_compat", "toolchain_compat",
        "source_credibility", "benchmark_evidence",
    ):
        assert f"{dim_id}:" in html


def test_inference_minimum_tier_uses_minimum_name_in_ui():
    """The retained weights+single-request-KV tier is presented simply as 最小."""
    resp = client.get("/recommend")
    assert resp.status_code == 200
    html = resp.text
    assert "显存需求 (最小/目标并发)" in html
    assert "卡数 (最小/目标并发)" in html
    assert "单请求完整" not in html


def test_training_uses_minimum_and_ideal_tiers_only():
    """Training exposes two meaningful tiers; the duplicate full tier stays hidden for API compatibility."""
    resp = client.get(
        "/api/v1/chips/recommend"
        "?model=Qwen2.5-7B&scenario=train&stage=sft&method=full_param"
        "&training_days=3&training_tokens=1&batch_size=1&seq_len=2048&limit=1"
    )
    assert resp.status_code == 200
    candidate = resp.json()["candidates"][0]
    rec = candidate["recommend"]
    calc = rec["card_calculation"]
    assert calc["display_tiers"] == ["minimum", "ideal"]
    assert calc["minimum"]["label"] == "最小部署"
    assert calc["ideal"]["label"] == "理想部署"
    assert calc["full"]["visible"] is False
    assert calc["full"]["deprecated"] is True
    assert rec["recommended_cards"] >= rec["vram_cards"]
    assert "最小部署" in calc["ideal"]["formula"]
    assert "全功能" not in calc["ideal"]["formula"]
    methodology = client.get("/api/v1/methodology").json()["card_estimation"]
    assert "训练仅两档" in methodology["vram_train_tiers"]
    assert "推理仅两档" in methodology["vram_inference_tiers"]
    assert "MoE 的 P_compute=每token激活参数量" in methodology["compute_train"]

    html = client.get("/recommend").text
    assert "训练数据量 ${req.training_tokens_T}T tokens" in html
    assert "目标 ${req.target_training_days} 天" in html
    assert "算力口径 ${mcalc.compute_params_b}B" in html


def test_recommend_cards_use_three_column_information_architecture():
    """Recommendation cards expose basic/reason/quantity columns with an inline deployment plan."""
    html = client.get("/recommend").text
    assert "rec-overview-grid" in html
    assert "renderChipBasicInfo(c)" in html
    assert "renderRecommendationReason(req,rec,c,scoring,i+1)" in html
    assert "renderQuantityEstimate(req,rec,c)" in html
    assert "🧩 芯片基本信息" in html
    assert "💡 芯片推荐理由" in html
    assert "🧮 芯片数量估算" in html
    assert "🔗 部署方案" in html
    assert "${renderDeploymentPanel(rec,chip)}" in html
    assert "toggleDeploymentDetail" not in html
    assert "rec-deployment-toggle" not in html
    assert "rec-scoring-breakdown" in html
    assert "rec-footer-toggle-row" not in html
    assert "font-size:1.08rem" in html
    assert "font-size:.91rem" in html
    assert "reasonCategoryOrder" in html
    assert "卡数 (最小/${idealLabel})" in html
    assert "卡数 (最小/${fullLabel}/${idealLabel})" not in html


def test_scoring_categories_show_compute_and_cost_first():
    """Display order should lead with compute/cost without changing category weights."""
    html = client.get("/recommend").text

    expected_ids = [
        "compute_power", "cost_effectiveness",
        "ecosystem_maturity", "benchmark_evidence",
    ]

    cat_order = html.split("const catOrder = [", 1)[1].split("];", 1)[0]
    assert [cat_order.index(f"id:'{item}'") for item in expected_ids] == sorted(
        cat_order.index(f"id:'{item}'") for item in expected_ids
    )

    reason_order = html.split("const reasonCategoryOrder=[", 1)[1].split("];", 1)[0]
    assert [reason_order.index(f"['{item}'") for item in expected_ids] == sorted(
        reason_order.index(f"['{item}'") for item in expected_ids
    )

    assert (
        "const categoryOrder = "
        "['compute_power','cost_effectiveness','ecosystem_maturity','benchmark_evidence'];"
    ) in html

    weights = client.get(
        "/api/v1/chips/recommend?model=Qwen2.5-7B&scenario=inference&limit=1"
    ).json()["scoring_meta"]["category_weights"]
    assert weights == {
        "compute_power": 0.20,
        "cost_effectiveness": 0.10,
        "ecosystem_maturity": 0.40,
        "benchmark_evidence": 0.30,
    }


def test_moe_recommendation_exposes_total_weight_and_card_calculation():
    """MoE active parameters must not replace resident weight parameters."""
    resp = client.get(
        "/api/v1/chips/recommend"
        "?model=Qwen3.5-35B-A3B&scenario=inference&quant=fp16"
        "&input_len=4096&output_len=512&concurrency=1&limit=1"
    )
    assert resp.status_code == 200
    data = resp.json()
    req = data["requirements"]
    assert req["model_calculation"]["is_moe"] is True
    assert req["model_calculation"]["active_params_b"] == 3.0
    assert req["model_calculation"]["weight_params_b"] == 36.0
    assert req["min_vram_gb"] == req["full_vram_gb"] == 90.1
    assert req["target_vram_gb"] == 90.1
    assert req["kv_cache_dtype"] == "BF16/FP16 (2 bytes/element)"
    assert req["context_basis"] == "input_plus_output"
    assert req["total_context"] == 4608
    assert req["kv_cache_gb"] == req["ideal_kv_gb"]
    card_calc = data["candidates"][0]["recommend"]["card_calculation"]
    assert card_calc["minimum"]["raw_cards"] == card_calc["full"]["raw_cards"]
    assert card_calc["minimum"]["rounded_cards"] == card_calc["full"]["rounded_cards"]
    assert card_calc["full"]["label"] == "最小部署"
    assert card_calc["ideal"]["label"] == "目标并发部署"
    assert card_calc["ideal"]["basis"] == "shared_pool_capacity"


def test_moe_training_uses_active_compute_but_total_weight_memory():
    """MoE training separates resident-memory and per-token-compute parameter bases."""
    resp = client.get(
        "/api/v1/chips/recommend"
        "?model=Qwen3.5-35B-A3B&scenario=train&stage=sft&method=full_param"
        "&training_days=3&training_tokens=1&batch_size=1&seq_len=2048&limit=1"
    )
    assert resp.status_code == 200
    data = resp.json()
    req = data["requirements"]
    model_calc = req["model_calculation"]
    assert model_calc["weight_params_b"] == 36.0
    assert model_calc["parameter_basis"] == "total"
    assert model_calc["compute_params_b"] == 3.0
    assert model_calc["compute_parameter_basis"] == "active"
    assert req["min_vram_gb"] == 546.7

    ideal = data["candidates"][0]["recommend"]["card_calculation"]["ideal"]
    assert ideal["compute_params_b"] == 3.0
    assert ideal["compute_parameter_basis"] == "active"
    assert ideal["training_tokens_t"] == 1.0
    assert ideal["total_flops"] == 1.8e22
    assert "6 × 3.0B（每token激活参数） × 1T tokens" in ideal["formula"]
    assert "× 10¹² ×" in ideal["formula"]


def test_chip_recommend_not_found():
    """Recommend for nonexistent model should return 404."""
    resp = client.get("/api/v1/chips/recommend?model=ZZZ_NONEXISTENT_MODEL_12345")
    assert resp.status_code == 404


def test_models_search():
    """GET /api/v1/models should return model list."""
    resp = client.get("/api/v1/models?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 0
    assert "models" in data


def test_benchmarks_search():
    """GET /api/v1/benchmarks should return benchmarks."""
    resp = client.get("/api/v1/benchmarks?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "benchmarks" in data


def test_benchmark_chip_model_alias():
    """GET /api/v1/benchmarks?chip_model=H100 should work (alias for chip)."""
    resp = client.get("/api/v1/benchmarks?chip_model=H100&limit=3")
    assert resp.status_code == 200


def test_compat_search():
    """GET /api/v1/compat should return compatibility records."""
    resp = client.get("/api/v1/compat?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "compatibilities" in data


def test_compat_benchmark_evidence():
    """Regression: benchmark_evidence must render without the missing source_* columns.

    chip_model_benchmarks has no source_type/source_url/evidence_level columns —
    source metadata is aggregated from field_provenance instead.
    """
    resp = client.get("/api/v1/compat?has_benchmark=true&limit=20")
    assert resp.status_code == 200
    data = resp.json()
    assert "compatibilities" in data
    for c in data["compatibilities"]:
        ev = c.get("benchmark_evidence", {})
        assert isinstance(ev, dict)
        assert "count" in ev
        assert "benchmarks" in ev
        # any benchmark row that has source metadata must carry the expected keys
        for b in ev["benchmarks"]:
            if b.get("source_type") or b.get("source_url"):
                assert "evidence_level" in b
                assert "confidence" in b


def test_provenance_stats():
    """GET /api/v1/provenance/stats should return aggregated stats."""
    resp = client.get("/api/v1/provenance/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "by_source_type" in data
    assert "by_confidence" in data


def test_cors_headers():
    """GET request should include CORS headers from middleware."""
    resp = client.get("/api/v1/chips?limit=1", headers={"Origin": "http://example.com"})
    assert resp.status_code == 200
    # CORS headers are set by middleware
    assert "access-control-allow-origin" in resp.headers


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  PASS: {name}")
            except AssertionError as e:
                print(f"  FAIL: {name} — {e}")
            except Exception as e:
                print(f"  ERROR: {name} — {type(e).__name__}: {e}")
    print("\nAPI tests complete!")
