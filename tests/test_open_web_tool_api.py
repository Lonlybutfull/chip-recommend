import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from chip_model.open_web_tool_server import create_open_web_tool_app
from chip_model.pipeline.hermes_open_web_state import create_hermes_open_web_run
from chip_model.pipeline.open_web_test import SearchResult, database_fingerprints


SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO chips (id,chip_model,vendor,vram_gb) "
            "VALUES (1,'TestChip X1','Vendor','64')"
        )
    return path


class FakeSearch:
    name = "fixture-search"

    def search(self, query: str, limit: int):
        return [
            SearchResult(
                url="https://vendor.example/testchip-x1",
                title="TestChip X1 official specifications",
                snippet="96 GB HBM and 2400 GB/s bandwidth",
                rank=1,
                query=query,
                provider=self.name,
            ),
            SearchResult(
                url="https://bench.example/testchip-x1",
                title="TestChip X1 benchmark",
                snippet="Measured throughput and latency",
                rank=2,
                query=query,
                provider=self.name,
            ),
        ][:limit]


class FakeRefresh:
    def __init__(self, *, db_path, snapshot_dir, **kwargs):
        self.db_path = Path(db_path)
        self.snapshot_dir = Path(snapshot_dir)

    def run(self, link_ids, *, apply_state, force):
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            placeholders = ",".join("?" for _ in link_ids)
            rows = db.execute(
                f"SELECT id,url FROM link_library WHERE id IN ({placeholders})", list(link_ids)
            ).fetchall()
        results = []
        for link_id, url in rows:
            snapshot = self.snapshot_dir / f"{link_id}.txt"
            snapshot.write_text(
                "TestChip X1 official page. Memory capacity: 96 GB HBM. "
                "Memory bandwidth: 2400 GB/s. Benchmark throughput: 1000 tokens/s.",
                encoding="utf-8",
            )
            results.append({
                "link_id": link_id,
                "requested_url": url,
                "final_url": url,
                "outcome": "new",
                "http_status": 200,
                "content_hash": f"hash-{link_id}",
                "normalized_snapshot_path": str(snapshot),
            })
        return type("Summary", (), {"results": results})()


class FakeExtractor:
    model_name = "kimi-fixture"

    def extract(self, *, text, url, chip_hints, skill):
        field = next(iter(skill["fields"]))
        value = "96" if field == "vram_gb" else "1000"
        evidence = (
            "Memory capacity: 96 GB HBM"
            if field == "vram_gb"
            else "Benchmark throughput: 1000 tokens/s"
        )
        return {
            "relevant": True,
            "reason": "正文有目标字段",
            "chip_model": "TestChip X1",
            "matched_categories": [skill["label"], "实测数据"],
            "source_type": "官方文档",
            "_model": self.model_name,
            "facts": [{
                "field_name": field,
                "proposed_value": value,
                "unit": "GB" if field == "vram_gb" else "tokens/s",
                "evidence_text": evidence,
                "evidence_location": "正文",
                "confidence": "high",
            }],
        }


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer local-test-token"}


def _queries() -> list[dict[str, str]]:
    return [
        {"query": f"TestChip X1 official specs {index}", "reason": "覆盖官方资料"}
        for index in range(10)
    ]


def test_tool_service_search_preview_select_and_extract(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    before = database_fingerprints(formal)
    run = create_hermes_open_web_run(
        source_db=formal, skill="chip-specs", target_chip="TestChip X1",
        target_fields=["vram_gb"],
    )
    app = create_open_web_tool_app(
        source_db=formal,
        token="local-test-token",
        search_provider_factory=lambda proxy=None: FakeSearch(),
        refresher_factory=FakeRefresh,
        extractor_factory=lambda: FakeExtractor(),
    )
    client = TestClient(app)

    unauthorized = client.post("/v1/search", json={})
    assert unauthorized.status_code == 401

    searched = client.post("/v1/search", headers=_headers(), json={
        "run_id": run["session_id"],
        "skill": "chip-specs",
        "target_chip": "TestChip X1",
        "queries": _queries(),
    })
    assert searched.status_code == 200, searched.text
    search_body = searched.json()
    assert search_body["raw_result_count"] == 20
    assert search_body["unique_candidate_count"] == 2
    assert search_body["candidate_ids"] == [
        row["candidate_id"] for row in search_body["candidates"]
    ]
    assert len(search_body["candidate_ids"]) == search_body["unique_candidate_count"]
    assert all("mentions" not in row for row in search_body["candidates"])
    assert all(len(row["title"]) <= 160 for row in search_body["candidates"])
    assert all(len(row["snippet"]) <= 240 for row in search_body["candidates"])

    candidate_ids = search_body["candidate_ids"]
    previewed = client.post("/v1/preview", headers=_headers(), json={
        "run_id": run["session_id"], "candidate_ids": candidate_ids,
    })
    assert previewed.status_code == 200, previewed.text
    previews = previewed.json()["previews"]
    assert len(previews) == 2
    assert all(len(row["core_text"]) <= 500 for row in previews)
    assert all(row["access_status"] == "reachable" for row in previews)

    decisions = []
    for index, candidate_id in enumerate(candidate_ids):
        decisions.append({
            "candidate_id": candidate_id,
            "selected": index == 0,
            "reason": "官方页面包含显存规格" if index == 0 else "仅有评测摘要",
            "matched_categories": ["基础参数", "实测数据"],
            "suggested_skills": ["chip-benchmark"] if index == 0 else [],
        })
    submitted = client.post("/v1/submit-selection", headers=_headers(), json={
        "run_id": run["session_id"], "skill": "chip-specs", "decisions": decisions,
    })
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["selected_count"] == 1
    assert body["validated_fact_count"] >= 1
    assert body["linked_task_count"] == 1
    assert database_fingerprints(formal)["db"] == before["db"]


def test_submit_selection_rejects_unknown_candidate(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    run = create_hermes_open_web_run(
        source_db=formal, skill="chip-specs", target_chip="TestChip X1",
    )
    app = create_open_web_tool_app(source_db=formal, token="local-test-token")
    response = TestClient(app).post("/v1/submit-selection", headers=_headers(), json={
        "run_id": run["session_id"],
        "skill": "chip-specs",
        "decisions": [{
            "candidate_id": "candidate-abcdef123456",
            "selected": True,
            "reason": "有价值",
            "matched_categories": ["基础参数"],
            "suggested_skills": [],
        }],
    })
    assert response.status_code == 409
