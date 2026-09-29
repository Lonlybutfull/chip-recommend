import sqlite3
from pathlib import Path

from chip_model.pipeline import open_web_full_search as full_search
from chip_model.pipeline.open_web_full_search import (
    DEFAULT_CHIPS,
    build_all_skill_query_plan,
    keyword_information_categories,
    select_global_candidates,
)
from chip_model.pipeline.open_web_test import (
    FallbackSearch,
    SearchResult,
    read_open_web_test_run,
)


SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")


def test_all_skill_plan_has_six_categories_two_queries_and_three_chips():
    plan = build_all_skill_query_plan(list(DEFAULT_CHIPS))
    assert len(plan) == 36
    assert len({row["skill"] for row in plan}) == 6
    assert all(row["query"] and row["chip"] and row["strategy"] for row in plan)
    assert all(f'"{row["chip"]}"' in row["query"] for row in plan)


def test_global_candidate_limit_is_ten_and_deduplicates_urls():
    rows = [
        {"url": f"https://example.com/{index % 12}", "status": "selected",
         "skill": "chip-specs" if index < 12 else "chip-compute",
         "coarse_score": index, "rank": index + 1}
        for index in range(20)
    ]
    selected = select_global_candidates(rows, 10)
    assert len(selected) == 10
    assert len({row["url"] for row in selected}) == 10
    duplicate = next(row for row in selected if row["url"] == "https://example.com/7")
    assert set(duplicate["matched_skills"]) == {"chip-specs", "chip-compute"}


def test_fallback_search_interleaves_sources_before_applying_limit():
    class Provider:
        def __init__(self, name, prefix):
            self.name, self.prefix = name, prefix

        def search(self, query, limit):
            return [SearchResult(url=f"https://{self.prefix}.example/{i}", provider=self.name)
                    for i in range(limit)]

    search = FallbackSearch()
    search.providers = (Provider("first", "one"), Provider("second", "two"),
                        Provider("unused", "three"))
    rows = search.search("chip", 4)
    assert [row.provider for row in rows] == ["first", "second", "first", "second"]


def test_keyword_category_fallback_requires_two_signal_groups():
    categories = keyword_information_categories(
        "H100 benchmark throughput 12000 tokens/s, latency 35 ms, concurrency 16."
    )
    assert "实测数据" in categories
    assert "芯片型号" not in categories


def test_all_skill_run_checks_existing_links_and_keeps_formal_db_unchanged(
    tmp_path, monkeypatch
):
    formal = tmp_path / "data.db"
    with sqlite3.connect(formal) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO chips (id,chip_model,vendor) VALUES (1,'TestChip X1','Vendor')"
        )
        db.execute(
            "INSERT INTO link_library (url,description,vendor,category) VALUES (?,?,?,?)",
            (
                "https://vendor.example/existing-spec",
                "TestChip X1 official product specification and benchmark",
                "Vendor",
                "芯片硬件信息",
            ),
        )
        db.execute(
            "INSERT INTO link_library (url,description,vendor,category) VALUES (?,?,?,?)",
            (
                "https://huggingface.co/vendor/model",
                "HF 模型",
                "Vendor",
                "模型信息",
            ),
        )
        db.commit()

    class Search:
        name = "fixture-search"

        def search(self, query, limit):
            return [SearchResult(
                url="https://vendor.example/new-audit-page",
                title="TestChip X1 product series vendor release specification datasheet",
                snippet=(
                    "memory HBM bandwidth TDP precision FP16 TFLOPS compute unit "
                    "compatibility supported framework PyTorch model benchmark throughput "
                    "tokens/s latency concurrency input tokens deployment vLLM docker topology"
                ),
                rank=1,
                query=query,
                provider=self.name,
            )][:limit]

    class Extractor:
        model_name = "fixture-model"

        def extract(self, *, text, url, chip_hints, skill):
            return {
                "relevant": True,
                "reason": "fixture page contains the requested category",
                "chip_model": "TestChip X1",
                "matched_categories": [
                    value["label"] for value in full_search.TEST_SKILL_REGISTRY.values()
                ],
                "facts": [],
            }

    class Refresh:
        def __init__(self, *, db_path, snapshot_dir, **kwargs):
            self.db_path = Path(db_path)
            self.snapshot_dir = Path(snapshot_dir)

        def run(self, link_ids, *, apply_state, force):
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot = self.snapshot_dir / "fixture.txt"
            snapshot.write_text(
                "TestChip X1 product series release memory HBM bandwidth TDP "
                "FP16 TFLOPS compatibility PyTorch benchmark throughput latency "
                "concurrency deployment vLLM docker topology",
                encoding="utf-8",
            )
            placeholders = ",".join("?" for _ in link_ids)
            with sqlite3.connect(self.db_path) as db:
                rows = db.execute(
                    f"SELECT id,url FROM link_library WHERE id IN ({placeholders})",
                    list(link_ids),
                ).fetchall()
            return type("Summary", (), {"results": [
                {
                    "link_id": row[0],
                    "requested_url": row[1],
                    "final_url": row[1],
                    "outcome": "new",
                    "http_status": 200,
                    "content_hash": f"fixture-{row[0]}",
                    "normalized_snapshot_path": str(snapshot),
                }
                for row in rows
            ]})()

    monkeypatch.setattr(full_search, "SourceRefresher", Refresh)
    before = full_search.database_fingerprints(formal)
    result = full_search.run_all_skill_search(
        source_db=formal,
        chips=["TestChip X1"],
        search_limit=1,
        visit_limit=10,
        search_provider=Search(),
        extractor=Extractor(),
    )

    assert result["status"] == "success"
    assert result["formal_database_modified"] is False
    assert full_search.database_fingerprints(formal)["db"] == before["db"]
    detail = read_open_web_test_run(formal, result["session_id"])
    report = detail["audit_report"]
    assert report["existing"] == {
        "database_rows": 2,
        "model_links_skipped": 1,
        "safe_unique_urls": 1,
        "visited": 1,
        "reachable": 1,
    }
    assert report["new"]["queries"] == 12
    assert report["new"]["visited_unique_urls"] == 1
    assert report["new"]["reachable"] == 1
    assert len(report["new"]["selected_by_skill"]) == 6
    assert len(detail["url_assets"]) == 2
    assert any(
        row["search_provider"] == "link_library" for row in detail["url_assets"]
    )
    assert any(
        row["search_provider"] == "fixture-search" for row in detail["url_assets"]
    )
