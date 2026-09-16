"""The user workbook reference changes only Flash inference measurements."""

from chip_model.database import get_db, init_db
from chip_model.measured_inference import get_comparable_measurements, score_comparable_measurements
from scripts.import_deepseek_v4_flash_benchmarks import import_reference


def test_reference_import_is_traced_idempotent_and_model_specific(tmp_path):
    copied_db = tmp_path / "data.db"
    init_db(copied_db)
    with get_db(copied_db) as db:
        db.executemany("INSERT INTO chips (chip_model) VALUES (?)", [
            ("深算三号 BW1000/BW100 (2025)",), ("C550",), ("P800 (OAM)",),
        ])
        db.commit()

    preview = import_reference(db_path=copied_db, dry_run=True)
    assert len(preview["inserted"]) == 3
    first = import_reference(db_path=copied_db)
    assert set(first["inserted"]) == {
        "深算三号 BW1000/BW100 (2025)", "C550", "P800 (OAM)",
    }
    second = import_reference(db_path=copied_db)
    assert second["inserted"] == []
    assert set(second["existing"]) == set(first["inserted"])

    with get_db(copied_db, readonly=True) as db:
        rows = db.execute(
            "SELECT id, chip_model, model_id, workload_type, input_seq_length, "
            "output_seq_length, concurrency FROM chip_model_benchmarks "
            "WHERE suite_name = 'DeepSeek-V4-Flash 20260713 curated serving reference'"
        ).fetchall()
        assert len(rows) == 3
        assert all(
            row["model_id"] == "deepseek-ai/DeepSeek-V4-Flash"
            and row["workload_type"] == "inference"
            and (row["input_seq_length"], row["output_seq_length"], row["concurrency"])
            == ("1024", "1024", "1")
            for row in rows
        )
        for row in rows:
            provenance = db.execute(
                "SELECT COUNT(*) FROM field_provenance WHERE table_name = ? AND row_id = ?",
                ("chip_model_benchmarks", str(row["id"])),
            ).fetchone()[0]
            assert provenance >= 15

    names = first["inserted"]
    measured = get_comparable_measurements("deepseek-ai/DeepSeek-V4-Flash", names, copied_db)
    assert set(measured) == set(names)
    assert all(item["sources"] for item in measured.values())
    assert score_comparable_measurements(measured)
    assert get_comparable_measurements("deepseek-ai/DeepSeek-V4-Pro", names, copied_db) == {}
