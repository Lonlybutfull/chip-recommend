"""All-category open-web search that writes only an isolated test workspace."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chip_model.pipeline.data_agent import canonicalize_source_url
from chip_model.pipeline.open_web_test import (
    SCHEMA_VERSION,
    TEST_SKILL_REGISTRY,
    FallbackSearch,
    OpenAICompatibleExtractor,
    SearchProvider,
    SemanticExtractor,
    _append_jsonl,
    _atomic_json,
    _coarse_selected,
    _ensure_test_tables,
    _insert_test_links,
    _now,
    _source_shape,
    _validate_facts,
    database_fingerprints,
    list_open_web_test_runs,
    read_open_web_test_run,
)
from chip_model.pipeline.source_refresh import SourceRefresher, validate_source_url
from chip_model.pipeline.test_workspace import create_test_workspace
from chip_model.pipeline.test_workspace import runs_root


DEFAULT_CHIPS = ("NVIDIA H100", "AMD MI300X", "华为昇腾 910B")


def keyword_information_categories(text: str) -> list[str]:
    """Provide an auditable category fallback when the semantic API is unavailable."""
    folded = text.casefold()
    matched: list[str] = []
    for skill in TEST_SKILL_REGISTRY.values():
        hit_groups = sum(
            1 for group in skill["coarse_keywords"]
            if any(token.casefold() in folded for token in group)
        )
        if hit_groups >= 2:
            matched.append(skill["label"])
    return matched


def reclassify_run_with_keywords(
    *, source_db: str | Path, session_id: str
) -> dict[str, Any]:
    """Reclassify one isolated run from saved snapshots without network access."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise ValueError("无效测试会话标识。")
    root = runs_root(source_db)
    folder = (root / session_id).resolve()
    folder.relative_to(root)
    marker = json.loads((folder / "test_mode.json").read_text(encoding="utf-8"))
    if marker.get("mode") != "test" or marker.get("pipeline") != "open-web-test":
        raise ValueError("不是开放互联网测试会话。")
    assets_path = folder / "url_assets.jsonl"
    assets = [json.loads(line) for line in assets_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    category_counts: Counter[str] = Counter()
    relevant_count = 0
    updated = 0
    with sqlite3.connect(folder / "data.db") as db:
        for asset in assets:
            if (asset.get("search_provider") == "link_library"
                    or asset.get("query_strategy") == "已有资产复查"):
                continue
            snapshot = Path(str(asset.get("snapshot_path") or ""))
            if asset.get("fetch_status") not in {"new", "changed", "unchanged"} or not snapshot.is_file():
                continue
            categories = keyword_information_categories(snapshot.read_text(encoding="utf-8"))
            primary = TEST_SKILL_REGISTRY.get(str(asset.get("skill") or ""), {}).get("label", "")
            relevant = bool(primary and primary in categories)
            asset["information_categories"] = categories
            asset["information_category"] = primary if relevant else ""
            asset["precise_relevant"] = relevant
            asset["asset_status"] = "已分类" if categories else "待复核"
            asset["decision_reason"] = (
                f"模型接口不可用，已按 Skill 关键词备用分类："
                f"{'、'.join(categories) or '未命中'}"
            )
            for label in categories:
                category_counts[label] += 1
            relevant_count += int(relevant)
            updated += 1
            db.execute(
                "UPDATE test_url_classifications SET precise_relevant=?,precise_reason=?,"
                "matched_categories_json=? WHERE skill_name=? AND canonical_url=?",
                (1 if relevant else 0, asset["decision_reason"],
                 json.dumps(categories, ensure_ascii=False), asset.get("skill"), asset.get("url")),
            )
        db.commit()
    temporary = assets_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in assets),
        encoding="utf-8",
    )
    temporary.replace(assets_path)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("counts", {})["precise_passed"] = relevant_count
    manifest["keyword_fallback_classified"] = updated
    _atomic_json(manifest_path, manifest)
    report_path = folder / "full_audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["matched_information_categories"] = dict(category_counts)
    report["keyword_fallback_classified"] = updated
    _atomic_json(report_path, report)
    return {"session_id": session_id, "classified": updated,
            "primary_relevant": relevant_count, "categories": dict(category_counts)}


def build_all_skill_query_plan(chips: list[str]) -> list[dict[str, str]]:
    """Use two complementary queries per chip and Skill (36 for defaults)."""
    plan: list[dict[str, str]] = []
    for skill_name, skill in TEST_SKILL_REGISTRY.items():
        templates = (
            (skill["query_templates"][0], "类别关键词"),
            (skill["source_query_templates"][0], "来源类型"),
        )
        for chip in chips:
            name = chip.strip()
            if not name:
                continue
            for template, strategy in templates:
                plan.append({
                    "query": template.format(chip=f'"{name}"'),
                    "strategy": strategy,
                    "skill": skill_name,
                    "chip": name,
                })
    return plan


def select_global_candidates(
    rows: list[dict[str, Any]], limit: int = 10
) -> list[dict[str, Any]]:
    """Select the strongest unique URLs globally while retaining Skill matches."""
    by_url: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("status") != "selected":
            continue
        url = str(row.get("url") or "")
        old = by_url.get(url)
        if old is None:
            chosen = dict(row)
            chosen["matched_skills"] = [row["skill"]]
            by_url[url] = chosen
            continue
        old["matched_skills"] = list(dict.fromkeys([
            *old.get("matched_skills", []), row["skill"]
        ]))
        if (int(row.get("coarse_score") or 0), -int(row.get("rank") or 0)) > (
            int(old.get("coarse_score") or 0), -int(old.get("rank") or 0)
        ):
            matched = old["matched_skills"]
            old.clear()
            old.update(row)
            old["matched_skills"] = matched
    return sorted(
        by_url.values(),
        key=lambda item: (-int(item.get("coarse_score") or 0), int(item.get("rank") or 0)),
    )[:limit]


def _previous_audit(source_db: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    runs = list_open_web_test_runs(source_db, final_only=True)
    if not runs:
        return {}, []
    detail = read_open_web_test_run(source_db, str(runs[0]["session_id"]))
    report = detail.get("audit_report") or {}
    assets = [
        row for row in detail.get("url_assets", [])
        if row.get("search_provider") == "link_library"
        or row.get("query_strategy") == "已有资产复查"
    ]
    return report, assets


def run_all_skill_search(
    *, source_db: str | Path, chips: list[str] | None = None,
    search_limit: int = 10, visit_limit: int = 10,
    search_provider: SearchProvider | None = None,
    extractor: SemanticExtractor | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Search all six categories, visit at most ten unique pages, never publish."""
    if not 1 <= search_limit <= 20 or not 1 <= visit_limit <= 10:
        raise ValueError("搜索结果上限须为 1–20，访问上限须为 1–10。")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise FileNotFoundError("正式数据库不存在。")
    selected_chips = [item.strip() for item in (chips or list(DEFAULT_CHIPS)) if item.strip()]
    if not selected_chips:
        raise ValueError("至少提供一个目标芯片。")

    previous_report, existing_assets = _previous_audit(source)
    formal_before = database_fingerprints(source)
    workspace = create_test_workspace(source)
    run_dir = Path(workspace["db_path"]).parent
    db_path = Path(workspace["db_path"])
    marker_path = run_dir / "test_mode.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker.update({"pipeline": "open-web-test", "skill": "all-skills", "schema_version": SCHEMA_VERSION})
    _atomic_json(marker_path, marker)
    _ensure_test_tables(db_path)

    provider = search_provider or FallbackSearch(proxy=proxy)
    semantic = extractor or OpenAICompatibleExtractor()
    plan = build_all_skill_query_plan(selected_chips)
    candidates_path = run_dir / "url_candidates.jsonl"
    assets_path = run_dir / "url_assets.jsonl"
    facts_path = run_dir / "extracted_facts.jsonl"
    events_path = run_dir / "events.jsonl"
    for row in existing_assets:
        _append_jsonl(assets_path, row)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": workspace["session_id"],
        "mode": "test",
        "skill": "all-skills",
        "skill_version": "1.2.0",
        "skill_label": "全部链接检查",
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "formal_database_modified": False,
        "search_provider": provider.name,
        "extractor_model": semantic.model_name,
        "chips": selected_chips,
        "target_fields": [],
        "queries": [item["query"] for item in plan],
        "counts": {"search_results": 0, "coarse_passed": 0, "visited": 0,
                   "visit_succeeded": 0, "precise_passed": 0,
                   "url_assets": len(existing_assets), "extracted": 0,
                   "validated": 0, "rejected": 0},
        "artifacts": {"queries": "search_queries.json", "candidates": "url_candidates.jsonl",
                      "url_assets": "url_assets.jsonl", "facts": "extracted_facts.jsonl",
                      "events": "events.jsonl", "audit": "full_audit_report.json"},
    }
    _atomic_json(run_dir / "search_queries.json", {
        "schema_version": SCHEMA_VERSION, "skill": "all-skills", "query_plan": plan,
    })
    _atomic_json(run_dir / "manifest.json", manifest)

    def event(stage: str, status: str, message: str, **details: Any) -> None:
        _append_jsonl(events_path, {"schema_version": SCHEMA_VERSION, "time": _now(),
                                   "stage": stage, "status": status,
                                   "message": message, "details": details})

    all_candidates: list[dict[str, Any]] = []
    search_errors: list[dict[str, str]] = []
    try:
        event("start", "running", "六类芯片信息开放互联网搜索开始。")
        for item in plan:
            try:
                results = provider.search(item["query"], search_limit)[:search_limit]
            except Exception as exc:
                search_errors.append({"query": item["query"], "error": str(exc)[:500]})
                event("search", "failed", "搜索服务暂时不可用。", query=item["query"], error=str(exc)[:500])
                continue
            for result in results:
                manifest["counts"]["search_results"] += 1
                try:
                    validate_source_url(result.url)
                    canonical = canonicalize_source_url(result.url)
                    passed, score, reasons = _coarse_selected(result, [item["chip"]], item["skill"])
                    row = {**asdict(result), **item, "url": canonical,
                           "coarse_score": score, "coarse_reasons": reasons,
                           "status": "selected" if passed else "filtered"}
                except Exception as exc:
                    row = {**asdict(result), **item, "coarse_score": 0,
                           "coarse_reasons": [str(exc)], "status": "unsafe"}
                all_candidates.append(row)
                _append_jsonl(candidates_path, {"schema_version": SCHEMA_VERSION, **row})

        selected = select_global_candidates(all_candidates, visit_limit)
        manifest["counts"]["coarse_passed"] = len(selected)
        event("coarse_filter", "success", f"全局去重后选出 {len(selected)} 个待访问网页。")
        link_ids = _insert_test_links(db_path, selected, skill_name="chip-specs") if selected else []
        if link_ids:
            summary = SourceRefresher(
                db_path=db_path, snapshot_dir=run_dir / "source_snapshots",
                proxy=proxy, max_bytes=20 * 1024 * 1024, max_attempts=2,
            ).run(link_ids, apply_state=True, force=True)
            fetched_by_url = {
                canonicalize_source_url(row["requested_url"]): row
                for row in summary.results
            }
        else:
            fetched_by_url = {}

        category_counts: Counter[str] = Counter()
        selected_by_skill: Counter[str] = Counter()
        outcome_counts: Counter[str] = Counter()
        for candidate in selected:
            for skill_name in candidate.get("matched_skills", [candidate["skill"]]):
                selected_by_skill[skill_name] += 1
            fetched = fetched_by_url.get(candidate["url"], {})
            fetch_status = str(fetched.get("outcome") or "failed")
            outcome_counts[fetch_status] += 1
            manifest["counts"]["visited"] += 1
            snapshot_path = str(fetched.get("normalized_snapshot_path") or "")
            relevant = False
            reason = str(fetched.get("error_message") or "")
            categories: list[str] = []
            accepted: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            chip_model = candidate.get("chip") or ""
            if fetch_status in {"new", "changed", "unchanged"} and snapshot_path:
                manifest["counts"]["visit_succeeded"] += 1
                text = Path(snapshot_path).read_text(encoding="utf-8")
                skill = TEST_SKILL_REGISTRY[candidate["skill"]]
                try:
                    result = semantic.extract(text=text, url=candidate["url"],
                                              chip_hints=selected_chips, skill=skill)
                    relevant = bool(result.get("relevant"))
                    reason = str(result.get("reason") or "")
                    chip_model = str(result.get("chip_model") or chip_model)
                    categories = [str(value) for value in result.get("matched_categories", [])
                                  if str(value) in {row["label"] for row in TEST_SKILL_REGISTRY.values()}]
                    accepted, rejected = _validate_facts(
                        result, source_text=text, source_url=candidate["url"],
                        skill_name=candidate["skill"],
                    )
                except Exception as exc:
                    categories = keyword_information_categories(text)
                    relevant = skill["label"] in categories
                    reason = (
                        f"模型提取未完成：{str(exc)[:260]}；"
                        f"已使用关键词备用分类（{'、'.join(categories) or '未命中'}）"
                    )
            if relevant:
                manifest["counts"]["precise_passed"] += 1
            extracted_fields = [row["field_name"] for row in accepted]
            manifest["counts"]["extracted"] += len(accepted) + len(rejected)
            manifest["counts"]["validated"] += len(accepted)
            manifest["counts"]["rejected"] += len(rejected)
            for label in categories:
                category_counts[label] += 1
            domain, source_format = _source_shape(candidate["url"])
            asset = {
                "schema_version": SCHEMA_VERSION, "url": candidate["url"],
                "final_url": fetched.get("final_url") or candidate["url"],
                "source_domain": domain, "source_format": source_format,
                "source_type": "", "discovery_type": "open-web-search", "parent_url": "",
                "discovery_query": candidate["query"], "query_strategy": candidate["strategy"],
                "search_provider": candidate.get("provider") or provider.name,
                "search_rank": candidate.get("rank"), "title": candidate.get("title") or "",
                "information_category": TEST_SKILL_REGISTRY[candidate["skill"]]["label"] if relevant else "",
                "information_categories": categories,
                "target_fields": list(TEST_SKILL_REGISTRY[candidate["skill"]]["fields"]),
                "extracted_fields": extracted_fields, "skill": candidate["skill"],
                "skill_version": TEST_SKILL_REGISTRY[candidate["skill"]]["version"],
                "chip_model": chip_model, "asset_status": "已核验" if relevant else "待复核",
                "coarse_score": candidate["coarse_score"],
                "coarse_reasons": candidate["coarse_reasons"], "fetch_status": fetch_status,
                "http_status": fetched.get("http_status"), "content_hash": fetched.get("content_hash"),
                "snapshot_path": snapshot_path, "precise_relevant": relevant,
                "decision_reason": reason, "recorded_at": _now(),
            }
            _append_jsonl(assets_path, asset)
            manifest["counts"]["url_assets"] += 1
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO test_url_classifications "
                    "(schema_version,skill_name,canonical_url,search_query,query_strategy,search_provider,"
                    "search_rank,title,snippet,target_fields_json,coarse_score,coarse_reason,fetch_status,"
                    "precise_relevant,precise_reason,matched_categories_json,extracted_fields_json,"
                    "content_hash,snapshot_path,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (SCHEMA_VERSION, candidate["skill"], candidate["url"], candidate["query"],
                     candidate["strategy"], candidate.get("provider") or provider.name,
                     candidate.get("rank"), candidate.get("title") or "", candidate.get("snippet") or "",
                     json.dumps(list(TEST_SKILL_REGISTRY[candidate["skill"]]["fields"]), ensure_ascii=False),
                     candidate["coarse_score"], "；".join(candidate["coarse_reasons"]), fetch_status,
                     1 if relevant else 0, reason, json.dumps(categories, ensure_ascii=False),
                     json.dumps(extracted_fields, ensure_ascii=False), fetched.get("content_hash"),
                     snapshot_path, _now()),
                )
                for status, rows in (("validated", accepted), ("rejected", rejected)):
                    for fact in rows:
                        db.execute(
                            "INSERT INTO test_extracted_records "
                            "(schema_version,skill_name,target_table,chip_model,field_name,proposed_value,unit,"
                            "source_url,evidence_text,evidence_location,confidence,validation_status,rejection_reason,"
                            "extractor_model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (SCHEMA_VERSION, candidate["skill"],
                             TEST_SKILL_REGISTRY[candidate["skill"]]["target_table"], chip_model,
                             fact.get("field_name") or "unknown", fact.get("proposed_value") or "",
                             fact.get("unit") or "", candidate["url"], fact.get("evidence_text") or "",
                             fact.get("evidence_location") or "", fact.get("confidence") or "",
                             status, fact.get("reason") or "", semantic.model_name, _now()),
                        )
                        _append_jsonl(facts_path, {"schema_version": SCHEMA_VERSION, "status": status,
                                                  "chip_model": chip_model, **fact})
                db.commit()

        existing = previous_report.get("existing") or {
            "database_rows": 0, "model_links_skipped": 0,
            "safe_unique_urls": len(existing_assets),
            "visited": len(existing_assets),
            "reachable": sum(1 for row in existing_assets
                             if row.get("fetch_status") in {"new", "changed", "unchanged"}),
        }
        report = {
            "session_id": workspace["session_id"], "existing": existing,
            "new": {"queries": len(plan), "search_mentions": manifest["counts"]["search_results"],
                    "search_errors": len(search_errors),
                    "selected_unique_skill_urls": sum(selected_by_skill.values()),
                    "visited_unique_urls": len(selected),
                    "reachable": manifest["counts"]["visit_succeeded"],
                    "selected_by_skill": dict(selected_by_skill)},
            "outcomes": dict(outcome_counts),
            "matched_information_categories": dict(category_counts),
            "search_errors": search_errors,
            "unsafe_existing": previous_report.get("unsafe_existing") or [],
            "test_database_quick_check": "ok",
        }
        _atomic_json(run_dir / "full_audit_report.json", report)
        manifest["status"] = "success" if manifest["counts"]["visit_succeeded"] else "failed"
        event("finish", manifest["status"], "六类信息搜索和十个候选网页访问已完成。",
              counts=manifest["counts"])
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)[:1000]
        event("finish", "failed", "全类别搜索执行失败。", error=str(exc)[:500])
    finally:
        manifest["finished_at"] = _now()
        formal_after = database_fingerprints(source)
        manifest["formal_database_before"] = formal_before
        manifest["formal_database_after"] = formal_after
        manifest["formal_database_modified"] = formal_before["db"] != formal_after["db"]
        _atomic_json(run_dir / "manifest.json", manifest)
    return {**workspace, **manifest}
