import sqlite3

from chip_model.measured_inference import (
    get_comparable_measurements,
    score_comparable_measurements,
)


def test_only_exact_model_and_reference_workload_are_scored(tmp_path):
    db_path = tmp_path / "measurements.db"
    db = sqlite3.connect(db_path)
    db.execute("""CREATE TABLE chip_model_benchmarks (
        id INTEGER PRIMARY KEY, chip_model TEXT, model_id TEXT, suite_name TEXT,
        workload_type TEXT, hardware_config TEXT, chip_count TEXT,
        framework TEXT, precision TEXT, input_seq_length TEXT,
        output_seq_length TEXT, concurrency TEXT, throughput_tok_s TEXT,
        time_to_first_token_ms TEXT, tpot_ms TEXT, test_date TEXT, notes TEXT
    )""")
    rows = [
        (1, "chip-A", "deepseek-ai/DeepSeek-V4-Flash", "test", "inference", "1 node", "4", "vllm", "fp8", "1024", "1024", "1", "400", "50", "10", "2026-07-13"),
        (2, "chip-A", "deepseek-ai/DeepSeek-V4-Flash", "test", "inference", "1 node", "4", "vllm", "fp8", "1024", "1024", "1", "800", "70", "14", "2026-07-13"),
        (3, "chip-B", "deepseek-ai/DeepSeek-V4-Flash", "test", "inference", "1 node", "2", "vllm", "fp8", "1024", "1024", "1", "200", "80", "20", "2026-07-13"),
        (4, "chip-C", "deepseek-ai/DeepSeek-V4-Pro", "test", "inference", "1 node", "4", "vllm", "fp8", "1024", "1024", "1", "10000", "1", "1", "2026-07-13"),
        (5, "chip-C", "deepseek-ai/DeepSeek-V4-Flash", "test", "inference", "1 node", "4", "vllm", "fp8", "4096", "1024", "1", "10000", "1", "1", "2026-07-13"),
        (6, "chip-C", "deepseek-ai/DeepSeek-V4-Flash", "test", "inference", "1 node", "", "vllm", "fp8", "1024", "1024", "1", "10000", "1", "1", "2026-07-13"),
    ]
    db.executemany("INSERT INTO chip_model_benchmarks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [r + (None,) for r in rows])
    db.commit()
    db.close()

    measurements = get_comparable_measurements(
        "deepseek-ai/DeepSeek-V4-Flash", ["chip-A", "chip-B", "chip-C"], db_path,
    )
    assert set(measurements) == {"chip-A", "chip-B"}
    assert measurements["chip-A"]["throughput_per_card"] == 150
    assert measurements["chip-A"]["ttft_ms"] == 60
    assert measurements["chip-A"]["record_ids"] == [1, 2]

    scores = score_comparable_measurements(measurements)
    assert scores["chip-A"]["score"] > scores["chip-B"]["score"]
    assert scores["chip-A"]["score"] == 100
    assert score_comparable_measurements({}) == {}


def test_flash_api_uses_neutral_fallback_and_then_measured_first(monkeypatch):
    from fastapi.testclient import TestClient
    from chip_model import server

    client = TestClient(server.app)
    monkeypatch.setattr(server, "get_comparable_measurements", lambda *_: {})
    params = {
        "model": "DeepSeek-V4-Flash",
        "scenario": "inference",
        "quant": "int4_gptq",
        "limit": 20,
    }
    baseline = client.get("/api/v1/chips/recommend", params=params)
    assert baseline.status_code == 200
    data = baseline.json()
    assert data["requirements"]["measured_ranking"]["enabled"] is False
    assert {c["scoring"]["categories"]["benchmark_evidence"]["score"]
            for c in data["candidates"]} == {50.0}

    measured_chip = data["candidates"][-1]["chip_model"]
    monkeypatch.setattr(server, "get_comparable_measurements", lambda *_: {
        measured_chip: {
            "throughput_per_card": 100.0,
            "ttft_ms": 50.0,
            "tpot_ms": 10.0,
            "record_ids": [123],
            "card_counts": [4],
            "frameworks": ["vllm"],
            "precision": ["fp8"],
        }
    })
    with_measurement = client.get("/api/v1/chips/recommend", params=params)
    assert with_measurement.status_code == 200
    ranked = with_measurement.json()
    assert ranked["requirements"]["measured_ranking"]["enabled"] is True
    assert ranked["scoring_meta"]["category_weights"]["benchmark_evidence"] == 0.60
    assert ranked["candidates"][0]["chip_model"] == measured_chip
    assert ranked["candidates"][0]["recommend"]["measured_inference"]["score"] == 70.0
