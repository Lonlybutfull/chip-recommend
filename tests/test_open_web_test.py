import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from chip_model.pipeline import open_web_test as pipeline
from chip_model.pipeline.open_web_test import SearchResult
from chip_model.server import app


SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")


def test_open_web_default_visit_limit_is_ten():
    assert pipeline.run_open_web_test.__kwdefaults__["search_limit"] == 10
    assert pipeline.run_open_web_test.__kwdefaults__["visit_limit"] == 10


def make_db(path: Path) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO chips (id,chip_model,vendor,vram_gb) VALUES (1,'TestChip X1','Vendor','64')"
        )
    return path


class FakeSearch:
    name = "fixture-search"

    def search(self, query: str, limit: int):
        return [
            SearchResult(
                url="https://vendor.example/testchip-x1-spec",
                title="TestChip X1 official specifications memory bandwidth TDP",
                snippet="96 GB HBM, 2400 GB/s memory bandwidth, TDP 500 W",
                rank=1,
                query=query,
                provider=self.name,
            ),
            SearchResult(
                url="https://unrelated.example/news",
                title="Unrelated news",
                rank=2,
                query=query,
                provider=self.name,
            ),
        ][:limit]


class FakeExtractor:
    model_name = "fixture-model"

    def extract(self, *, text, url, chip_hints, skill):
        return {
            "relevant": True,
            "reason": "页面明确列出基础参数",
            "chip_model": "TestChip X1",
            "matched_categories": ["基础参数", "算力指标"],
            "source_type": "官方文档",
            "_model": self.model_name,
            "facts": [
                {
                    "field_name": "vram_gb",
                    "proposed_value": "96",
                    "unit": "GB",
                    "evidence_text": "Memory capacity: 96 GB HBM",
                    "evidence_location": "Specifications",
                    "confidence": "high",
                },
                {
                    "field_name": "tdp_w",
                    "proposed_value": "999",
                    "unit": "W",
                    "evidence_text": "not present in source",
                    "evidence_location": "Specifications",
                    "confidence": "low",
                },
            ],
        }


class FakeRefresh:
    def __init__(self, *, db_path, snapshot_dir, **kwargs):
        self.db_path = Path(db_path)
        self.snapshot_dir = Path(snapshot_dir)

    def run(self, link_ids, *, apply_state, force):
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        text_path = self.snapshot_dir / "fixture.txt"
        text_path.write_text(
            "TestChip X1\nMemory capacity: 96 GB HBM\nMemory bandwidth: 2400 GB/s\nTDP: 500 W",
            encoding="utf-8",
        )
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id,url FROM link_library WHERE id IN ("
                + ",".join("?" for _ in link_ids)
                + ")",
                list(link_ids),
            ).fetchall()
        results = [
            {
                "link_id": row[0],
                "requested_url": row[1],
                "final_url": row[1],
                "outcome": "new",
                "http_status": 200,
                "content_hash": "fixture-hash",
                "normalized_snapshot_path": str(text_path),
            }
            for row in rows
        ]
        return type("Summary", (), {"results": results})()


def test_open_web_chain_isolated_and_observable(tmp_path, monkeypatch):
    formal = make_db(tmp_path / "data.db")
    before = pipeline.database_fingerprints(formal)
    monkeypatch.setattr(pipeline, "SourceRefresher", FakeRefresh)

    result = pipeline.run_basic_spec_test(
        source_db=formal,
        chips=["TestChip X1"],
        search_provider=FakeSearch(),
        extractor=FakeExtractor(),
        search_limit=2,
        visit_limit=2,
    )

    assert result["status"] == "success"
    assert result["counts"]["validated"] == 1
    assert result["counts"]["rejected"] == 1
    assert pipeline.database_fingerprints(formal)["db"] == before["db"]
    assert result["formal_database_modified"] is False
    with sqlite3.connect(formal) as db:
        assert db.execute("SELECT vram_gb FROM chips WHERE id=1").fetchone()[0] == "64"
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='test_extracted_records'"
        ).fetchone()

    session = Path(result["db_path"]).parent
    marker = json.loads((session / "test_mode.json").read_text(encoding="utf-8"))
    assert marker["pipeline"] == "open-web-test"
    assert (session / "manifest.json").is_file()
    assert (session / "url_candidates.jsonl").is_file()
    assert (session / "events.jsonl").is_file()
    with sqlite3.connect(result["db_path"]) as db:
        assert db.execute("SELECT COUNT(*) FROM test_url_classifications").fetchone()[0] == 1
        rows = db.execute(
            "SELECT validation_status,rejection_reason FROM test_extracted_records ORDER BY id"
        ).fetchall()
        assert [row[0] for row in rows] == ["validated", "rejected"]
        assert "原文依据" in rows[1][1]


def test_open_web_run_api_reads_only_test_artifacts(tmp_path, monkeypatch):
    formal = make_db(tmp_path / "data.db")
    monkeypatch.setattr(pipeline, "SourceRefresher", FakeRefresh)
    result = pipeline.run_basic_spec_test(
        source_db=formal,
        chips=["TestChip X1"],
        search_provider=FakeSearch(),
        extractor=FakeExtractor(),
        search_limit=1,
        visit_limit=1,
    )
    monkeypatch.setenv("DATA_DB_PATH", str(formal))
    client = TestClient(app)
    listed = client.get("/api/v1/data-agent/open-web-runs")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["session_id"] == result["session_id"]
    detail = client.get(
        f"/api/v1/data-agent/open-web-runs/{result['session_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["facts"][0]["validation_status"] == "validated"
    assert detail.json()["url_assets"][0]["information_category"] == "基础参数"
    assert detail.json()["url_assets"][0]["information_categories"] == [
        "基础参数", "算力指标"
    ]
    assert detail.json()["url_assets"][0]["query_strategy"]
    assert detail.json()["url_assets"][0]["source_domain"] == "vendor.example"
    assert detail.json()["url_assets"][0]["extracted_fields"] == ["vram_gb"]
    assert detail.json()["search_plan"][0]["query"]
    assert detail.json()["search_results"][0]["title"].startswith("TestChip X1")
    assert detail.json()["search_results"][0]["status"] == "selected"


def test_open_web_run_api_exposes_optional_full_audit_summary(tmp_path, monkeypatch):
    formal = make_db(tmp_path / "data.db")
    monkeypatch.setattr(pipeline, "SourceRefresher", FakeRefresh)
    result = pipeline.run_basic_spec_test(
        source_db=formal,
        chips=["TestChip X1"],
        search_provider=FakeSearch(),
        extractor=FakeExtractor(),
        search_limit=1,
        visit_limit=1,
    )
    session = Path(result["db_path"]).parent
    report = {
        "session_id": result["session_id"],
        "existing": {
            "database_rows": 763,
            "model_links_skipped": 202,
            "safe_unique_urls": 550,
            "reachable": 408,
        },
        "new": {
            "queries": 36,
            "search_mentions": 264,
            "visited_unique_urls": 9,
            "reachable": 7,
        },
        "outcomes": {"new": 416, "retry_wait": 18, "failed": 126},
        "unsafe_existing": [{"url": "http://unsafe.example"}],
    }
    (session / "full_audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    manifest_path = session / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"skill": "all-skills", "skill_label": "全部链接检查"})
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    sample = pipeline.run_basic_spec_test(
        source_db=formal,
        chips=["SampleChip Y1"],
        search_provider=FakeSearch(),
        extractor=FakeExtractor(),
        search_limit=1,
        visit_limit=1,
    )
    failed_folder = Path(sample["db_path"]).parent
    failed_manifest_path = failed_folder / "manifest.json"
    failed_manifest = json.loads(failed_manifest_path.read_text(encoding="utf-8"))
    failed_manifest.update({
        "skill": "all-skills", "skill_label": "全部链接检查", "status": "failed"
    })
    failed_manifest_path.write_text(
        json.dumps(failed_manifest, ensure_ascii=False), encoding="utf-8"
    )
    (failed_folder / "full_audit_report.json").write_text(
        json.dumps({"session_id": sample["session_id"], "new": {"queries": 36}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DB_PATH", str(formal))
    client = TestClient(app)

    all_runs = client.get("/api/v1/data-agent/open-web-runs").json()["runs"]
    assert {item["session_id"] for item in all_runs} == {
        result["session_id"], sample["session_id"]
    }
    final_runs = client.get(
        "/api/v1/data-agent/open-web-runs?final_only=true"
    ).json()["runs"]
    assert len(final_runs) == 1
    assert final_runs[0]["session_id"] == result["session_id"]
    assert final_runs[0]["skill"] == "all-skills"

    listed = next(
        item for item in all_runs if item["session_id"] == result["session_id"]
    )
    assert listed["audit_summary"]["existing"]["reachable"] == 408
    assert "unsafe_existing" not in listed["audit_summary"]

    detail = client.get(
        f"/api/v1/data-agent/open-web-runs/{result['session_id']}"
    ).json()
    assert detail["audit_report"]["new"]["search_mentions"] == 264
    assert detail["audit_report"]["unsafe_existing"][0]["url"] == (
        "http://unsafe.example"
    )


def test_open_web_requires_explicit_target(tmp_path):
    formal = make_db(tmp_path / "data.db")
    try:
        pipeline.run_basic_spec_test(
            source_db=formal,
            chips=[],
            extra_queries=[],
            search_provider=FakeSearch(),
            extractor=FakeExtractor(),
        )
    except ValueError as exc:
        assert "至少提供" in str(exc)
    else:
        raise AssertionError("missing target should fail before creating a test session")


def test_search_fallback_uses_next_reachable_provider():
    class BrokenSearch:
        name = "broken"

        def search(self, query, limit):
            raise RuntimeError("network unavailable")

    class WorkingSearch:
        name = "working"

        def search(self, query, limit):
            return [SearchResult(
                url="https://vendor.example/spec",
                title="Official specifications",
                query=query,
                provider=self.name,
            )]

    search = pipeline.FallbackSearch.__new__(pipeline.FallbackSearch)
    search.providers = (BrokenSearch(), WorkingSearch())
    results = search.search("TestChip specs", 2)

    assert results[0].provider == "working"


def test_coarse_filter_matches_chip_tokens_in_different_order():
    result = SearchResult(
        url="https://www.nvidia.com/data-center/h100/",
        title="H100 GPU | NVIDIA",
        snippet="80 GB HBM memory bandwidth and TDP specifications",
    )

    score, reasons = pipeline._coarse_score(result, ["NVIDIA H100"])

    assert score >= 3
    assert "提到 NVIDIA H100" in reasons


def test_coarse_filter_requires_category_evidence_for_named_chip():
    news = SearchResult(
        url="https://news.example/h100-market",
        title="NVIDIA H100 market news",
        snippet="Company discusses product sales and supply",
    )

    selected, score, reasons = pipeline._coarse_selected(
        news, ["NVIDIA H100"], "chip-specs"
    )

    assert selected is False
    assert score == 2
    assert reasons == ["提到 NVIDIA H100"]


def test_coarse_filter_routes_benchmark_to_benchmark_skill_only():
    benchmark = SearchResult(
        url="https://bench.example/h100-results",
        title="NVIDIA H100 inference benchmark",
        snippet="Measured throughput, latency and concurrency results",
    )

    basic_selected, _, _ = pipeline._coarse_selected(
        benchmark, ["NVIDIA H100"], "chip-specs"
    )
    benchmark_selected, _, reasons = pipeline._coarse_selected(
        benchmark, ["NVIDIA H100"], "chip-benchmark"
    )

    assert basic_selected is False
    assert benchmark_selected is True
    assert any("实测数据线索" in reason for reason in reasons)


def test_benchmark_skill_writes_test_record_to_benchmark_target(tmp_path, monkeypatch):
    class BenchmarkSearch:
        name = "fixture-benchmark-search"

        def search(self, query, limit):
            return [SearchResult(
                url="https://bench.example/testchip-x1",
                title="TestChip X1 inference benchmark",
                snippet="Measured throughput 1000 tokens/s and latency 20 ms",
                rank=1,
                query=query,
                provider=self.name,
            )]

    class BenchmarkExtractor:
        model_name = "fixture-model"

        def extract(self, *, text, url, chip_hints, skill):
            assert skill["target_table"] == "chip_model_benchmarks"
            return {
                "relevant": True,
                "reason": "页面给出了测试条件和吞吐",
                "chip_model": "TestChip X1",
                "facts": [{
                    "field_name": "throughput_tok_s",
                    "proposed_value": "1000",
                    "unit": "tokens/s",
                    "evidence_text": "Throughput: 1000 tokens/s",
                    "evidence_location": "Results",
                    "confidence": "high",
                }],
            }

    class BenchmarkRefresh(FakeRefresh):
        def run(self, link_ids, *, apply_state, force):
            result = super().run(link_ids, apply_state=apply_state, force=force)
            path = Path(result.results[0]["normalized_snapshot_path"])
            path.write_text(
                "TestChip X1\nThroughput: 1000 tokens/s\nLatency: 20 ms",
                encoding="utf-8",
            )
            return result

    formal = make_db(tmp_path / "data.db")
    monkeypatch.setattr(pipeline, "SourceRefresher", BenchmarkRefresh)
    result = pipeline.run_open_web_test(
        source_db=formal,
        chips=["TestChip X1"],
        skill_name="chip-benchmark",
        search_provider=BenchmarkSearch(),
        extractor=BenchmarkExtractor(),
        search_limit=1,
        visit_limit=1,
    )

    assert result["status"] == "success"
    assert result["skill"] == "chip-benchmark"
    assert Path(result["db_path"]).parent.joinpath("url_assets.jsonl").is_file()
    with sqlite3.connect(result["db_path"]) as db:
        row = db.execute(
            "SELECT skill_name,target_table,field_name,proposed_value "
            "FROM test_extracted_records"
        ).fetchone()
    assert row == (
        "chip-benchmark", "chip_model_benchmarks", "throughput_tok_s", "1000"
    )


def test_unknown_open_web_skill_is_rejected(tmp_path):
    formal = make_db(tmp_path / "data.db")
    try:
        pipeline.run_open_web_test(
            source_db=formal,
            chips=["TestChip X1"],
            skill_name="model-catalog",
            search_provider=FakeSearch(),
            extractor=FakeExtractor(),
        )
    except ValueError as exc:
        assert "不支持的信息类别" in str(exc)
    else:
        raise AssertionError("unknown skill should fail before creating a test session")


def test_target_fields_drive_query_plan_and_limit_extraction(tmp_path, monkeypatch):
    formal = make_db(tmp_path / "data.db")
    monkeypatch.setattr(pipeline, "SourceRefresher", FakeRefresh)

    result = pipeline.run_open_web_test(
        source_db=formal,
        chips=["TestChip X1"],
        skill_name="chip-specs",
        target_fields=["vram_gb"],
        search_provider=FakeSearch(),
        extractor=FakeExtractor(),
        search_limit=1,
        visit_limit=1,
    )

    assert result["target_fields"] == ["vram_gb"]
    assert result["counts"]["validated"] == 1
    assert result["counts"]["rejected"] == 1
    plan = json.loads(
        Path(result["db_path"]).parent.joinpath("search_queries.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["target_fields"] == ["vram_gb"]
    assert any(item["strategy"] == "目标字段" for item in plan["query_plan"])
    assert any("显存容量" in item["query"] for item in plan["query_plan"])


def test_unknown_target_field_is_rejected_before_workspace_creation(tmp_path):
    formal = make_db(tmp_path / "data.db")
    try:
        pipeline.run_open_web_test(
            source_db=formal,
            chips=["TestChip X1"],
            skill_name="chip-specs",
            target_fields=["throughput_tok_s"],
            search_provider=FakeSearch(),
            extractor=FakeExtractor(),
        )
    except ValueError as exc:
        assert "字段不属于基础参数" in str(exc)
    else:
        raise AssertionError("cross-category target field should be rejected")


def test_precise_rejection_is_still_recorded_as_url_asset(tmp_path, monkeypatch):
    class IrrelevantExtractor:
        model_name = "fixture-model"

        def extract(self, *, text, url, chip_hints, skill):
            return {
                "relevant": False,
                "reason": "正文只有产品宣传，没有目标字段",
                "chip_model": "TestChip X1",
                "matched_categories": [],
                "facts": [],
            }

    formal = make_db(tmp_path / "data.db")
    monkeypatch.setattr(pipeline, "SourceRefresher", FakeRefresh)
    result = pipeline.run_open_web_test(
        source_db=formal,
        chips=["TestChip X1"],
        skill_name="chip-specs",
        search_provider=FakeSearch(),
        extractor=IrrelevantExtractor(),
        search_limit=1,
        visit_limit=1,
    )

    detail = pipeline.read_open_web_test_run(formal, result["session_id"])
    assert result["counts"]["url_assets"] == 1
    assert detail["url_assets"][0]["asset_status"] == "复核未通过"
    assert detail["url_assets"][0]["information_categories"] == []
    assert detail["url_assets"][0]["decision_reason"] == "正文只有产品宣传，没有目标字段"
