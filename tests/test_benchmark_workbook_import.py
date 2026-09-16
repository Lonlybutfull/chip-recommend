"""Full workbook is archived without contaminating model-specific ranking."""

import json
from pathlib import Path

from chip_model.database import get_db, init_db
from chip_model.measured_inference import get_comparable_measurements
from scripts.import_benchmark_workbook import import_workbook
from scripts.import_deepseek_v4_flash_benchmarks import import_reference


def test_all_rows_archived_idempotently_and_flash_ranking_stays_comparable(tmp_path):
    db_path = tmp_path / "data.db"
    init_db(db_path)
    with get_db(db_path) as db:
        db.executemany("INSERT INTO chips (chip_model) VALUES (?)", [
            ("深算三号 BW1000/BW100 (2025)",), ("C550",), ("P800 (OAM)",),
        ])
        db.commit()
    import_reference(db_path=db_path)

    preview = import_workbook(db_path=db_path, dry_run=True)
    assert preview["sheets"] == 20
    assert preview["archived_rows"] == 881
    assert preview["inference_observations"] == 720

    first = import_workbook(db_path=db_path)
    assert first["archived_rows_inserted"] == 881
    assert first["flash_benchmarks_inserted"] > 100
    second = import_workbook(db_path=db_path)
    assert second["archived_rows_inserted"] == 0
    assert second["archived_rows_existing"] == 881
    assert second["flash_benchmarks_inserted"] == 0

    with get_db(db_path, readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM benchmark_workbook_rows").fetchone()[0] == 881
        assert db.execute(
            "SELECT COUNT(*) FROM benchmark_workbook_rows WHERE source_sheet IN (?,?,?)",
            ("适配性测试", "910B能耗测试", "Qwen3.8-27B 910B MTP"),
        ).fetchone()[0] > 0
        row = db.execute(
            "SELECT id, cells_json, input_tokens, output_tokens, concurrency, "
            "output_throughput_tok_s FROM benchmark_workbook_rows "
            "WHERE source_sheet = ? AND source_row = 6",
            ("海光BW1000性能评测（第二次）",),
        ).fetchone()
        assert row["input_tokens"] == "1024"
        assert row["output_tokens"] == "1024"
        assert row["concurrency"] == "1"
        assert float(row["output_throughput_tok_s"]) == 36.6
        assert json.loads(row["cells_json"])[0] == "I_1024_O_1024_C_1"
        assert db.execute(
            "SELECT COUNT(*) FROM field_provenance WHERE table_name = ? AND row_id = ?",
            ("benchmark_workbook_rows", str(row["id"])),
        ).fetchone()[0] >= 1

    names = ["深算三号 BW1000/BW100 (2025)", "C550", "P800 (OAM)"]
    measured = get_comparable_measurements("deepseek-ai/DeepSeek-V4-Flash", names, db_path)
    assert set(measured) == set(names)
    assert all(item["record_ids"] for item in measured.values())
    assert len(measured["深算三号 BW1000/BW100 (2025)"]["record_ids"]) == 1
