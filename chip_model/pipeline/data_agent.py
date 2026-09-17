"""Hermes-oriented data crawler orchestration and shared Agent/Skill queues.

The deterministic half of the system lives here: due-source planning, fetch
reuse, parse ledgers, cross-skill routing and observable worker leases.  LLM
workers only receive bounded jobs and must return candidate facts; they never
write business tables directly.
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from chip_model.database import get_db, get_db_path, get_project_root
from chip_model.pipeline.source_refresh import (
    SourceRefresher,
    SourceRefreshError,
    iso_utc,
    validate_source_url,
)
from chip_model.pipeline.model_sync import sync_known_models
from chip_model.pipeline.candidate_validation import (
    CandidateValidationError, normalize_fact_entity_key, validate_fact_value,
)


PARSER_VERSION = "data-agent-v1"
MAX_CRAWL_DEPTH = 2
CHIP_TEST_SKILLS = frozenset({"chip-catalog", "chip-basic", "chip-compute"})
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source", "spm",
}

SKILL_POLICIES: dict[str, dict[str, Any]] = {
    "url-discovery": {"label": "目标链接发现", "days": 0, "target": "control"},
    "chip-catalog": {"label": "芯片清单", "days": 14, "target": "chips"},
    "chip-basic": {"label": "芯片基础规格", "days": 30, "target": "chips"},
    "chip-compute": {"label": "芯片算力参数", "days": 30, "target": "chips"},
    "chip-interconnect": {"label": "芯片互联", "days": 30, "target": "chips"},
    "chip-ecosystem": {"label": "芯片生态", "days": 3, "target": "chips"},
    "chip-price": {"label": "芯片价格", "days": 3, "target": "chips"},
    "model-catalog": {"label": "模型目录", "days": 7, "target": "models"},
    "model-activity": {"label": "模型活跃度", "days": 7, "target": "models"},
    "benchmark-ingest": {"label": "芯片模型实测", "days": 14, "target": "chip_model_benchmarks"},
    "deployment-ingest": {"label": "适配与部署", "days": 7, "target": "chip_model_compatibility"},
    "data-quality-review": {"label": "数据质量", "days": 7, "target": "control"},
}


def canonicalize_source_url(value: str) -> str:
    """Return a stable public HTTPS URL key for discovery deduplication."""
    raw = str(value or "").strip()
    validate_source_url(raw)
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise ValueError("发现的目标 URL 必须使用 HTTPS。")
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunparse(("https", netloc, path, "", query, ""))


def classify_source_role(url: str, description: str = "", category: str = "") -> str:
    """Conservatively distinguish list/seed pages from extractable targets."""
    parsed = urlparse(str(url or ""))
    text = f"{description} {category} {parsed.path}".casefold()
    if parsed.path.casefold().endswith(".pdf") or "datasheet" in text or "白皮书" in text:
        return "document"
    if any(token in text for token in ("api/", "/api", "接口", "api ")):
        return "api"
    if any(token in text for token in ("列表", "目录", "catalog", "products", "product list", "型号汇总")):
        return "listing"
    if any(token in text for token in ("规格", "产品页", "详情", "spec", "benchmark", "部署", "兼容")):
        return "detail"
    return "seed"

FIELD_OWNERS: dict[str, tuple[str, str]] = {
    "vendor": ("chip-catalog", "chips"),
    "chip_model": ("chip-catalog", "chips"),
    "production_status": ("chip-catalog", "chips"),
    "release_date": ("chip-catalog", "chips"),
    "vram_gb": ("chip-basic", "chips"),
    "vram_type": ("chip-basic", "chips"),
    "vram_bw_gb_s": ("chip-basic", "chips"),
    "tdp_w": ("chip-basic", "chips"),
    "form_factor": ("chip-basic", "chips"),
    "bus_interface": ("chip-basic", "chips"),
    "precision_support": ("chip-compute", "chips"),
    "precision_perf": ("chip-compute", "chips"),
    "architecture": ("chip-compute", "chips"),
    "process_node_nm": ("chip-compute", "chips"),
    "compute_units": ("chip-compute", "chips"),
    "interconnect_bw_gb_s": ("chip-interconnect", "chips"),
    "interconnect_tech": ("chip-interconnect", "chips"),
    "network_interface": ("chip-interconnect", "chips"),
    "software_stack": ("chip-ecosystem", "chips"),
    "compatible_frameworks": ("chip-ecosystem", "chips"),
    "maturity_level": ("chip-ecosystem", "chips"),
    "price_usd": ("chip-price", "chips"),
    "price_cny_wan": ("chip-price", "chips"),
    "downloads": ("model-activity", "models"),
    "likes": ("model-activity", "models"),
    "last_modified": ("model-activity", "models"),
    "total_params_b": ("model-catalog", "models"),
    "architecture_family": ("model-catalog", "models"),
    "benchmark": ("benchmark-ingest", "chip_model_benchmarks"),
    "compatibility": ("deployment-ingest", "chip_model_compatibility"),
    "deployment": ("deployment-ingest", "deployment_guides"),
}

# The queue boundary is stricter than the database boundary. A Skill may only
# submit fields from its own domain and target table; the Publisher performs a
# second table-schema check before anything reaches business data.
SKILL_FIELD_TARGETS: dict[str, dict[str, set[str]]] = {
    "url-discovery": {},
    "chip-catalog": {"chips": {
        "vendor", "vendor_display", "vendor_region", "chip_series", "chip_model",
        "chip_type", "usage", "tier", "release_date", "production_status",
        "eol_date", "target_market", "is_released", "expected_release_date",
    }},
    "chip-basic": {"chips": {
        "vram_gb", "vram_type", "vram_bw_gb_s", "tdp_w", "form_factor", "bus_interface",
    }},
    "chip-compute": {"chips": {
        "precision_support", "precision_perf", "architecture", "process_node_nm", "compute_units",
    }},
    "chip-interconnect": {"chips": {
        "interconnect_bw_gb_s", "interconnect_tech", "network_interface",
    }},
    "chip-ecosystem": {"chips": {
        "software_stack", "compatible_frameworks", "framework_compat", "sw_stack",
        "cloud_available", "ecosystem_notes", "maturity_level",
    }},
    "chip-price": {"chips": {
        "price_usd", "price_cny_wan", "price_period", "price_notes",
    }},
    "model-catalog": {"models": {
        "author", "pipeline_tag", "library_name", "tags", "architecture_family",
        "total_params_b", "config_json", "card_data_json",
    }},
    "model-activity": {"models": {"downloads", "likes", "last_modified"}},
    "benchmark-ingest": {"chip_model_benchmarks": {
        "scenario", "task", "hardware_config", "framework", "precision", "batch_size",
        "input_seq_length", "output_seq_length", "concurrency", "prefill_throughput",
        "decode_throughput", "time_to_first_token_ms", "inter_token_latency_ms",
        "memory_peak_mb", "throughput_tok_s", "throughput_samples_s", "tpot_ms",
        "mfu_pct", "gpu_hours", "training_tokens_T", "training_gpu_count",
        "training_workload_type", "test_date", "notes", "citation", "evidence_level",
        "methodology", "reference_url",
    }},
    "deployment-ingest": {
        "chip_model_compatibility": {"compat_status", "framework", "precision", "verified_at", "notes"},
        "deployment_guides": {"backend", "notes"},
    },
    "data-quality-review": {},
}


def _category_skills(category: str, description: str = "") -> list[str]:
    text = f"{category} {description}".casefold()
    skills: list[str] = []
    if any(token in text for token in ("模型", "model", "huggingface", "modelscope")):
        skills.extend(("model-catalog", "model-activity"))
    if any(token in text for token in ("价格", "price", "租赁", "出货")):
        skills.append("chip-price")
    if any(token in text for token in ("测试", "benchmark", "mlperf", "性能实测")):
        skills.append("benchmark-ingest")
    if any(token in text for token in ("兼容", "适配", "部署", "生态")):
        skills.extend(("chip-ecosystem", "deployment-ingest"))
    if any(token in text for token in ("芯片", "chip", "gpu", "npu", "硬件", "规格")):
        skills.extend(
            ("chip-catalog", "chip-basic", "chip-compute", "chip-interconnect")
        )
    return list(dict.fromkeys(skills or ("chip-catalog",)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalize_entity_name(value: str) -> str:
    """Shared conservative identity key used by every domain Skill."""
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def upsert_entity_alias(
    entity_type: str,
    entity_id: str | int,
    canonical_name: str,
    alias: str,
    *,
    source_url: str = "",
    confidence: str = "medium",
    db_path: str | Path | None = None,
) -> int:
    normalized = normalize_entity_name(alias)
    if not entity_type or not str(entity_id) or not canonical_name or not normalized:
        raise ValueError("实体类型、ID、规范名称和别名不能为空。")
    now = iso_utc(datetime.now(timezone.utc))
    with get_db(db_path) as db:
        existing = db.execute(
            "SELECT id,entity_id FROM entity_aliases WHERE entity_type=? AND normalized=?",
            (entity_type, normalized),
        ).fetchone()
        if existing and str(existing["entity_id"]) != str(entity_id):
            raise ValueError("该别名已经指向另一个实体，必须先解决冲突。")
        if existing:
            db.execute(
                "UPDATE entity_aliases SET canonical_name=?,alias=?,source_url=?,confidence=?,updated_at=? WHERE id=?",
                (canonical_name, alias, source_url, confidence, now, existing["id"]),
            )
            alias_id = int(existing["id"])
        else:
            cursor = db.execute(
                "INSERT INTO entity_aliases "
                "(entity_type,entity_id,canonical_name,alias,normalized,source_url,confidence,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (entity_type, str(entity_id), canonical_name, alias, normalized, source_url, confidence, now, now),
            )
            alias_id = int(cursor.lastrowid)
        db.commit()
    return alias_id


def resolve_entity_alias(
    entity_type: str, name: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    with get_db(db_path, readonly=True) as db:
        row = db.execute(
            "SELECT * FROM entity_aliases WHERE entity_type=? AND normalized=?",
            (entity_type, normalize_entity_name(name)),
        ).fetchone()
    return dict(row) if row else None


@dataclass
class CycleSummary:
    run_id: int
    status: str
    planned_jobs: int
    completed_jobs: int
    awaiting_agent_jobs: int
    failed_jobs: int
    candidate_count: int
    inbox_count: int
    source_run_id: int | None
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataAgentOrchestrator:
    """Plan and execute one bounded data-maintenance cycle."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        snapshot_dir: str | Path | None = None,
        *,
        now_fn=lambda: datetime.now(timezone.utc),
        source_refresher_factory=SourceRefresher,
        model_sync_fn=sync_known_models,
    ) -> None:
        self.db_path = Path(db_path or get_db_path())
        default_snapshots = (
            self.db_path.parent / "source_snapshots"
            if "test_runs" in self.db_path.parts
            else get_project_root() / "data" / "source_snapshots"
        )
        self.snapshot_dir = Path(snapshot_dir or default_snapshots)
        self.now_fn = now_fn
        self.source_refresher_factory = source_refresher_factory
        self.model_sync_fn = model_sync_fn

    def ensure_schema(self) -> None:
        schema = (get_project_root() / "schema.sql").read_text(encoding="utf-8")
        with get_db(self.db_path) as db:
            db.executescript(schema)
            db.execute("DROP INDEX IF EXISTS idx_extraction_candidates_dedupe")
            db.commit()

    def _event(
        self,
        run_id: int,
        actor: str,
        stage: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        link_id: int | None = None,
    ) -> None:
        with get_db(self.db_path) as db:
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,link_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    link_id,
                    actor,
                    stage,
                    status,
                    message,
                    _json(details or {}),
                    iso_utc(self.now_fn()),
                ),
            )
            db.commit()

    def start_cycle(self, *, allow_overlap: bool = False, mode: str = "automatic") -> int:
        if mode not in {"automatic", "test"}:
            raise ValueError("数据智能体模式仅支持 automatic 或 test。")
        self.ensure_schema()
        now = iso_utc(self.now_fn())
        with get_db(self.db_path) as db:
            if not allow_overlap:
                active = db.execute(
                    "SELECT id,status FROM update_runs WHERE run_type='data_agent_cycle' "
                    "AND status IN ('running','awaiting_agent','awaiting_publish') "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if active:
                    raise RuntimeError(
                        f"数据智能体周期 {active['id']} 仍处于 {active['status']}，拒绝重叠启动。"
                    )
            cursor = db.execute(
                "INSERT INTO update_runs "
                "(run_type,mode,status,started_at,created_at,counts_json) "
                "VALUES ('data_agent_cycle',?,'running',?,?, '{}')",
                (mode, now, now),
            )
            db.commit()
            run_id = int(cursor.lastrowid)
        self._event(
            run_id,
            "orchestrator",
            "cycle_started",
            "started",
            "数据抓取智能体周期开始。",
            {"parser_version": PARSER_VERSION},
        )
        return run_id

    def _ensure_source_registry(self, db: sqlite3.Connection) -> None:
        """Backfill roles for legacy links without changing link_library."""
        now = iso_utc(self.now_fn())
        rows = db.execute(
            "SELECT l.id,l.url,l.description,l.category FROM link_library l "
            "LEFT JOIN source_registry r ON r.link_id=l.id WHERE r.link_id IS NULL"
        ).fetchall()
        for row in rows:
            raw_url = str(row["url"] or "").strip()
            if not raw_url:
                continue
            try:
                canonical = canonicalize_source_url(raw_url)
            except (ValueError, SourceRefreshError):
                parsed = urlparse(raw_url)
                canonical = urlunparse(
                    (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path or "/", "", parsed.query, "")
                )
            db.execute(
                "INSERT OR IGNORE INTO source_registry "
                "(link_id,canonical_url,url_role,crawl_depth,discovered_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    int(row["id"]),
                    canonical,
                    classify_source_role(raw_url, str(row["description"] or ""), str(row["category"] or "")),
                    0,
                    now,
                    now,
                ),
            )

    def plan(
        self, run_id: int, *, limit: int = 50, force: bool = False,
        selected_link_ids: list[int] | None = None,
        chip_only: bool = False,
    ) -> int:
        now_dt = self.now_fn().astimezone(timezone.utc)
        now = iso_utc(now_dt)
        with get_db(self.db_path) as db:
            self._ensure_source_registry(db)
            selection = [int(value) for value in (selected_link_ids or [])]
            if any(value <= 0 for value in selection):
                raise ValueError("选择的种子 link_id 必须是正整数。")
            where = "WHERE COALESCE(l.url,'')!='' AND COALESCE(l.accessible,'')!='disabled' "
            if selection:
                where += "AND l.id IN (" + ",".join("?" for _ in selection) + ") "
            rows = db.execute(
                "SELECT l.id,l.url,l.description,l.vendor,l.category,s.next_check_at,"
                "r.url_role,r.crawl_depth,r.parent_link_id "
                "FROM link_library l LEFT JOIN source_monitor_state s ON s.link_id=l.id "
                "LEFT JOIN source_registry r ON r.link_id=l.id "
                + where +
                "ORDER BY CASE WHEN s.next_check_at IS NULL THEN 0 ELSE 1 END, "
                "COALESCE(s.next_check_at,''),l.id",
                selection,
            ).fetchall()
            count = 0
            for row in rows:
                if count >= max(1, limit):
                    break
                item = dict(row)
                if chip_only:
                    from chip_model.pipeline.link_families import is_chip_source
                    if not is_chip_source(str(item.get("url") or ""), str(item.get("category") or "")):
                        continue
                skills = _category_skills(
                    str(item.get("category") or ""),
                    str(item.get("description") or ""),
                )
                if chip_only:
                    skills = [skill for skill in skills if skill in CHIP_TEST_SKILLS]
                due_skills: list[str] = []
                for skill in skills:
                    last = db.execute(
                        "SELECT MAX(COALESCE(finished_at,started_at)) AS last_at FROM parse_ledger "
                        "WHERE link_id=? AND skill_name=? AND status IN ('new','unchanged','success')",
                        (item["id"], skill),
                    ).fetchone()["last_at"]
                    is_due = force or not last
                    if last and not force:
                        try:
                            parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                            if parsed.tzinfo is None:
                                parsed = parsed.replace(tzinfo=timezone.utc)
                            is_due = parsed + timedelta(days=int(SKILL_POLICIES[skill]["days"])) <= now_dt
                        except ValueError:
                            is_due = True
                    if is_due:
                        due_skills.append(skill)
                if not due_skills:
                    continue
                dedupe_key = f"cycle:{run_id}:source:{item['id']}"
                db.execute(
                    "INSERT OR IGNORE INTO data_agent_jobs "
                    "(cycle_run_id,dedupe_key,job_type,skill_name,link_id,priority,status,"
                    "scheduled_for,input_json,created_at,updated_at) "
                    "VALUES (?,?, 'source_refresh','source-refresh',?,100,'queued',?,?,?,?)",
                    (
                        run_id,
                        dedupe_key,
                        item["id"],
                        now,
                        _json({**item, "target_skills": due_skills}),
                        now,
                        now,
                    ),
                )
                count += int(db.execute("SELECT changes()").fetchone()[0])

            # Semantic discovery is a bounded Agent job. It runs after known
            # sources and creates candidates/inbox records instead of writing.
            for skill, priority in (() if chip_only else (
                ("chip-catalog", 40),
                ("model-catalog", 35),
                ("data-quality-review", 20),
            )):
                previous = db.execute(
                    "SELECT MAX(COALESCE(finished_at,created_at)) AS last_at FROM data_agent_jobs "
                    "WHERE job_type='agent_discovery' AND skill_name=? AND cycle_run_id!=?",
                    (skill, run_id),
                ).fetchone()["last_at"]
                discovery_due = force or not previous
                if previous and not force:
                    try:
                        parsed = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        discovery_due = parsed + timedelta(days=int(SKILL_POLICIES[skill]["days"])) <= now_dt
                    except ValueError:
                        discovery_due = True
                if not discovery_due:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO data_agent_jobs "
                    "(cycle_run_id,dedupe_key,job_type,skill_name,priority,status,"
                    "scheduled_for,input_json,created_at,updated_at) "
                    "VALUES (?,?, 'agent_discovery',?,?, 'awaiting_agent',?,?,?,?)",
                    (
                        run_id,
                        f"cycle:{run_id}:discovery:{skill}",
                        skill,
                        priority,
                        now,
                        _json({"scope": "incremental", "max_entities": 20}),
                        now,
                        now,
                    ),
                )
                count += int(db.execute("SELECT changes()").fetchone()[0])
            previous_model_sync = db.execute(
                "SELECT MAX(COALESCE(finished_at,created_at)) AS last_at FROM data_agent_jobs "
                "WHERE job_type='model_api_sync' AND cycle_run_id!=?",
                (run_id,),
            ).fetchone()["last_at"]
            model_sync_due = force or not previous_model_sync
            if previous_model_sync and not force:
                try:
                    parsed = datetime.fromisoformat(str(previous_model_sync).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    model_sync_due = parsed + timedelta(days=1) <= now_dt
                except ValueError:
                    model_sync_due = True
            if model_sync_due and not chip_only:
                db.execute(
                    "INSERT OR IGNORE INTO data_agent_jobs "
                    "(cycle_run_id,dedupe_key,job_type,skill_name,priority,status,scheduled_for,"
                    "input_json,created_at,updated_at) VALUES "
                    "(?,?,'model_api_sync','model-catalog',90,'queued',?,?,?,?)",
                    (
                        run_id,
                        f"cycle:{run_id}:model-api-sync",
                        now,
                        _json({"provider": "huggingface", "limit": 20}),
                        now,
                        now,
                    ),
                )
                count += int(db.execute("SELECT changes()").fetchone()[0])
            db.commit()
        self._event(
            run_id,
            "scheduler",
            "job_planning",
            "success",
            f"已生成 {count} 个去重任务。",
            {"source_limit": limit, "force": force, "planned_jobs": count},
        )
        return count

    def _route_source_result(
        self,
        cycle_run_id: int,
        source_run_id: int,
        job: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[int, int]:
        input_data = _decode(job.get("input_json"), {})
        target_skills = input_data.get("target_skills") or ["chip-catalog"]
        url_role = str(input_data.get("url_role") or "detail")
        crawl_depth = int(input_data.get("crawl_depth") or 0)
        discovery_page = url_role in {"seed", "listing"} and crawl_depth < MAX_CRAWL_DEPTH
        routed_skills = ["url-discovery"] if discovery_page else list(target_skills)
        routed_job_type = "agent_url_discovery" if discovery_page else "agent_extract"
        now = iso_utc(self.now_fn())
        candidate_count = 0
        inbox_count = 0
        with get_db(self.db_path) as db:
            check = db.execute(
                "SELECT c.id,d.id AS diff_id FROM source_checks c "
                "LEFT JOIN source_diffs d ON d.source_check_id=c.id "
                "WHERE c.run_id=? AND c.link_id=?",
                (source_run_id, result["link_id"]),
            ).fetchone()
            check_id = int(check["id"]) if check else None
            diff_id = int(check["diff_id"]) if check and check["diff_id"] else None
            outcome = str(result.get("outcome") or "failed")
            job_status = "succeeded" if outcome in {"new", "changed", "unchanged", "not_due"} else "failed"
            db.execute(
                "UPDATE data_agent_jobs SET status=?,finished_at=?,attempt_count=attempt_count+1,"
                "output_json=?,error_summary=?,updated_at=? WHERE id=?",
                (
                    job_status,
                    now,
                    _json(result),
                    result.get("error_message"),
                    now,
                    job["id"],
                ),
            )
            if check_id:
                for skill in routed_skills:
                    db.execute(
                        "INSERT OR IGNORE INTO parse_ledger "
                        "(source_check_id,run_id,link_id,skill_name,content_hash,parser_version,"
                        "status,extracted_count,started_at,finished_at,details_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            check_id,
                            cycle_run_id,
                            result["link_id"],
                            skill,
                            result.get("content_hash"),
                            PARSER_VERSION,
                            "awaiting_agent" if outcome in {"new", "changed"} else outcome,
                            0,
                            now,
                            now if outcome not in {"new", "changed"} else None,
                            _json({"source_run_id": source_run_id, "outcome": outcome}),
                        ),
                    )
            if outcome in {"new", "changed"}:
                for skill in routed_skills:
                    db.execute(
                        "INSERT OR IGNORE INTO data_agent_jobs "
                        "(cycle_run_id,dedupe_key,job_type,skill_name,link_id,priority,status,"
                        "scheduled_for,input_json,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,80,'awaiting_agent',?,?,?,?)",
                        (
                            cycle_run_id,
                            f"cycle:{cycle_run_id}:{routed_job_type}:{result['link_id']}:{skill}:{result.get('content_hash')}",
                            routed_job_type,
                            skill,
                            result["link_id"],
                            now,
                            _json(
                                {
                                    "source_run_id": source_run_id,
                                    "source_check_id": check_id,
                                    "source_diff_id": diff_id,
                                    "url": result.get("final_url") or result.get("requested_url"),
                                    "snapshot_path": result.get("normalized_snapshot_path"),
                                    "diff_path": result.get("diff_path"),
                                    "evidence_scope": "diff" if outcome == "changed" else "snapshot",
                                    "url_role": url_role,
                                    "crawl_depth": crawl_depth,
                                    "target_skills": target_skills,
                                    "outbound_links": result.get("outbound_links") or [],
                                    "candidate_fields": result.get("candidate_fields") or [],
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                for field in ([] if discovery_page else (result.get("candidate_fields") or [])):
                    field_name = str(field.get("field") or "")
                    owner, target_table = FIELD_OWNERS.get(
                        field_name, (target_skills[0], SKILL_POLICIES.get(target_skills[0], {}).get("target", "chips"))
                    )
                    cursor = db.execute(
                        "INSERT OR IGNORE INTO extraction_candidates "
                        "(cycle_run_id,source_check_id,source_diff_id,link_id,owner_skill,"
                        "target_table,field_name,evidence_text,evidence_location,source_url,"
                        "status,extractor_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cycle_run_id,
                            check_id,
                            diff_id,
                            result["link_id"],
                            owner,
                            target_table,
                            field_name,
                            field.get("evidence"),
                            field.get("change_type"),
                            result.get("final_url") or result.get("requested_url"),
                            "awaiting_agent",
                            PARSER_VERSION,
                            now,
                        ),
                    )
                    candidate_count += int(cursor.rowcount > 0)
                    if owner not in target_skills:
                        db.execute(
                            "INSERT INTO skill_inbox "
                            "(cycle_run_id,from_skill,to_skill,link_id,source_check_id,"
                            "content_hash,topic,payload_json,status,created_at) "
                            "VALUES (?,?,?,?,?,?,?,?, 'pending',?)",
                            (
                                cycle_run_id,
                                target_skills[0],
                                owner,
                                result["link_id"],
                                check_id,
                                result.get("content_hash"),
                                field_name,
                                _json(field),
                                now,
                            ),
                        )
                        inbox_count += 1
            db.commit()
        return candidate_count, inbox_count

    def run_cycle(
        self,
        *,
        limit: int = 50,
        force: bool = False,
        proxy: str | None = None,
        max_attempts: int = 2,
        mode: str = "automatic",
        selected_link_ids: list[int] | None = None,
    ) -> CycleSummary:
        started = self.now_fn()
        run_id = self.start_cycle(allow_overlap=force, mode=mode)
        self._event(run_id, 'orchestrator', 'run_parameters', 'recorded', '本轮运行参数快照。',
                    {'limit': limit, 'force': force, 'mode': mode, 'max_attempts': max_attempts,
                     'proxy_enabled': bool(proxy), 'max_crawl_depth': MAX_CRAWL_DEPTH,
                     'selected_link_ids': selected_link_ids, 'parser_version': PARSER_VERSION})
        planned = self.plan(run_id, limit=limit, force=force,
                            selected_link_ids=selected_link_ids,
                            chip_only=mode == "test")
        source_run_id: int | None = None
        candidate_count = 0
        inbox_count = 0
        fatal_error = ""
        try:
            with get_db(self.db_path) as db:
                jobs = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM data_agent_jobs WHERE cycle_run_id=? "
                        "AND job_type='source_refresh' AND status='queued' ORDER BY priority DESC,id",
                        (run_id,),
                    ).fetchall()
                ]
                now = iso_utc(self.now_fn())
                db.executemany(
                    "UPDATE data_agent_jobs SET status='running',started_at=?,worker_id='deterministic-fetcher',updated_at=? WHERE id=?",
                    [(now, now, job["id"]) for job in jobs],
                )
                db.commit()
            if jobs:
                self._event(
                    run_id,
                    "orchestrator",
                    "shared_fetch",
                    "started",
                    f"开始统一抓取 {len(jobs)} 个来源；同一 URL 只访问一次。",
                )
                refresher = self.source_refresher_factory(
                    db_path=self.db_path,
                    snapshot_dir=self.snapshot_dir,
                    proxy=proxy,
                    max_attempts=max_attempts,
                    now_fn=self.now_fn,
                )
                source_summary = refresher.run(
                    [int(job["link_id"]) for job in jobs],
                    apply_state=True,
                    # The orchestrator already applied each Skill's own schedule.
                    # Force only bypasses the older per-link generic schedule.
                    force=True,
                )
                source_run_id = source_summary.run_id
                if mode == "test" and source_run_id is not None:
                    with get_db(self.db_path) as db:
                        db.execute("UPDATE update_runs SET mode='test' WHERE id=?", (source_run_id,))
                        db.commit()
                by_link = {int(item["link_id"]): item for item in source_summary.results}
                for job in jobs:
                    result = by_link.get(int(job["link_id"]))
                    if not result:
                        continue
                    added_candidates, added_inbox = self._route_source_result(
                        run_id, int(source_run_id), job, result
                    )
                    candidate_count += added_candidates
                    inbox_count += added_inbox
                self._event(
                    run_id,
                    "orchestrator",
                    "shared_fetch",
                    source_summary.status,
                    "统一抓取、快照复用和按 Skill 路由完成。",
                    {
                        "source_run_id": source_run_id,
                        "counts": source_summary.counts,
                        "candidate_count": candidate_count,
                        "inbox_count": inbox_count,
                    },
                )
            with get_db(self.db_path) as db:
                model_jobs = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM data_agent_jobs WHERE cycle_run_id=? "
                        "AND job_type='model_api_sync' AND status='queued' ORDER BY id",
                        (run_id,),
                    ).fetchall()
                ]
                now = iso_utc(self.now_fn())
                db.executemany(
                    "UPDATE data_agent_jobs SET status='running',started_at=?,"
                    "worker_id='huggingface-sync',updated_at=? WHERE id=?",
                    [(now, now, job["id"]) for job in model_jobs],
                )
                db.commit()
            for job in model_jobs:
                model_input = _decode(job.get("input_json"), {})
                self._event(
                    run_id,
                    "model-sync",
                    "huggingface_api",
                    "started",
                    "开始通过 HuggingFace API 增量更新已知模型。",
                    model_input,
                )
                model_result = self.model_sync_fn(
                    db_path=self.db_path,
                    limit=int(model_input.get("limit") or 20),
                    proxy=proxy,
                )
                model_counts = model_result.get("counts", {})
                successful_models = int(model_counts.get("updated") or 0) + int(
                    model_counts.get("unchanged") or 0
                )
                model_status = (
                    "failed"
                    if int(model_counts.get("selected") or 0) > 0
                    and int(model_counts.get("failed") or 0) > 0
                    and successful_models == 0
                    else "succeeded"
                )
                now = iso_utc(self.now_fn())
                with get_db(self.db_path) as db:
                    db.execute(
                        "UPDATE data_agent_jobs SET status=?,finished_at=?,attempt_count=attempt_count+1,"
                        "output_json=?,error_summary=?,updated_at=? WHERE id=?",
                        (
                            model_status,
                            now,
                            _json(model_result),
                            "模型 API 不可达，剩余任务已延期" if model_status == "failed" else "",
                            now,
                            job["id"],
                        ),
                    )
                    db.commit()
                self._event(
                    run_id,
                    "model-sync",
                    "huggingface_api",
                    "failed" if model_status == "failed" else "success",
                    "HuggingFace 模型增量同步完成。",
                    model_counts,
                )
        except Exception as exc:
            fatal_error = str(exc)
            failed_at = iso_utc(self.now_fn())
            with get_db(self.db_path) as db:
                db.execute(
                    "UPDATE data_agent_jobs SET status='failed',finished_at=?,error_summary=?,updated_at=? "
                    "WHERE cycle_run_id=? AND status IN ('queued','running')",
                    (failed_at, fatal_error[:2000], failed_at, run_id),
                )
                db.commit()
            self._event(
                run_id,
                "orchestrator",
                "cycle_error",
                "failed",
                "确定性抓取阶段失败。",
                {"error_message": fatal_error},
            )

        finished = self.now_fn()
        with get_db(self.db_path) as db:
            counts = {
                str(row["status"]): int(row["count"])
                for row in db.execute(
                    "SELECT status,COUNT(*) AS count FROM data_agent_jobs "
                    "WHERE cycle_run_id=? GROUP BY status",
                    (run_id,),
                ).fetchall()
            }
            awaiting = counts.get("awaiting_agent", 0)
            failed = counts.get("failed", 0)
            completed = counts.get("succeeded", 0)
            status = "failed" if fatal_error else ("awaiting_agent" if awaiting else ("partial" if failed else "success"))
            payload = {
                "planned": planned,
                "completed": completed,
                "awaiting_agent": awaiting,
                "failed": failed,
                "candidates": candidate_count,
                "inbox": inbox_count,
                "source_run_id": source_run_id,
            }
            db.execute(
                "UPDATE update_runs SET status=?,finished_at=?,counts_json=?,error_summary=? WHERE id=?",
                (status, iso_utc(finished), _json(payload), fatal_error, run_id),
            )
            db.commit()
        self._event(
            run_id,
            "orchestrator",
            "cycle_finished",
            status,
            "确定性阶段完成，等待 Hermes 并行处理语义任务。" if awaiting else "数据更新周期完成。",
            payload,
        )
        return CycleSummary(
            run_id=run_id,
            status=status,
            planned_jobs=planned,
            completed_jobs=completed,
            awaiting_agent_jobs=awaiting,
            failed_jobs=failed,
            candidate_count=candidate_count,
            inbox_count=inbox_count,
            source_run_id=source_run_id,
            started_at=iso_utc(started),
            finished_at=iso_utc(finished),
        )

    def advance_target_phase(
        self,
        cycle_run_id: int,
        *,
        job_ids: Iterable[int] | None = None,
        proxy: str | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Fetch URLs discovered by Hermes and route them within the same cycle."""
        self.ensure_schema()
        selected_ids = [int(item) for item in (job_ids or [])]
        with get_db(self.db_path) as db:
            params: list[Any] = [cycle_run_id]
            sql = (
                "SELECT * FROM data_agent_jobs WHERE cycle_run_id=? "
                "AND job_type='source_target_refresh' AND status='queued'"
            )
            if selected_ids:
                marks = ",".join("?" for _ in selected_ids)
                sql += f" AND id IN ({marks})"
                params.extend(selected_ids)
            sql += " ORDER BY priority DESC,id"
            jobs = [dict(row) for row in db.execute(sql, params).fetchall()]
            now = iso_utc(self.now_fn())
            db.executemany(
                "UPDATE data_agent_jobs SET status='running',started_at=?,"
                "worker_id='deterministic-target-fetcher',updated_at=? WHERE id=?",
                [(now, now, job["id"]) for job in jobs],
            )
            db.commit()
        if not jobs:
            return {"cycle_run_id": cycle_run_id, "selected": 0, "status": "no_targets"}

        self._event(
            cycle_run_id,
            "orchestrator",
            "target_fetch",
            "started",
            f"第二轮开始访问 {len(jobs)} 个去重目标 URL。",
            {"job_ids": [job["id"] for job in jobs]},
        )
        candidate_count = 0
        inbox_count = 0
        try:
            refresher = self.source_refresher_factory(
                db_path=self.db_path,
                snapshot_dir=self.snapshot_dir,
                proxy=proxy,
                max_attempts=max_attempts,
                now_fn=self.now_fn,
            )
            source_summary = refresher.run(
                [int(job["link_id"]) for job in jobs], apply_state=True, force=True
            )
            by_link = {int(item["link_id"]): item for item in source_summary.results}
            for job in jobs:
                result = by_link.get(int(job["link_id"]))
                if not result:
                    continue
                added_candidates, added_inbox = self._route_source_result(
                    cycle_run_id, int(source_summary.run_id), job, result
                )
                candidate_count += added_candidates
                inbox_count += added_inbox
                with get_db(self.db_path) as db:
                    db.execute(
                        "UPDATE source_discovery_edges SET status=?,fetched_at=? "
                        "WHERE cycle_run_id=? AND child_link_id=?",
                        (
                            str(result.get("outcome") or "failed"),
                            iso_utc(self.now_fn()),
                            cycle_run_id,
                            int(job["link_id"]),
                        ),
                    )
                    db.commit()
            status = source_summary.status
            source_run_id = source_summary.run_id
        except Exception as exc:
            now = iso_utc(self.now_fn())
            with get_db(self.db_path) as db:
                db.executemany(
                    "UPDATE data_agent_jobs SET status='failed',finished_at=?,error_summary=?,updated_at=? "
                    "WHERE id=? AND status='running'",
                    [(now, str(exc)[:2000], now, job["id"]) for job in jobs],
                )
                db.commit()
            self._event(
                cycle_run_id, "orchestrator", "target_fetch", "failed",
                "第二轮目标 URL 抓取失败。", {"error_message": str(exc)},
            )
            raise

        with get_db(self.db_path) as db:
            counts = {
                str(row["status"]): int(row["count"])
                for row in db.execute(
                    "SELECT status,COUNT(*) AS count FROM data_agent_jobs "
                    "WHERE cycle_run_id=? GROUP BY status",
                    (cycle_run_id,),
                ).fetchall()
            }
            awaiting = counts.get("awaiting_agent", 0)
            cycle_status = "awaiting_agent" if awaiting else ("partial" if counts.get("failed", 0) else "success")
            db.execute(
                "UPDATE update_runs SET status=?,counts_json=? WHERE id=?",
                (cycle_status, _json(counts), cycle_run_id),
            )
            db.commit()
        payload = {
            "cycle_run_id": cycle_run_id,
            "selected": len(jobs),
            "source_run_id": source_run_id,
            "status": status,
            "awaiting_agent_jobs": awaiting,
            "candidate_count": candidate_count,
            "inbox_count": inbox_count,
        }
        self._event(
            cycle_run_id,
            "orchestrator",
            "target_fetch",
            status,
            "第二轮目标 URL 抓取完成，已生成后续提取任务。",
            payload,
        )
        return payload


def claim_agent_jobs(
    worker_id: str,
    skills: Iterable[str],
    *,
    db_path: str | Path | None = None,
    limit: int = 5,
    lease_minutes: int = 30,
) -> list[dict[str, Any]]:
    """Atomically lease bounded semantic jobs to a Hermes worker."""
    clean_skills = list(dict.fromkeys(str(skill).strip() for skill in skills if str(skill).strip()))
    if not clean_skills:
        return []
    now = datetime.now(timezone.utc)
    now_text = iso_utc(now)
    lease_until = iso_utc(now + timedelta(minutes=max(1, lease_minutes)))
    placeholders = ",".join("?" for _ in clean_skills)
    with get_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE data_agent_jobs SET status='awaiting_agent',worker_id=NULL,lease_until=NULL,updated_at=? "
            "WHERE status='running' AND lease_until IS NOT NULL AND lease_until<?",
            (now_text, now_text),
        )
        target_cycle_row = db.execute(
            f"SELECT cycle_run_id FROM data_agent_jobs WHERE status='awaiting_agent' "
            f"AND skill_name IN ({placeholders}) ORDER BY priority DESC,id LIMIT 1",
            clean_skills,
        ).fetchone()
        if not target_cycle_row:
            db.commit()
            return []
        target_cycle_id = int(target_cycle_row[0])
        active_rows = db.execute(
            "SELECT * FROM data_agent_jobs WHERE status='running' AND worker_id=? "
            "AND cycle_run_id=? ORDER BY priority DESC,id LIMIT ?",
            (worker_id, target_cycle_id, max(1, limit)),
        ).fetchall()
        if active_rows:
            db.commit()
            return [
                {
                    **dict(row),
                    "input": _decode(row["input_json"], {}),
                    "input_json": None,
                    "worker_id": worker_id,
                    "lease_until": row["lease_until"],
                }
                for row in active_rows
            ]
        rows = db.execute(
            f"SELECT * FROM data_agent_jobs WHERE status='awaiting_agent' AND cycle_run_id=? "
            f"AND skill_name IN ({placeholders}) ORDER BY priority DESC,id LIMIT ?",
            [target_cycle_id, *clean_skills, max(1, limit)],
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            id_marks = ",".join("?" for _ in ids)
            db.execute(
                f"UPDATE data_agent_jobs SET status='running',worker_id=?,lease_until=?,"
                f"started_at=COALESCE(started_at,?),attempt_count=attempt_count+1,updated_at=? "
                f"WHERE id IN ({id_marks})",
                [worker_id, lease_until, now_text, now_text, *ids],
            )
        db.commit()
    return [
        {
            **dict(row),
            "input": _decode(row["input_json"], {}),
            "input_json": None,
            "worker_id": worker_id,
            "lease_until": lease_until,
        }
        for row in rows
    ]


def recover_agent_jobs(
    cycle_run_id: int,
    worker_id: str,
    *,
    db_path: str | Path | None = None,
    reason: str = "worker interrupted",
) -> dict[str, Any]:
    """Release jobs held by one interrupted worker so a recovery run can resume now."""
    now = iso_utc(datetime.now(timezone.utc))
    with get_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "UPDATE data_agent_jobs SET status='awaiting_agent',worker_id=NULL,lease_until=NULL,"
            "updated_at=? WHERE cycle_run_id=? AND status='running' AND worker_id=?",
            (now, cycle_run_id, worker_id),
        )
        released = int(cursor.rowcount or 0)
        if released:
            db.execute(
                "UPDATE update_runs SET status='awaiting_agent',finished_at=NULL WHERE id=?",
                (cycle_run_id,),
            )
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,'operator','lease_recovery','success',?,?,?)",
                (
                    cycle_run_id,
                    f"已释放中断 worker 持有的 {released} 个任务，等待重新领取。",
                    _json({"worker_id": worker_id, "released_jobs": released, "reason": reason}),
                    now,
                ),
            )
        db.commit()
    return {
        "cycle_run_id": cycle_run_id,
        "worker_id": worker_id,
        "released_jobs": released,
        "reason": reason,
    }


def recover_target_jobs(
    cycle_run_id: int,
    *,
    db_path: str | Path | None = None,
    reason: str = "target fetch interrupted",
    older_than_minutes: int = 0,
) -> dict[str, Any]:
    """Requeue only target fetches abandoned after their process has exited."""
    now_dt = datetime.now(timezone.utc)
    now = iso_utc(now_dt)
    cutoff = iso_utc(now_dt - timedelta(minutes=max(0, older_than_minutes)))
    with get_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        ids = [int(row[0]) for row in db.execute(
            "SELECT id FROM data_agent_jobs WHERE cycle_run_id=? "
            "AND job_type='source_target_refresh' AND status='running' "
            "AND worker_id='deterministic-target-fetcher' AND started_at<?",
            (cycle_run_id, cutoff),
        ).fetchall()]
        cursor = db.execute(
            "UPDATE data_agent_jobs SET status='queued',worker_id=NULL,started_at=NULL,"
            "updated_at=? WHERE cycle_run_id=? AND job_type='source_target_refresh' "
            "AND status='running' AND worker_id='deterministic-target-fetcher' "
            "AND started_at<?",
            (now, cycle_run_id, cutoff),
        )
        released = int(cursor.rowcount or 0)
        if released:
            db.execute(
                "UPDATE update_runs SET status='awaiting_agent',finished_at=NULL WHERE id=? "
                "AND run_type='data_agent_cycle'",
                (cycle_run_id,),
            )
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,'operator','target_recovery','success',?,?,?)",
                (
                    cycle_run_id,
                    f"已重新排队 {released} 个中断的第二轮目标 URL 抓取任务。",
                    _json({"released_jobs": released, "reason": reason}),
                    now,
                ),
            )
        db.commit()
    return {"cycle_run_id": cycle_run_id, "released_jobs": released,
            "job_ids": ids, "reason": reason}


def abort_agent_cycle(
    cycle_run_id: int,
    *,
    db_path: str | Path | None = None,
    reason: str = "operator cancelled",
) -> dict[str, Any]:
    """Fail only unfinished control-plane jobs for an interrupted cycle."""
    now = iso_utc(datetime.now(timezone.utc))
    with get_db(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "UPDATE data_agent_jobs SET status='failed',finished_at=?,lease_until=NULL,"
            "error_summary=?,updated_at=? WHERE cycle_run_id=? "
            "AND status IN ('queued','running','awaiting_agent')",
            (now, reason, now, cycle_run_id),
        )
        failed_jobs = int(cursor.rowcount or 0)
        counts = {
            str(row["status"]): int(row["count"])
            for row in db.execute(
                "SELECT status,COUNT(*) AS count FROM data_agent_jobs "
                "WHERE cycle_run_id=? GROUP BY status",
                (cycle_run_id,),
            ).fetchall()
        }
        db.execute(
            "UPDATE update_runs SET status='failed',finished_at=?,error_summary=?,counts_json=? "
            "WHERE id=? AND run_type='data_agent_cycle'",
            (now, reason, _json(counts), cycle_run_id),
        )
        db.execute(
            "INSERT INTO update_run_events "
            "(run_id,actor,stage,status,message,details_json,created_at) "
            "VALUES (?,'operator','cycle_aborted','failed',?,?,?)",
            (
                cycle_run_id,
                f"周期被安全终止；{failed_jobs} 个未完成任务标记为失败。",
                _json({"failed_jobs": failed_jobs, "reason": reason}),
                now,
            ),
        )
        db.commit()
    return {
        "cycle_run_id": cycle_run_id,
        "failed_jobs": failed_jobs,
        "reason": reason,
    }


def finish_agent_job(
    job_id: int,
    worker_id: str,
    *,
    status: str,
    output: dict[str, Any] | None = None,
    error_summary: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    if status not in {"succeeded", "rejected", "failed"}:
        raise ValueError("status 必须是 succeeded、rejected 或 failed。")
    now = iso_utc(datetime.now(timezone.utc))
    resolved_db_path = Path(db_path).resolve() if db_path is not None else get_db_path().resolve()
    snapshot_root = (resolved_db_path.parent / "source_snapshots").resolve()
    candidate_ids: list[int] = []
    detail_job_ids: list[int] = []
    with get_db(db_path) as db:
        job = db.execute(
            "SELECT * FROM data_agent_jobs WHERE id=? AND status='running' AND worker_id=?",
            (job_id, worker_id),
        ).fetchone()
        if not job:
            raise RuntimeError("任务不存在、未被当前 worker 持有或租约状态已变化。")
        job_item = dict(job)
        cycle_row = db.execute(
            "SELECT mode FROM update_runs WHERE id=?", (job_item["cycle_run_id"],)
        ).fetchone()
        chip_test = bool(cycle_row and cycle_row["mode"] == "test")
        result = output or {}
        if status == "succeeded":
            facts = result.get("facts", [])
            messages = result.get("inbox", [])
            discovered_sources = result.get("discovered_sources", [])
            if not isinstance(facts, list) or not isinstance(messages, list) or not isinstance(discovered_sources, list):
                raise ValueError("成功结果中的 facts、inbox 和 discovered_sources 必须是数组。")
            source_input = _decode(job_item.get("input_json"), {})
            source_diff_id = source_input.get("source_diff_id")
            source_check_id = source_input.get("source_check_id")
            link_id = job_item.get("link_id")
            if job_item["job_type"] in {"agent_discovery", "agent_url_discovery"} and facts:
                raise ValueError("发现任务只能提交 discovered_sources，必须先抓取快照后才能形成事实。")
            evidence_source_text = ""
            if job_item["job_type"] == "agent_extract":
                evidence_scope = str(source_input.get("evidence_scope") or "diff")
                if evidence_scope == "diff":
                    if not source_diff_id:
                        raise ValueError("变化提取任务缺少 source_diff_id，不能验证原文证据。")
                    source_row = db.execute(
                        "SELECT diff_path AS evidence_path FROM source_diffs WHERE id=?",
                        (source_diff_id,),
                    ).fetchone()
                elif evidence_scope == "snapshot":
                    source_row = {"evidence_path": source_input.get("snapshot_path")}
                else:
                    raise ValueError("不支持的证据范围。")
                if not source_row or not source_row["evidence_path"]:
                    raise ValueError("提取任务没有可读取的正文证据。")
                evidence_path = Path(str(source_row["evidence_path"])).resolve()
                try:
                    evidence_path.relative_to(snapshot_root)
                except ValueError as exc:
                    raise ValueError("正文证据文件不在受信任的 source_snapshots 目录中。") from exc
                evidence_text = evidence_path.read_text(encoding="utf-8")
                if evidence_scope == "diff":
                    evidence_source_text = "\n".join(
                        line[1:] for line in evidence_text.splitlines()
                        if line.startswith("+") and not line.startswith("+++")
                    ).casefold()
                else:
                    evidence_source_text = evidence_text.casefold()
            inserted = 0
            for fact in facts:
                if not isinstance(fact, dict):
                    raise ValueError("facts 每一项都必须是对象。")
                field_name = str(fact.get("field_name") or "").strip()
                proposed_value = fact.get("proposed_value")
                evidence = str(fact.get("evidence_text") or "").strip()
                source_url = str(fact.get("source_url") or source_input.get("url") or "").strip()
                entity_key = fact.get("entity_key")
                target_table = str(fact.get("target_table") or "")
                skill_targets = SKILL_FIELD_TARGETS.get(str(job_item["skill_name"]), {})
                if not field_name or proposed_value is None or not str(proposed_value).strip():
                    raise ValueError("候选事实必须提供 field_name 和非空 proposed_value。")
                if not isinstance(entity_key, dict) or not entity_key:
                    raise ValueError(f"候选字段 {field_name} 缺少 entity_key。")
                if target_table not in skill_targets or field_name not in skill_targets[target_table]:
                    owners = sorted(
                        skill for skill, targets in SKILL_FIELD_TARGETS.items()
                        if field_name in targets.get(target_table, set())
                    )
                    if owners:
                        raise ValueError(
                            f"字段 {target_table}.{field_name} 属于 {','.join(owners)}，"
                            f"当前 {job_item['skill_name']} 应写入 inbox。"
                        )
                    raise ValueError(
                        f"字段 {target_table}.{field_name} 未列入任何 Skill 的可发布字段。"
                    )
                if chip_test and (
                    target_table != "chips" or str(job_item["skill_name"]) not in CHIP_TEST_SKILLS
                ):
                    raise ValueError("芯片隔离测试只接受芯片目录、规格和算力字段。")
                owner = str(job_item["skill_name"])
                if target_table not in {
                    "chips", "models", "chip_model_benchmarks",
                    "chip_model_compatibility", "deployment_guides",
                }:
                    raise ValueError(f"不允许的目标表：{target_table}。")
                entity_key = normalize_fact_entity_key(db, target_table, entity_key)
                validate_fact_value(target_table, field_name, proposed_value)
                parsed = urlparse(source_url)
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError(f"候选字段 {field_name} 必须提供 HTTPS 来源。")
                if not evidence:
                    raise ValueError(f"候选字段 {field_name} 缺少逐字段原文证据。")
                if job_item["job_type"] == "agent_extract" and evidence.casefold() not in evidence_source_text:
                    raise ValueError(f"候选字段 {field_name} 的证据不在本轮可信正文中。")
                expected_url = str(source_input.get("url") or "").strip()
                if expected_url and source_url.rstrip("/") != expected_url.rstrip("/"):
                    raise ValueError(f"候选字段 {field_name} 的来源 URL 与抓取任务不一致。")
                existing = db.execute(
                    "SELECT id FROM extraction_candidates WHERE cycle_run_id=? "
                    "AND COALESCE(source_diff_id,0)=COALESCE(?,0) AND owner_skill=? "
                    "AND target_table=? AND entity_key_json=? AND source_url=? "
                    "AND field_name=? AND COALESCE(proposed_value,'')='' ORDER BY id LIMIT 1",
                    (job_item["cycle_run_id"], source_diff_id, owner,
                     target_table, _json(entity_key), source_url, field_name),
                ).fetchone()
                values = (
                    target_table,
                    str(fact.get("entity_type") or job_item.get("entity_type") or ""),
                    _json(entity_key),
                    str(proposed_value).strip(),
                    str(fact.get("unit") or ""),
                    evidence,
                    str(fact.get("evidence_location") or "snapshot"),
                    source_url,
                    str(fact.get("source_type") or "official_page"),
                    str(fact.get("confidence") or "medium"),
                    "ready_to_publish",
                    now,
                )
                if existing:
                    db.execute(
                        "UPDATE extraction_candidates SET target_table=?,entity_type=?,"
                        "entity_key_json=?,proposed_value=?,unit=?,evidence_text=?,"
                        "evidence_location=?,source_url=?,source_type=?,confidence=?,"
                        "status=?,validated_at=? WHERE id=?",
                        (*values, int(existing["id"])),
                    )
                    candidate_ids.append(int(existing["id"]))
                else:
                    cursor = db.execute(
                        "INSERT INTO extraction_candidates "
                        "(cycle_run_id,source_check_id,source_diff_id,link_id,owner_skill,"
                        "target_table,entity_type,entity_key_json,field_name,proposed_value,"
                        "unit,evidence_text,evidence_location,source_url,source_type,confidence,"
                        "status,extractor_version,created_at,validated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            job_item["cycle_run_id"], source_check_id, source_diff_id,
                            link_id, owner, target_table,
                            str(fact.get("entity_type") or job_item.get("entity_type") or ""),
                            _json(entity_key), field_name, str(proposed_value).strip(),
                            str(fact.get("unit") or ""), evidence,
                            str(fact.get("evidence_location") or "snapshot"), source_url,
                            str(fact.get("source_type") or "official_page"),
                            str(fact.get("confidence") or "medium"), "ready_to_publish",
                            PARSER_VERSION, now, now,
                        ),
                    )
                    candidate_ids.append(int(cursor.lastrowid))
                from chip_model.pipeline.run_history import capture_candidate_baseline
                capture_candidate_baseline(db, candidate_ids[-1])
                inserted += 1
            discovered_count = 0
            raw_parent_depth = source_input.get("crawl_depth")
            parent_depth = int(raw_parent_depth) if raw_parent_depth is not None else -1
            child_depth = 0 if job_item["job_type"] == "agent_discovery" else parent_depth + 1
            if len(discovered_sources) > 50:
                raise ValueError("单个发现任务最多返回 50 个目标 URL。")
            if chip_test and len(discovered_sources) > 3:
                raise ValueError("芯片隔离测试每个父页面最多发现 3 个目标 URL。")
            allowed_discovered_urls: set[str] = set()
            if job_item["job_type"] == "agent_url_discovery":
                for item in source_input.get("outbound_links") or []:
                    try:
                        allowed_discovered_urls.add(canonicalize_source_url(str(item)))
                    except (ValueError, SourceRefreshError):
                        continue
            for source in discovered_sources:
                if not isinstance(source, dict):
                    raise ValueError("discovered_sources 每一项都必须是对象。")
                url = str(source.get("url") or "").strip()
                description = str(source.get("description") or "").strip()
                category = str(source.get("category") or "").strip()
                if not description or not category:
                    raise ValueError("新来源必须提供 HTTPS URL、description 和 category。")
                canonical = canonicalize_source_url(url)
                if chip_test:
                    from chip_model.pipeline.link_families import is_chip_source
                    if not is_chip_source(url, category):
                        raise ValueError("芯片隔离测试不接受模型或非芯片候选链接。")
                    parent_host = urlparse(str(source_input.get("url") or "")).hostname
                    if parent_host and urlparse(url).hostname != parent_host:
                        raise ValueError("芯片隔离测试只发现同一父URL站点内的目标页。")
                if (
                    job_item["job_type"] == "agent_url_discovery"
                    and canonical not in allowed_discovered_urls
                ):
                    raise ValueError("发现 URL 不在首轮页面提取的链接清单中。")
                role = str(source.get("role") or classify_source_role(url, description, category))
                if role not in {"listing", "detail", "api", "document"}:
                    raise ValueError("新来源 role 必须是 listing、detail、api 或 document。")
                if child_depth > MAX_CRAWL_DEPTH:
                    raise ValueError(f"目标 URL 超过最大抓取深度 {MAX_CRAWL_DEPTH}。")
                existing_link = db.execute(
                    "SELECT l.id FROM link_library l LEFT JOIN source_registry r ON r.link_id=l.id "
                    "WHERE r.canonical_url=? OR l.url=? ORDER BY l.id LIMIT 1",
                    (canonical, url),
                ).fetchone()
                if not existing_link:
                    cursor = db.execute(
                        "INSERT INTO link_library "
                        "(url,description,vendor,category,access_method,accessible,created_at,updated_at) "
                        "VALUES (?,?,?,?, 'web','待检查',?,?)",
                        (url, description, str(source.get("vendor") or ""), category, now, now),
                    )
                    child_link_id = int(cursor.lastrowid)
                else:
                    child_link_id = int(existing_link["id"])
                db.execute(
                    "INSERT OR IGNORE INTO source_registry "
                    "(link_id,canonical_url,url_role,crawl_depth,parent_link_id,discovery_job_id,"
                    "discovered_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        child_link_id, canonical, role, child_depth, link_id,
                        job_id, now, now,
                    ),
                )
                edge = db.execute(
                    "INSERT OR IGNORE INTO source_discovery_edges "
                    "(cycle_run_id,discovery_job_id,parent_link_id,child_link_id,source_url,"
                    "canonical_url,url_role,crawl_depth,status,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?, 'queued',?)",
                    (
                        job_item["cycle_run_id"], job_id, link_id, child_link_id,
                        url, canonical, role, child_depth, now,
                    ),
                )
                if int(edge.rowcount or 0) == 0:
                    continue
                discovered_count += 1
                prior_visit = db.execute(
                    "SELECT 1 FROM data_agent_jobs WHERE cycle_run_id=? AND link_id=? "
                    "AND job_type IN ('source_refresh','source_target_refresh') LIMIT 1",
                    (job_item["cycle_run_id"], child_link_id),
                ).fetchone()
                if prior_visit:
                    db.execute(
                        "UPDATE source_discovery_edges SET status='skipped',reason='本轮已访问，复用已有快照' "
                        "WHERE id=?", (edge.lastrowid,),
                    )
                    continue
                if chip_test:
                    visit_count = int(db.execute(
                        "SELECT COUNT(*) FROM data_agent_jobs WHERE cycle_run_id=? "
                        "AND job_type IN ('source_refresh','source_target_refresh')",
                        (job_item["cycle_run_id"],),
                    ).fetchone()[0])
                    if visit_count >= 10:
                        db.execute(
                            "UPDATE source_discovery_edges SET status='skipped',reason='隔离测试最多访问10个来源' "
                            "WHERE id=?", (edge.lastrowid,),
                        )
                        continue
                target_skills = _category_skills(category, description)
                if chip_test:
                    target_skills = [skill for skill in target_skills if skill in CHIP_TEST_SKILLS]
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
                detail_cursor = db.execute(
                    "INSERT OR IGNORE INTO data_agent_jobs "
                    "(cycle_run_id,dedupe_key,job_type,skill_name,link_id,priority,status,"
                    "scheduled_for,input_json,created_at,updated_at) "
                    "VALUES (?,?, 'source_target_refresh','source-refresh',?,90,'queued',?,?,?,?)",
                    (
                        job_item["cycle_run_id"],
                        f"cycle:{job_item['cycle_run_id']}:target:{digest}",
                        child_link_id,
                        now,
                        _json(
                            {
                                "url": url,
                                "canonical_url": canonical,
                                "url_role": role,
                                "crawl_depth": child_depth,
                                "parent_link_id": link_id,
                                "discovery_job_id": job_id,
                                "target_skills": target_skills,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                if int(detail_cursor.rowcount or 0) > 0:
                    detail_job_ids.append(int(detail_cursor.lastrowid))
            for message in messages:
                if not isinstance(message, dict):
                    raise ValueError("inbox 每一项都必须是对象。")
                to_skill = str(message.get("to_skill") or "").strip()
                topic = str(message.get("topic") or "").strip()
                if to_skill not in SKILL_POLICIES or not topic:
                    raise ValueError("inbox 必须提供有效 to_skill 和 topic。")
                if chip_test and to_skill not in CHIP_TEST_SKILLS:
                    raise ValueError("芯片隔离测试不得转交模型或其他领域任务。")
                db.execute(
                    "INSERT INTO skill_inbox "
                    "(cycle_run_id,from_skill,to_skill,link_id,source_check_id,content_hash,"
                    "topic,payload_json,status,created_at) VALUES (?,?,?,?,?,?,?,?, 'pending',?)",
                    (
                        job_item["cycle_run_id"], job_item["skill_name"], to_skill,
                        link_id, source_check_id, source_input.get("content_hash"), topic,
                        _json(message.get("payload") or {}), now,
                    ),
                )
            if source_check_id:
                db.execute(
                    "UPDATE parse_ledger SET status='success',extracted_count=?,finished_at=? "
                    "WHERE source_check_id=? AND skill_name=?",
                    (inserted, now, source_check_id, job_item["skill_name"]),
                )
            result = {
                **result,
                "candidate_count": inserted,
                "inbox_count": len(messages),
                "discovered_source_count": discovered_count,
                "detail_job_ids": detail_job_ids,
            }
        cursor = db.execute(
            "UPDATE data_agent_jobs SET status=?,output_json=?,error_summary=?,"
            "finished_at=?,lease_until=NULL,updated_at=? "
            "WHERE id=? AND status='running' AND worker_id=?",
            (status, _json(result), error_summary, now, now, job_id, worker_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("任务不存在、未被当前 worker 持有或租约状态已变化。")
        cycle_run_id = int(job_item["cycle_run_id"])
        counts = {
            str(row["status"]): int(row["count"])
            for row in db.execute(
                "SELECT status,COUNT(*) AS count FROM data_agent_jobs "
                "WHERE cycle_run_id=? GROUP BY status",
                (cycle_run_id,),
            ).fetchall()
        }
        unfinished = counts.get("awaiting_agent", 0) + counts.get("running", 0) + counts.get("queued", 0)
        final_status = None
        if unfinished == 0:
            pending_publish = int(
                db.execute(
                    "SELECT COUNT(*) FROM extraction_candidates "
                    "WHERE cycle_run_id=? AND status='ready_to_publish'",
                    (cycle_run_id,),
                ).fetchone()[0]
            )
            final_status = (
                "awaiting_publish"
                if pending_publish
                else ("partial" if counts.get("failed", 0) else "success")
            )
            final_counts = {**counts, "awaiting_publish": pending_publish}
            db.execute(
                "UPDATE update_runs SET status=?,finished_at=?,counts_json=? WHERE id=?",
                (final_status, now, _json(final_counts), cycle_run_id),
            )
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,'orchestrator','cycle_completed',?,?,?,?)",
                (
                    cycle_run_id,
                    final_status,
                    "全部 Agent 任务完成，等待候选字段发布。"
                    if pending_publish else "全部 Agent 任务及发布流程完成，周期关闭。",
                    _json(final_counts),
                    now,
                ),
            )
        db.commit()
    return {
        "status": status,
        "job_id": job_id,
        "cycle_run_id": cycle_run_id,
        "candidate_ids": candidate_ids,
        "detail_job_ids": detail_job_ids,
        "cycle_status": final_status,
    }


def revalidate_ready_candidates(
    cycle_run_id: int, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Audit already queued facts without touching business tables."""
    now = iso_utc(datetime.now(timezone.utc))
    rejected: list[dict[str, Any]] = []
    valid = 0
    with get_db(db_path) as db:
        rows = db.execute(
            "SELECT id,target_table,entity_key_json,source_url,field_name,proposed_value "
            "FROM extraction_candidates WHERE cycle_run_id=? AND status='ready_to_publish' "
            "ORDER BY id", (cycle_run_id,),
        ).fetchall()
        groups: dict[tuple[str, str, str], list[Any]] = {}
        for row in rows:
            group_key = (row["target_table"], row["entity_key_json"], row["source_url"])
            groups.setdefault(group_key, []).append(row)
        for group_rows in groups.values():
            normalized = None
            reason = ""
            try:
                for row in group_rows:
                    key = json.loads(row["entity_key_json"] or "{}")
                    normalized = normalize_fact_entity_key(db, row["target_table"], key)
                    validate_fact_value(row["target_table"], row["field_name"], row["proposed_value"])
            except (ValueError, TypeError, CandidateValidationError) as exc:
                reason = str(exc)
            for row in group_rows:
                if reason:
                    db.execute(
                        "UPDATE extraction_candidates SET status='rejected',rejection_reason=?,validated_at=? "
                        "WHERE id=?", ("同源同实体批次校验失败：" + reason, now, row["id"]),
                    )
                    rejected.append({"id": int(row["id"]), "reason": reason})
                else:
                    db.execute(
                        "UPDATE extraction_candidates SET entity_key_json=?,validated_at=? WHERE id=?",
                        (_json(normalized), now, row["id"]),
                    )
                    valid += 1
        if rejected:
            db.execute(
                "INSERT INTO update_run_events "
                "(run_id,actor,stage,status,message,details_json,created_at) "
                "VALUES (?,'validator','candidate_revalidation','partial',?,?,?)",
                (cycle_run_id, f"拒绝 {len(rejected)} 条实体键或字段值不兼容的候选；未写业务库。",
                 _json({"rejected": rejected}), now),
            )
        remaining = db.execute(
            "SELECT COUNT(*) FROM extraction_candidates WHERE cycle_run_id=? "
            "AND status='ready_to_publish'", (cycle_run_id,),
        ).fetchone()[0]
        unfinished = db.execute(
            "SELECT COUNT(*) FROM data_agent_jobs WHERE cycle_run_id=? "
            "AND status IN ('awaiting_agent','running','queued')", (cycle_run_id,),
        ).fetchone()[0]
        if not remaining and not unfinished:
            current = db.execute(
                "SELECT status FROM update_runs WHERE id=?", (cycle_run_id,),
            ).fetchone()
            if current and current["status"] == "awaiting_publish":
                db.execute(
                    "UPDATE update_runs SET status='partial',finished_at=? WHERE id=?",
                    (now, cycle_run_id),
                )
        db.commit()
    return {"cycle_run_id": cycle_run_id, "valid": valid, "rejected": rejected}


def get_data_agent_status(
    *, db_path: str | Path | None = None, run_id: int | None = None, limit: int = 100
) -> dict[str, Any]:
    with get_db(db_path, readonly=True) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_agent_jobs'"
        ).fetchone()
        if not table:
            return {"available": False, "latest_cycle": None, "jobs": [], "discoveries": [], "candidates": [], "inbox": [], "publications": [], "totals": {}}
        if run_id is None:
            row = db.execute(
                "SELECT * FROM update_runs WHERE run_type='data_agent_cycle' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM update_runs WHERE id=? AND run_type='data_agent_cycle'", (run_id,)
            ).fetchone()
        if not row:
            return {"available": True, "latest_cycle": None, "jobs": [], "discoveries": [], "candidates": [], "inbox": [], "publications": [], "totals": {}}
        cycle = dict(row)
        cycle["counts"] = _decode(cycle.pop("counts_json", None), {})
        status_counts = {
            str(item["status"]): int(item["count"])
            for item in db.execute(
                "SELECT status,COUNT(*) AS count FROM data_agent_jobs "
                "WHERE cycle_run_id=? GROUP BY status", (cycle["id"],),
            ).fetchall()
        }
        totals = {
            "jobs": sum(status_counts.values()),
            "statuses": status_counts,
            "discoveries": int(db.execute(
                "SELECT COUNT(*) FROM source_discovery_edges WHERE cycle_run_id=?",
                (cycle["id"],),
            ).fetchone()[0]),
            "candidates": int(db.execute(
                "SELECT COUNT(*) FROM extraction_candidates WHERE cycle_run_id=?",
                (cycle["id"],),
            ).fetchone()[0]),
        }
        events = [
            {**dict(event), "details": _decode(event["details_json"], {})}
            for event in db.execute(
                "SELECT * FROM update_run_events WHERE run_id=? ORDER BY id", (cycle["id"],)
            ).fetchall()
        ]
        for event in events:
            event.pop("details_json", None)
        jobs = []
        for job in db.execute(
            "SELECT j.*,p.url AS parent_url FROM data_agent_jobs j "
            "LEFT JOIN source_registry r ON r.link_id=j.link_id "
            "LEFT JOIN link_library p ON p.id=r.parent_link_id "
            "WHERE j.cycle_run_id=? ORDER BY j.id DESC LIMIT ?",
            (cycle["id"], max(1, limit)),
        ).fetchall():
            item = dict(job)
            item["input"] = _decode(item.pop("input_json", None), {})
            item["output"] = _decode(item.pop("output_json", None), {})
            jobs.append(item)
        candidates = [
            dict(item)
            for item in db.execute(
                "SELECT * FROM extraction_candidates WHERE cycle_run_id=? ORDER BY id DESC LIMIT ?",
                (cycle["id"], max(1, limit)),
            ).fetchall()
        ]
        discoveries = [
            dict(item)
            for item in db.execute(
                "SELECT e.*,p.url AS parent_url,c.url AS child_url "
                "FROM source_discovery_edges e "
                "LEFT JOIN link_library p ON p.id=e.parent_link_id "
                "LEFT JOIN link_library c ON c.id=e.child_link_id "
                "WHERE e.cycle_run_id=? ORDER BY e.id DESC LIMIT ?",
                (cycle["id"], max(1, limit)),
            ).fetchall()
        ]
        inbox = []
        for row_item in db.execute(
            "SELECT * FROM skill_inbox WHERE cycle_run_id=? ORDER BY id LIMIT ?",
            (cycle["id"], max(1, limit)),
        ).fetchall():
            item = dict(row_item)
            item["payload"] = _decode(item.pop("payload_json", None), {})
            inbox.append(item)
        publications = []
        for row_item in db.execute(
            "SELECT * FROM candidate_publications WHERE cycle_run_id=? ORDER BY id DESC LIMIT ?",
            (cycle["id"], max(1, limit)),
        ).fetchall():
            item = dict(row_item)
            for key in ("candidate_ids_json", "old_values_json", "new_values_json", "validation_json"):
                item[key.removesuffix("_json")] = _decode(item.pop(key, None), [] if key == "candidate_ids_json" else {})
            publications.append(item)
    cycle["events"] = events
    return {
        "available": True,
        "skill_policies": SKILL_POLICIES,
        "latest_cycle": cycle,
        "totals": totals,
        "jobs": jobs,
        "discoveries": discoveries,
        "candidates": candidates,
        "inbox": inbox,
        "publications": publications,
    }
