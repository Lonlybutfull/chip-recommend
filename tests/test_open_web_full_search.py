from chip_model.pipeline.open_web_full_search import (
    DEFAULT_CHIPS,
    build_all_skill_query_plan,
    keyword_information_categories,
    select_global_candidates,
)
from chip_model.pipeline.open_web_test import FallbackSearch, SearchResult


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
