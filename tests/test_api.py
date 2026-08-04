"""Tests for server.py — API endpoints via FastAPI TestClient."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Override DB path before importing server modules
import os
os.environ["DATA_DB_PATH"] = str(Path(__file__).parent.parent / "data" / "data.db")

from fastapi.testclient import TestClient
from chip_model.server import app

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


def test_db_status():
    """GET /api/v1/db/status should return table counts."""
    resp = client.get("/api/v1/db/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "tables" in data
    assert "chips" in data["tables"]


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
