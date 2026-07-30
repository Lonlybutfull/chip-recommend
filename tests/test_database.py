"""Tests for database.py — CRUD operations and query functions."""

import sqlite3
import sys
import os
import tempfile
from pathlib import Path

# Ensure the parent directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from chip_model import database as db


def _make_temp_db():
    """Create a temp file database with the schema and return (conn, path).

    Uses temp file (not in-memory) because search functions open their own
    connections via get_db() and in-memory DBs are connection-scoped.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    schema_path = Path(__file__).parent.parent / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn, path


def _search_with_db_path(filters, db_path, **kwargs):
    """Call search_chips with explicit db_path."""
    return db.search_chips(filters, db_path=db_path, **kwargs)


def test_add_chip():
    """Insert a chip and verify provenance is recorded."""
    conn, path = _make_temp_db()
    fields = {
        "vendor": "NVIDIA",
        "vendor_display": "NVIDIA",
        "vendor_region": "foreign",
        "chip_series": "H100",
        "chip_model": "H100 SXM5 80GB",
        "chip_type": "GPU",
        "usage": "训推一体",
        "tier": "datacenter",
        "vram_gb": "80",
        "vram_type": "HBM3",
    }
    source = {
        "source_type": "official_datasheet",
        "source_url": "https://example.com/h100-spec",
        "source_detail": "NVIDIA official spec sheet",
        "confidence": "high",
        "is_official": "1",
        "notes": "Test data",
    }

    chip_id = db.add_chip(conn, fields, source)
    conn.commit()

    assert chip_id > 0, "add_chip should return a positive row ID"

    row = conn.execute("SELECT * FROM chips WHERE id = ?", (chip_id,)).fetchone()
    assert row is not None
    assert dict(row)["chip_model"] == "H100 SXM5 80GB"

    prov_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM field_provenance WHERE table_name='chips' AND row_id=?",
        (str(chip_id),),
    ).fetchone()["cnt"]
    assert prov_count > 0, f"Expected provenance records, got {prov_count}"

    conn.close()
    os.unlink(path)


def test_search_chips():
    """Search chips by vendor and region filters."""
    conn, path = _make_temp_db()
    source = {
        "source_type": "test",
        "source_url": "",
        "source_detail": "",
        "confidence": "high",
        "is_official": "0",
        "notes": "",
    }

    db.add_chip(conn, {
        "vendor": "NVIDIA", "vendor_display": "NVIDIA", "vendor_region": "foreign",
        "chip_series": "H100", "chip_model": "H100 SXM5 80GB",
        "chip_type": "GPU", "usage": "训推一体", "tier": "datacenter", "vram_gb": "80",
    }, source)

    db.add_chip(conn, {
        "vendor": "Huawei", "vendor_display": "华为(昇腾)", "vendor_region": "domestic",
        "chip_series": "昇腾910B", "chip_model": "昇腾910B B1 (64GB)",
        "chip_type": "NPU", "usage": "训推一体", "tier": "datacenter", "vram_gb": "64",
    }, source)
    conn.commit()
    conn.close()

    # Use db_path so search functions open their own connection to the temp DB
    result = _search_with_db_path(db.ChipFilters(search="H100"), db_path=path)
    assert result["count"] == 1, f"Expected 1 chip matching 'H100', got {result['count']}"

    result = _search_with_db_path(db.ChipFilters(region="domestic"), db_path=path)
    assert result["count"] == 1, f"Expected 1 domestic chip, got {result['count']}"

    result = _search_with_db_path(db.ChipFilters(vendor="NVIDIA"), db_path=path)
    assert result["count"] == 1, f"Expected 1 NVIDIA chip, got {result['count']}"

    os.unlink(path)


def test_chip_summary_fields():
    """Verify chip_summary includes architecture and process_node_nm."""
    row = {
        "id": 1, "vendor_display": "NVIDIA", "vendor_region": "foreign",
        "chip_series": "H100", "chip_model": "H100 SXM5 80GB", "chip_type": "GPU",
        "architecture": "Hopper", "process_node_nm": "4",
        "vram_gb": "80", "vram_type": "HBM3", "vram_bw_gb_s": "3350",
        "precision_perf": "BF16=1980TF", "tdp_w": "700",
        "interconnect_tech": "NVLink 4.0", "price_cny_wan": "18",
        "maturity_level": "5", "production_status": "已量产",
    }
    summary = db.chip_summary(row)
    assert "architecture" in summary, "architecture should be in chip summary"
    assert summary["architecture"] == "Hopper"
    assert "process_node_nm" in summary, "process_node_nm should be in chip summary"
    assert summary["process_node_nm"] == "4"


def test_usage_filter_aliases():
    """Verify usage filter accepts 'training' and 'infer' aliases."""
    conn, path = _make_temp_db()
    source = {
        "source_type": "test", "source_url": "", "source_detail": "",
        "confidence": "high", "is_official": "0", "notes": "",
    }

    db.add_chip(conn, {
        "vendor": "NVIDIA", "vendor_display": "NVIDIA", "vendor_region": "foreign",
        "chip_series": "H100", "chip_model": "H100 train chip",
        "chip_type": "GPU", "usage": "训练", "tier": "datacenter", "vram_gb": "80",
    }, source)

    db.add_chip(conn, {
        "vendor": "NVIDIA", "vendor_display": "NVIDIA", "vendor_region": "foreign",
        "chip_series": "A100", "chip_model": "A100 infer chip",
        "chip_type": "GPU", "usage": "推理", "tier": "datacenter", "vram_gb": "80",
    }, source)
    conn.commit()
    conn.close()

    # 'training' should match chips with usage '训练' or '训推一体'
    r1 = _search_with_db_path(db.ChipFilters(usage="training"), db_path=path)
    assert r1["count"] == 1, f"Expected 1 chip for 'training', got {r1['count']}"

    # 'infer' should match chips with usage '推理' or '训推一体'
    r2 = _search_with_db_path(db.ChipFilters(usage="infer"), db_path=path)
    assert r2["count"] == 1, f"Expected 1 chip for 'infer', got {r2['count']}"

    os.unlink(path)


if __name__ == "__main__":
    test_add_chip()
    test_search_chips()
    test_chip_summary_fields()
    test_usage_filter_aliases()
    print("All database tests passed!")
