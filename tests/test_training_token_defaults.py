"""Regression tests for stage-specific training token defaults."""

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["DATA_DB_PATH"] = str(PROJECT_ROOT / "data" / "data.db")

from chip_model.server import app  # noqa: E402


client = TestClient(app)


@pytest.mark.parametrize(
    ("stage", "expected_tokens_t"),
    [("cpt", 10.0), ("sft", 0.2), ("rl", 0.2)],
)
def test_training_tokens_default_by_stage(stage, expected_tokens_t):
    response = client.get(
        "/api/v1/chips/recommend",
        params={
            "model": "Qwen2.5-7B",
            "scenario": "train",
            "stage": stage,
            "training_days": 3,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requirements"]["training_tokens_T"] == expected_tokens_t
    ideal = payload["candidates"][0]["recommend"]["card_calculation"]["ideal"]
    assert ideal["training_tokens_t"] == expected_tokens_t


def test_explicit_training_tokens_override_stage_default():
    response = client.get(
        "/api/v1/chips/recommend",
        params={
            "model": "Qwen2.5-7B",
            "scenario": "train",
            "stage": "cpt",
            "training_tokens": 1.5,
            "training_days": 3,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["requirements"]["training_tokens_T"] == 1.5


def test_recommend_form_exposes_stage_defaults():
    html = client.get("/recommend").text

    assert "Object.freeze({cpt:10, sft:0.2, rl:0.2})" in html
    assert 'id="rec-tokens" value="0.2"' in html
    assert "applyTrainingTokenDefault(this.value)" in html


def test_recommend_form_keeps_advanced_training_inputs_hidden():
    html = client.get("/recommend").text
    init_block = html[html.index("function initRecommend(){") : html.index("function updateRecForm(){")]

    assert "updateRecForm();" in init_block
    assert 'id="rec-batch-size" value="1"' in init_block
    assert 'id="rec-seq-len" value="2048"' in init_block
    assert html.count("batchGrp.style.display='none'; seqGrp.style.display='none';") == 3


def test_direct_recommend_route_refreshes_vendor_options_after_async_load():
    html = client.get("/recommend").text

    assert "厂商加载中…" in html
    assert "function refreshRecommendVendorOptions()" in html
    assert "STATE.vendors = [...new Set(CHIP_LIST.map(c=>c.vendor).filter(Boolean))].sort();" in html
    assert "ensureChipList().then(refreshRecommendVendorOptions)" in html
