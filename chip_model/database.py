"""SQLite database layer for Parse1 — connection management + query interface.

V4 — 5-table equality: every table has dedicated query functions.
All functions return dicts/lists (sqlite3.Row → dict). No SQL leaks to cli layer.
"""

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Filter types
# ---------------------------------------------------------------------------


@dataclass
class ChipFilters:
    search: str | None = None
    vendor: str | None = None
    region: str | None = None
    usage: str | None = None
    vram_min: float | None = None
    vram_max: float | None = None
    tdp_max: float | None = None
    price_max: float | None = None
    interconnect_min: float | None = None
    tier: str | None = None
    min_maturity: int | None = None
    for_model: str | None = None
    scenario: str | None = None


@dataclass
class ModelFilters:
    search: str | None = None
    architecture: str | None = None
    params_min: float | None = None
    params_max: float | None = None
    for_chip: str | None = None


@dataclass
class BenchmarkFilters:
    chip: str | None = None
    model: str | None = None
    workload: str | None = None
    suite: str | None = None


@dataclass
class CompatFilters:
    chip: str | None = None
    model: str | None = None
    status: str | None = None


@dataclass
class ProvenanceFilters:
    table_name: str | None = None
    row_id: str | None = None
    field_name: str | None = None
    source_type: str | None = None
    confidence: str | None = None
    is_official: str | None = None


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent


def get_project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return _PROJECT_ROOT


def get_data_dir() -> Path:
    """Return the absolute path to the data/ directory."""
    return _PROJECT_ROOT / "data"


DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "parse1.db"


def get_db_path() -> Path:
    env_path = os.environ.get("PARSE1_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def set_db_path(path: str | Path) -> None:
    os.environ["PARSE1_DB_PATH"] = str(Path(path).resolve())


@contextmanager
def get_db(db_path: str | Path | None = None, readonly: bool = False):
    path = str(db_path) if db_path else str(get_db_path())
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    uri = f"file:{path}"
    if readonly:
        uri += "?mode=ro"

    conn = sqlite3.connect(uri if readonly else path, uri=readonly)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    schema_path = _PROJECT_ROOT / "schema.sql"
    with get_db(db_path) as db:
        schema = schema_path.read_text(encoding="utf-8")
        db.executescript(schema)
        db.commit()
    print(f"[DB] Database initialized at {db_path or get_db_path()}")


def get_db_stats(db_path: str | Path | None = None) -> dict:
    with get_db(db_path, readonly=True) as db:
        stats = {}
        tables = [
            "chips", "models", "chip_model_benchmarks",
            "chip_model_compatibility", "field_provenance",
        ]
        for table in tables:
            try:
                row = db.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                stats[table] = row["cnt"] if row else 0
            except sqlite3.OperationalError:
                stats[table] = None
        return stats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _count(db, table: str, conditions: list[str], params: list) -> int:
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    row = db.execute(f"SELECT COUNT(*) as cnt FROM {table} {where}", params).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# chips table
# ---------------------------------------------------------------------------

def search_chips(
    filters: ChipFilters,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
    include_provenance: bool = False,
) -> dict:
    """Search chips with multi-condition filtering.

    Supports --for-model auto VRAM estimation.
    When ``include_provenance=True``, each chip gets a ``_provenance`` key
    with a compact provenance summary.
    """
    with get_db(db_path, readonly=True) as db:
        conditions: list[str] = []
        params: list = []

        # ── for_model: auto-estimate VRAM ──
        if filters.for_model:
            model = db.execute(
                "SELECT total_params_b, model_id FROM models "
                "WHERE model_id LIKE ? OR author LIKE ? LIMIT 1",
                (f"%{filters.for_model}%", f"%{filters.for_model}%"),
            ).fetchone()
            if model:
                model_dict = dict(model)
                total_params = float(model_dict.get("total_params_b", 0) or 0)
                if filters.scenario == "train":
                    inferred_vram = total_params * 12 * 1.3
                    conditions.append("(usage LIKE ? OR usage LIKE ?)")
                    params.extend(["%训推%", "%训练%"])
                else:
                    inferred_vram = total_params * 2 * 1.25
                if filters.vram_min is not None:
                    inferred_vram = max(inferred_vram, filters.vram_min)
                conditions.append("CAST(vram_gb AS REAL) >= ?")
                params.append(round(inferred_vram, 1))
            else:
                # Model not found — force zero results
                conditions.append("1=0")

        # ── search ──
        if filters.search:
            conditions.append(
                "(chip_model LIKE ? OR vendor LIKE ? "
                "OR chip_series LIKE ? OR architecture LIKE ?)"
            )
            like = f"%{filters.search}%"
            params.extend([like, like, like, like])

        # ── vendor ──
        if filters.vendor:
            conditions.append("(vendor LIKE ? OR vendor_display LIKE ?)")
            like = f"%{filters.vendor}%"
            params.extend([like, like])

        # ── region ──
        if filters.region:
            r = "domestic" if filters.region in ("domestic", "国产", "国内") else "foreign"
            conditions.append("vendor_region = ?")
            params.append(r)

        # ── usage ──
        if filters.usage:
            # train/training → chips that CAN train (训推一体 OR 训练)
            # inference/infer → chips that CAN infer (训推一体 OR 推理)
            # both → chips that support both (训推一体 only)
            u = filters.usage.lower().strip()
            if u in ("train", "training"):
                conditions.append("(usage LIKE ? OR usage LIKE ?)")
                params.extend(["%训推%", "%训练%"])
            elif u in ("inference", "infer"):
                conditions.append("(usage LIKE ? OR usage LIKE ?)")
                params.extend(["%训推%", "%推理%"])
            elif u == "both":
                conditions.append("(usage LIKE ?)")
                params.append("%训推%")
            else:
                conditions.append("usage LIKE ?")
                params.append(f"%{u}%")

        # ── vram range ──
        if filters.vram_min is not None:
            conditions.append("CAST(vram_gb AS REAL) >= ?")
            params.append(filters.vram_min)
        if filters.vram_max is not None:
            conditions.append("CAST(vram_gb AS REAL) <= ?")
            params.append(filters.vram_max)

        # ── tdp ──
        if filters.tdp_max is not None:
            conditions.append("CAST(tdp_w AS REAL) <= ?")
            params.append(filters.tdp_max)

        # ── price ──
        if filters.price_max is not None:
            conditions.append(
                "(CAST(price_cny_wan AS REAL) <= ? OR price_cny_wan IS NULL)"
            )
            params.append(filters.price_max)

        # ── interconnect ──
        if filters.interconnect_min is not None:
            conditions.append("CAST(interconnect_bw_gb_s AS REAL) >= ?")
            params.append(filters.interconnect_min)

        # ── tier ──
        if filters.tier and filters.tier != "all":
            conditions.append("tier = ?")
            params.append(filters.tier)

        # ── maturity ──
        if filters.min_maturity is not None:
            conditions.append("CAST(maturity_level AS REAL) >= ?")
            params.append(filters.min_maturity)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count = _count(db, "chips", conditions, params[:])

        sql = f"SELECT * FROM chips {where} ORDER BY CAST(vram_gb AS REAL) DESC LIMIT ? OFFSET ?"
        rows = [dict(r) for r in db.execute(sql, params + [limit, offset]).fetchall()]

        # ── Provenance enrichment ──
        summaries: dict[str, dict] = {}
        if include_provenance and rows:
            chip_ids = [str(r["id"]) for r in rows]
            placeholders = ",".join("?" for _ in chip_ids)
            prov_rows = db.execute(
                f"SELECT * FROM field_provenance "
                f"WHERE table_name='chips' AND row_id IN ({placeholders})",
                chip_ids,
            ).fetchall()
            # Group by chip id
            grouped: dict[str, list[dict]] = {}
            for pr in prov_rows:
                rid = str(dict(pr).get("row_id", ""))
                grouped.setdefault(rid, []).append(dict(pr))
            for rid, recs in grouped.items():
                summaries[rid] = _build_provenance_summary(recs)

        result_chips = []
        for r in rows:
            c = chip_summary(r)
            if include_provenance:
                c["_provenance"] = summaries.get(str(r["id"]), {
                    "field_count": 0, "record_count": 0, "sources": [],
                    "confidence": {}, "last_updated": None,
                })
            result_chips.append(c)

    return {"count": count, "chips": result_chips}


def _resolve_chip(db, identifier: str) -> dict | None:
    """Resolve a chip by integer ID or fuzzy name match.

    Try order:
      1. ``identifier`` is a pure digit → WHERE id = ?
      2. chip_model LIKE
      3. Broad multi-field LIKE (vendor, vendor_display, chip_series, architecture)
      4. English alias fallback
    """
    # ── Pure-digit → ID lookup ──
    if identifier.isdigit():
        row = db.execute(
            "SELECT * FROM chips WHERE id = ?", (int(identifier),)
        ).fetchone()
        if row:
            return dict(row)

    like = f"%{identifier}%"

    # 1. Direct chip_model match
    row = db.execute(
        "SELECT * FROM chips WHERE chip_model LIKE ? "
        "ORDER BY CAST(vram_gb AS REAL) DESC LIMIT 1",
        (like,),
    ).fetchone()

    # 2. Broader multi-field search
    if not row:
        row = db.execute(
            "SELECT * FROM chips WHERE "
            "vendor LIKE ? OR vendor_display LIKE ? OR "
            "chip_series LIKE ? OR architecture LIKE ? "
            "ORDER BY CASE WHEN vendor_display LIKE ? THEN 0 ELSE 1 END, "
            "CAST(vram_gb AS REAL) DESC LIMIT 1",
            (like, like, like, like, like),
        ).fetchone()

    # 3. English alias fallback
    if not row:
        alias_map = {
            'ascend': '昇腾',
            'kunlun': '昆仑芯',
            'cambricon': '思元',
            'shenwei': '深算',
            'biren': '壁砺',
            'iluvatar': '天垓',
        }
        name_lower = identifier.lower()
        for eng, chn in alias_map.items():
            if eng in name_lower:
                row = db.execute(
                    "SELECT * FROM chips WHERE chip_series LIKE ? OR chip_model LIKE ? "
                    "ORDER BY CAST(vram_gb AS REAL) DESC LIMIT 1",
                    (f"%{chn}%", f"%{chn}%"),
                ).fetchone()
                if row:
                    break

    return dict(row) if row else None


def _attach_chip_relations(db, chip_data: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch benchmarks, compatibilities, and provenance for a chip row."""
    chip_model = chip_data.get("chip_model", "")
    chip_id = str(chip_data.get("id", ""))

    bm_rows = db.execute(
        "SELECT * FROM chip_model_benchmarks WHERE chip_model LIKE ?",
        (f"%{chip_model}%",),
    ).fetchall()
    benchmarks = [dict(r) for r in bm_rows]

    comp_rows = db.execute(
        "SELECT * FROM chip_model_compatibility WHERE chip_model LIKE ?",
        (f"%{chip_model}%",),
    ).fetchall()
    compatibilities = [dict(r) for r in comp_rows]

    prov_rows = db.execute(
        "SELECT * FROM field_provenance WHERE table_name = 'chips' AND row_id = ?",
        (chip_id,),
    ).fetchall()
    provenance = [dict(r) for r in prov_rows]

    return benchmarks, compatibilities, provenance


def get_chip_profile(identifier: str, db_path: str | Path | None = None) -> dict | None:
    """Full chip profile: all 78 fields + benchmarks + compatibilities + provenance.

    ``identifier`` is auto-detected:
      - Pure digits (e.g. ``"3"``) → exact ``id`` lookup
      - Otherwise → fuzzy name search (chip_model > multi-field > English alias)
    """
    with get_db(db_path, readonly=True) as db:
        chip_data = _resolve_chip(db, identifier)
        if chip_data is None:
            return None
        benchmarks, compatibilities, provenance = _attach_chip_relations(db, chip_data)

    return chip_profile(chip_data,
                        benchmarks=benchmarks,
                        compatibilities=compatibilities,
                        provenance=provenance)


def get_chip_profiles_batch(
    identifiers: list[str],
    db_path: str | Path | None = None,
) -> list[dict]:
    """Batch chip profiles — one per identifier.  Missing chips → None slots."""
    results: list[dict] = []
    with get_db(db_path, readonly=True) as db:
        for ident in identifiers:
            chip_data = _resolve_chip(db, ident)
            if chip_data is None:
                results.append(None)  # type: ignore[arg-type]
                continue
            benchmarks, compatibilities, provenance = _attach_chip_relations(db, chip_data)
            results.append(chip_profile(chip_data,
                                        benchmarks=benchmarks,
                                        compatibilities=compatibilities,
                                        provenance=provenance))
    return results


def get_chip_recommend_candidates(
    model_name: str,
    scenario: str = "train",
    tier: str = "datacenter",
    prefer_domestic: bool = False,
    db_path: str | Path | None = None,
) -> tuple[dict | None, list[dict]]:
    """Find model + candidate chips for recommend pipeline."""
    with get_db(db_path, readonly=True) as db:
        # Find model
        row = db.execute(
            "SELECT * FROM models WHERE model_id LIKE ? OR author LIKE ? LIMIT 1",
            (f"%{model_name}%", f"%{model_name}%"),
        ).fetchone()
        if not row:
            return None, []

        model_data = dict(row)

        # Calculate VRAM
        total_params = float(model_data.get("total_params_b", 0) or 0)
        if scenario == "train":
            min_vram_total = total_params * 12 * 1.3
            usage_cond = "AND (usage LIKE ? OR usage LIKE ?)"
            usage_params = ["%训练%", "%训推%"]
        else:
            min_vram_total = total_params * 2 * 1.25
            usage_cond = ""
            usage_params = []

        min_vram_per_card = max(8.0, min_vram_total / 8)

        # Build query
        region_cond = "AND vendor_region = 'domestic'" if prefer_domestic else ""
        tier_params: list = []
        if tier and tier != "all":
            tier_cond = "AND tier = ?"
            tier_params = [tier]
        else:
            tier_cond = ""

        sql = (
            f"SELECT * FROM chips "
            f"WHERE CAST(vram_gb AS REAL) >= ? {usage_cond} "
            f"{region_cond} {tier_cond} "
            f"ORDER BY CAST(vram_gb AS REAL) DESC LIMIT 30"
        )
        all_params = [min_vram_per_card] + usage_params + tier_params
        candidates = [dict(r) for r in db.execute(sql, all_params).fetchall()]

    return model_data, candidates


# ---------------------------------------------------------------------------
# models table
# ---------------------------------------------------------------------------

def search_models(
    filters: ModelFilters,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
    include_provenance: bool = False,
) -> dict:
    """Search models with filters + optional chip compatibility JOIN.

    When ``include_provenance=True``, each model gets a ``_provenance`` key
    with a compact provenance summary.
    """
    with get_db(db_path, readonly=True) as db:
        conditions: list[str] = []
        params: list = []

        # ── for_chip: JOIN compatibility ──
        if filters.for_chip:
            # English alias fallback for chip search
            chip_terms = [filters.for_chip]
            alias_map = {
                'ascend': '昇腾',
                'kunlun': '昆仑芯',
                'cambricon': '思元',
                'shenwei': '深算',
                'biren': '壁砺',
                'iluvatar': '天垓',
            }
            name_lower = filters.for_chip.lower()
            if name_lower in alias_map:
                chip_terms.append(alias_map[name_lower])
            # Also try partial alias matching
            for eng, chn in alias_map.items():
                if eng in name_lower and eng not in [t.lower() for t in chip_terms]:
                    chip_terms.append(chn)

            like_clauses = " OR ".join(["chip_model LIKE ?" for _ in chip_terms])
            conditions.append(
                f"model_id IN ("
                f"  SELECT DISTINCT model_id FROM chip_model_compatibility "
                f"  WHERE {like_clauses})"
            )
            params.extend([f"%{t}%" for t in chip_terms])

        # ── search ──
        if filters.search:
            conditions.append("(model_id LIKE ? OR author LIKE ?)")
            like = f"%{filters.search}%"
            params.extend([like, like])

        # ── architecture ──
        if filters.architecture:
            arch = "MoE" if filters.architecture.lower() in ("moe", "mixture") else "Dense"
            conditions.append("architecture_family = ?")
            params.append(arch)

        # ── params range ──
        if filters.params_min is not None:
            conditions.append("CAST(total_params_b AS REAL) >= ?")
            params.append(filters.params_min)
        if filters.params_max is not None:
            conditions.append("CAST(total_params_b AS REAL) <= ?")
            params.append(filters.params_max)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count = _count(db, "models", conditions, params[:])

        sql = f"SELECT * FROM models {where} ORDER BY CAST(downloads AS REAL) DESC LIMIT ? OFFSET ?"
        rows = [dict(r) for r in db.execute(sql, params + [limit, offset]).fetchall()]

        # ── Provenance enrichment ──
        summaries: dict[str, dict] = {}
        if include_provenance and rows:
            model_ids = [str(r["id"]) for r in rows]
            placeholders = ",".join("?" for _ in model_ids)
            prov_rows = db.execute(
                f"SELECT * FROM field_provenance "
                f"WHERE table_name='models' AND row_id IN ({placeholders})",
                model_ids,
            ).fetchall()
            grouped: dict[str, list[dict]] = {}
            for pr in prov_rows:
                rid = str(dict(pr).get("row_id", ""))
                grouped.setdefault(rid, []).append(dict(pr))
            for rid, recs in grouped.items():
                summaries[rid] = _build_provenance_summary(recs)

        result_models = []
        for r in rows:
            m = model_summary(r)
            if include_provenance:
                m["_provenance"] = summaries.get(str(r["id"]), {
                    "field_count": 0, "record_count": 0, "sources": [],
                    "confidence": {}, "last_updated": None,
                })
            result_models.append(m)

    return {"count": count, "models": result_models}


def _resolve_model(db, identifier: str) -> dict | None:
    """Resolve a model by integer ID or fuzzy name match."""
    if identifier.isdigit():
        row = db.execute(
            "SELECT * FROM models WHERE id = ?", (int(identifier),)
        ).fetchone()
        if row:
            return dict(row)

    row = db.execute(
        "SELECT * FROM models WHERE model_id LIKE ? OR author LIKE ? "
        "ORDER BY CAST(downloads AS REAL) DESC LIMIT 1",
        (f"%{identifier}%", f"%{identifier}%"),
    ).fetchone()

    if not row:
        row = db.execute(
            "SELECT * FROM models WHERE author LIKE ? "
            "ORDER BY CAST(downloads AS REAL) DESC LIMIT 1",
            (f"%{identifier}%",),
        ).fetchone()

    return dict(row) if row else None


def _attach_model_relations(db, model_data: dict) -> tuple[list[dict], list[dict]]:
    """Fetch compatible chips and provenance for a model row."""
    model_id = model_data.get("model_id", "")
    db_id = str(model_data.get("id", ""))

    comp_rows = db.execute(
        "SELECT * FROM chip_model_compatibility WHERE model_id LIKE ?",
        (f"%{model_id}%",),
    ).fetchall()
    compatible_chips = [dict(r) for r in comp_rows]

    prov_rows = db.execute(
        "SELECT * FROM field_provenance WHERE table_name = 'models' AND row_id = ?",
        (db_id,),
    ).fetchall()
    provenance = [dict(r) for r in prov_rows]

    return compatible_chips, provenance


def get_model_profile(identifier: str, db_path: str | Path | None = None) -> dict | None:
    """Full model profile: all 18 fields + compatible chips + provenance.

    ``identifier`` is auto-detected:
      - Pure digits → exact ``id`` lookup
      - Otherwise → fuzzy name search (model_id > author)
    """
    with get_db(db_path, readonly=True) as db:
        model_data = _resolve_model(db, identifier)
        if model_data is None:
            return None
        compatible_chips, provenance = _attach_model_relations(db, model_data)

    return model_profile(model_data,
                         compatible_chips=compatible_chips,
                         provenance=provenance)


def get_model_profiles_batch(
    identifiers: list[str],
    db_path: str | Path | None = None,
) -> list[dict]:
    """Batch model profiles — one per identifier.  Missing models → None slots."""
    results: list[dict] = []
    with get_db(db_path, readonly=True) as db:
        for ident in identifiers:
            model_data = _resolve_model(db, ident)
            if model_data is None:
                results.append(None)  # type: ignore[arg-type]
                continue
            compatible_chips, provenance = _attach_model_relations(db, model_data)
            results.append(model_profile(model_data,
                                         compatible_chips=compatible_chips,
                                         provenance=provenance))
    return results


# ---------------------------------------------------------------------------
# chip_model_benchmarks table
# ---------------------------------------------------------------------------

def search_benchmarks(
    filters: BenchmarkFilters,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
    include_provenance: bool = False,
) -> dict:
    """Search benchmark records by chip/model/workload/suite.

    When ``include_provenance=True``, each record gets a ``_provenance`` key.
    """
    with get_db(db_path, readonly=True) as db:
        conditions: list[str] = []
        params: list = []

        if filters.chip:
            conditions.append("chip_model LIKE ?")
            params.append(f"%{filters.chip}%")

        if filters.model:
            conditions.append("model_id LIKE ?")
            params.append(f"%{filters.model}%")

        if filters.workload:
            w = filters.workload.lower()
            if w in ("inference", "推理"):
                conditions.append("workload_type = 'inference'")
            elif w in ("training", "train", "训练"):
                conditions.append("workload_type = 'training'")

        if filters.suite:
            conditions.append("suite_name LIKE ?")
            params.append(f"%{filters.suite}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count = _count(db, "chip_model_benchmarks", conditions, params[:])

        sql = f"SELECT * FROM chip_model_benchmarks {where} ORDER BY test_date DESC LIMIT ? OFFSET ?"
        rows = [dict(r) for r in db.execute(sql, params + [limit, offset]).fetchall()]

        # ── Provenance enrichment ──
        summaries: dict[str, dict] = {}
        if include_provenance and rows:
            bm_ids = [str(r["id"]) for r in rows]
            placeholders = ",".join("?" for _ in bm_ids)
            prov_rows = db.execute(
                f"SELECT * FROM field_provenance "
                f"WHERE table_name='chip_model_benchmarks' AND row_id IN ({placeholders})",
                bm_ids,
            ).fetchall()
            grouped: dict[str, list[dict]] = {}
            for pr in prov_rows:
                rid = str(dict(pr).get("row_id", ""))
                grouped.setdefault(rid, []).append(dict(pr))
            for rid, recs in grouped.items():
                summaries[rid] = _build_provenance_summary(recs)

        result = []
        for r in rows:
            bm = group_benchmark(r)
            if include_provenance:
                bm["_provenance"] = summaries.get(str(r["id"]), {
                    "field_count": 0, "record_count": 0, "sources": [],
                    "confidence": {}, "last_updated": None,
                })
            result.append(bm)

    return {"count": count, "benchmarks": result}


# ---------------------------------------------------------------------------
# chip_model_compatibility table
# ---------------------------------------------------------------------------

def search_compat(
    filters: CompatFilters,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
    include_provenance: bool = False,
) -> dict:
    """Search compatibility records by chip/model/status.

    When ``include_provenance=True``, each record gets a ``_provenance`` key.
    """
    with get_db(db_path, readonly=True) as db:
        conditions: list[str] = []
        params: list = []

        if filters.chip:
            conditions.append("chip_model LIKE ?")
            params.append(f"%{filters.chip}%")

        if filters.model:
            conditions.append("model_id LIKE ?")
            params.append(f"%{filters.model}%")

        if filters.status:
            conditions.append("compat_status = ?")
            params.append(filters.status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count = _count(db, "chip_model_compatibility", conditions, params[:])

        sql = f"SELECT * FROM chip_model_compatibility {where} ORDER BY verified_at DESC LIMIT ? OFFSET ?"
        rows = [dict(r) for r in db.execute(sql, params + [limit, offset]).fetchall()]

        # ── Provenance enrichment ──
        summaries: dict[str, dict] = {}
        if include_provenance and rows:
            comp_ids = [str(r["id"]) for r in rows]
            placeholders = ",".join("?" for _ in comp_ids)
            prov_rows = db.execute(
                f"SELECT * FROM field_provenance "
                f"WHERE table_name='chip_model_compatibility' AND row_id IN ({placeholders})",
                comp_ids,
            ).fetchall()
            grouped: dict[str, list[dict]] = {}
            for pr in prov_rows:
                rid = str(dict(pr).get("row_id", ""))
                grouped.setdefault(rid, []).append(dict(pr))
            for rid, recs in grouped.items():
                summaries[rid] = _build_provenance_summary(recs)

        result = []
        for r in rows:
            c = group_compat(r)
            if include_provenance:
                c["_provenance"] = summaries.get(str(r["id"]), {
                    "field_count": 0, "record_count": 0, "sources": [],
                    "confidence": {}, "last_updated": None,
                })
            result.append(c)

    return {"count": count, "compatibilities": result}


# ---------------------------------------------------------------------------
# field_provenance table
# ---------------------------------------------------------------------------

def search_provenance(
    filters: ProvenanceFilters,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> dict:
    """Search field provenance records by table/row/field/source/confidence."""
    with get_db(db_path, readonly=True) as db:
        conditions: list[str] = []
        params: list = []

        if filters.table_name:
            conditions.append("table_name = ?")
            params.append(filters.table_name)

        if filters.row_id:
            conditions.append("row_id = ?")
            params.append(filters.row_id)

        if filters.field_name:
            conditions.append("field_name LIKE ?")
            params.append(f"%{filters.field_name}%")

        if filters.source_type:
            conditions.append("source_type = ?")
            params.append(filters.source_type)

        if filters.confidence:
            conditions.append("confidence = ?")
            params.append(filters.confidence)

        if filters.is_official is not None:
            conditions.append("is_official = ?")
            params.append(filters.is_official)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        count = _count(db, "field_provenance", conditions, params[:])

        sql = f"SELECT * FROM field_provenance {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        rows = [dict(r) for r in db.execute(sql, params + [limit, offset]).fetchall()]

    return {"count": count, "records": rows}


def get_provenance_stats(
    table: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Aggregated provenance statistics — by table, source type, confidence, official vs not."""
    with get_db(db_path, readonly=True) as db:
        table_filter = "WHERE table_name = ?" if table else ""
        t_params = [table] if table else []

        # Total
        total = _count(db, "field_provenance",
                       ["table_name = ?"] if table else [],
                       t_params)

        # By table
        sql_bt = (
            "SELECT table_name, COUNT(*) as cnt FROM field_provenance "
            + (f"WHERE table_name = ? " if table else "") +
            "GROUP BY table_name"
        )
        by_table = {
            r["table_name"]: r["cnt"]
            for r in db.execute(sql_bt, t_params).fetchall()
        }

        # By source_type
        sql_st = (
            "SELECT source_type, COUNT(*) as cnt FROM field_provenance "
            + (f"WHERE table_name = ? " if table else "") +
            "GROUP BY source_type"
        )
        by_source_type = {
            r["source_type"]: r["cnt"]
            for r in db.execute(sql_st, t_params).fetchall()
        }

        # By confidence
        sql_cf = (
            "SELECT confidence, COUNT(*) as cnt FROM field_provenance "
            + (f"WHERE table_name = ? " if table else "") +
            "GROUP BY confidence"
        )
        by_confidence = {
            r["confidence"]: r["cnt"]
            for r in db.execute(sql_cf, t_params).fetchall()
        }

        # By is_official
        sql_of = (
            "SELECT is_official, COUNT(*) as cnt FROM field_provenance "
            + (f"WHERE table_name = ? " if table else "") +
            "GROUP BY is_official"
        )
        official_count = 0
        unofficial_count = 0
        for r in db.execute(sql_of, t_params).fetchall():
            if r["is_official"] == "1":
                official_count = r["cnt"]
            else:
                unofficial_count += r["cnt"]

    return {
        "total": total,
        "by_table": by_table,
        "by_source_type": by_source_type,
        "by_confidence": by_confidence,
        "by_is_official": {
            "official": official_count,
            "unofficial": unofficial_count,
        },
    }


# ---------------------------------------------------------------------------
# Unified output formatters — summary (minimal list) and profile (full grouped)
# ---------------------------------------------------------------------------

# ── chip_summary: 13 core fields for list/search results ──
_CHIP_SUMMARY_FIELDS = [
    "id",
    "vendor_display", "vendor_region",
    "chip_series", "chip_model", "chip_type",
    "architecture", "process_node_nm",
    "vram_gb", "vram_type", "vram_bw_gb_s",
    "precision_perf",
    "tdp_w",
    "interconnect_tech",
    "price_cny_wan",
    "maturity_level", "production_status",
]


def chip_summary(row: dict) -> dict:
    """Reduce a full chip dict to the minimal summary fields for list/search output."""
    return {k: row.get(k) for k in _CHIP_SUMMARY_FIELDS if k in row}


def chip_profile(row: dict,
                 benchmarks: list[dict] | None = None,
                 compatibilities: list[dict] | None = None,
                 provenance: list[dict] | None = None) -> dict:
    """Full chip profile: 78 fields in 14 groups + embedded sub-arrays.

    Provenance is formatted as ``field_provenance``: a per-field history index
    generated by ``_build_provenance_index()``.

    Always use this function to format a chip profile — never hand-construct
    the profile dict in CLI or database code. The grouping defers to
    ``group_chip()`` so the 14-group structure has a single source of truth.
    """
    return {
        "chip": group_chip(row),
        "benchmarks": [group_benchmark(b) for b in (benchmarks or [])],
        "compatibilities": [group_compat(c) for c in (compatibilities or [])],
        "field_provenance": _build_provenance_index(provenance or []),
    }


# ── model_summary: bare-minimum fields for list/search results ──
_MODEL_SUMMARY_FIELDS = [
    "id",
    "model_id", "author",
    "architecture_family", "total_params_b",
    "pipeline_tag", "library_name",
    "downloads", "likes", "last_modified",
]


def model_summary(row: dict) -> dict:
    """Reduce a full model dict to the minimal summary fields for list/search output."""
    return {k: row.get(k) for k in _MODEL_SUMMARY_FIELDS if k in row}


def model_profile(row: dict,
                  compatible_chips: list[dict] | None = None,
                  provenance: list[dict] | None = None) -> dict:
    """Full model profile: 18 fields in 6 groups + embedded sub-arrays.

    Provenance is formatted as ``field_provenance``: a per-field history index.
    """
    return {
        "model": group_model(row),
        "compatible_chips": compatible_chips or [],
        "field_provenance": _build_provenance_index(provenance or []),
    }


# ── chip_recommend_candidate: chip_summary + recommend-specific fields ──


def _make_recommend_rationale(
    chip: dict,
    cards: int,
    est_days: float | None,
    meets_sla: bool,
    eco_strength: str,
    cloud_available: int,
) -> str:
    """Build a one-line rationale string for a recommend candidate."""
    parts = [f"{cards}卡 {chip.get('chip_model', '?')}"]
    if est_days:
        parts.append(f"预计 {est_days} 天")
    if cloud_available:
        parts.append("云可用")
    if eco_strength:
        parts.append(str(eco_strength)[:40])
    if not meets_sla:
        parts.append("[不满足 SLA]")
    return " | ".join(parts)


def chip_recommend_candidate(
    chip: dict,
    vram_cards: int,
    recommended_cards: int,
    estimated_training_days: float | None,
    meets_sla: bool,
    total_cost_wan: float | None,
    score: float,
) -> dict:
    """Format one scored chip for recommend output.

    Chip basics come from chip_summary(); recommend-specific fields
    go into a nested ``recommend`` sub-object.  Single source of truth
    for the recommend candidate shape.
    """
    eco_strength = chip.get("key_strength", "") or ""
    cloud_available = int(float(chip.get("cloud_available", 0) or 0))

    return {
        **chip_summary(chip),
        "recommend": {
            "vram_cards": vram_cards,
            "recommended_cards": recommended_cards,
            "estimated_training_days": estimated_training_days,
            "meets_sla": str(meets_sla).lower(),
            "total_cost_wan": total_cost_wan,
            "score": score,
            "rationale": _make_recommend_rationale(
                chip, recommended_cards, estimated_training_days,
                meets_sla, eco_strength, cloud_available,
            ),
        },
    }


# ---------------------------------------------------------------------------
# Output grouping — flatten 78-column rows into nested logical groups
# ---------------------------------------------------------------------------

# chips 78 fields → 13 groups
_CHIP_FIELD_GROUPS = {
    "identity": [
        "id", "vendor", "vendor_display", "vendor_region", "chip_series",
        "chip_model", "chip_type", "usage", "tier",
    ],
    "architecture": [
        "architecture", "arch_codename", "generation", "process_node_nm",
        "foundry", "die_size_mm2", "transistors_b", "package_type", "is_chiplet",
    ],
    "memory": [
        "vram_gb", "vram_type", "vram_bus_bit", "vram_bw_gb_s", "vram_clock_mhz",
    ],
    "compute_units": [
        "compute_units", "tensor_cores", "rt_cores", "shading_units", "sm_count",
    ],
    "cache": [
        "l1_cache_kb", "l2_cache_mb", "on_chip_sram_mb",
    ],
    "precision": [
        "precision_support", "precision_perf",
    ],
    "clock_power_physical": [
        "base_clock_mhz", "boost_clock_mhz", "tdp_w", "max_power_w",
        "psu_w", "power_connector", "board_length_mm", "board_width_mm",
        "slot_width", "form_factor", "bus_interface",
    ],
    "interconnect": [
        "interconnect_bw_gb_s", "interconnect_tech", "network_interface",
    ],
    "software": [
        "software_stack", "compatible_frameworks",
    ],
    "lifecycle": [
        "release_date", "production_status", "eol_date", "target_market",
        "is_released", "expected_release_date", "known_specs", "unconfirmed_items",
    ],
    "pricing": [
        "price_usd", "price_cny_wan", "price_period", "price_notes",
    ],
    "description": [
        "description", "highlights", "limitations",
        "target_workloads", "typical_deployment", "competitor_comparison",
    ],
    "ecosystem": [
        "ecosystem_notes", "maturity_level", "framework_compat", "sw_stack",
        "cuda_compat", "cloud_available", "cluster_scale",
        "key_strength", "key_weakness",
    ],
    "meta": [
        "created_at", "updated_at",
    ],
}

# models 18 fields → 5 groups
_MODEL_FIELD_GROUPS = {
    "identity": [
        "id", "model_id", "author", "pipeline_tag", "library_name", "tags",
    ],
    "stats": [
        "downloads", "likes", "last_modified",
    ],
    "access": [
        "private", "gated",
    ],
    "architecture": [
        "architecture_family", "total_params_b",
    ],
    "raw": [
        "config_json", "card_data_json", "api_response_json",
    ],
    "meta": [
        "created_at", "updated_at",
    ],
}

# benchmarks 31 fields → 4 groups
_BENCHMARK_FIELD_GROUPS = {
    "identity": [
        "id", "chip_model", "model_id",
    ],
    "test_metadata": [
        "suite_name", "workload_type", "scenario", "task",
        "hardware_config", "chip_count", "framework", "precision",
        "batch_size", "input_seq_length", "output_seq_length", "concurrency",
        "test_date", "notes",
    ],
    "inference_metrics": [
        "prefill_throughput", "decode_throughput",
        "time_to_first_token_ms", "inter_token_latency_ms",
        "memory_peak_mb", "throughput_tok_s", "throughput_samples_s", "tpot_ms",
    ],
    "training_metrics": [
        "mfu_pct", "gpu_hours", "training_tokens_T",
        "training_gpu_count", "training_workload_type",
    ],
    "meta": [
        "created_at",
    ],
}

# compatibility 9 fields → 2 groups
_COMPAT_FIELD_GROUPS = {
    "identity": [
        "id", "chip_model", "model_id",
    ],
    "compat_details": [
        "compat_status", "framework", "precision", "verified_at", "notes",
    ],
    "meta": [
        "created_at",
    ],
}


def _group_fields(row: dict, groups: dict) -> dict:
    """Partition a flat dict into named groups. Only includes groups with
    at least one non-null value (excluding meta groups which are always emitted)."""
    result = {}
    for group_name, fields in groups.items():
        group = {f: row.get(f) for f in fields if f in row}
        if group_name == "meta":
            result[group_name] = group
        elif any(v is not None for v in group.values()):
            result[group_name] = group
    return result


# ---------------------------------------------------------------------------
# Provenance formatting — per-field history index
# ---------------------------------------------------------------------------

def _build_provenance_index(records: list[dict]) -> dict:
    """Convert flat provenance list into a per-field history index.

    Input:  [{"field_name":"vram_gb","old_value":"5","new_value":"24",...}, ...]
    Output: {
      "vram_gb": {
        "field_label": "显存",
        "current_value": "24",
        "update_count": 2,
        "history": [
          {old_value, new_value, source_type, source_url, source_detail,
           confidence, is_official, updated_at, notes},
          ...  (newest first)
        ]
      },
      ...
    }
    """
    if not records:
        return {}

    # Group by field_name
    by_field: dict[str, list[dict]] = {}
    for r in records:
        fn = r.get("field_name", "")
        if not fn:
            continue
        by_field.setdefault(fn, []).append(r)

    result = {}
    for field_name, entries in by_field.items():
        # Sort newest first
        entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        latest = entries[0]
        result[field_name] = {
            "field_label": latest.get("field_label", field_name),
            "current_value": latest.get("new_value", ""),
            "update_count": len(entries),
            "history": [
                {
                    "old_value": e.get("old_value"),
                    "new_value": e.get("new_value"),
                    "source_type": e.get("source_type"),
                    "source_url": e.get("source_url"),
                    "source_detail": e.get("source_detail"),
                    "confidence": e.get("confidence"),
                    "is_official": e.get("is_official"),
                    "updated_at": e.get("updated_at"),
                    "notes": e.get("notes"),
                }
                for e in entries
            ],
        }

    return result


def _build_provenance_summary(records: list[dict]) -> dict:
    """Compact provenance overview for list/search results.

    Returns a lightweight summary instead of full per-field history,
    so search responses stay small.
    """
    if not records:
        return {"field_count": 0, "sources": [], "last_updated": None}

    sources: dict[str, int] = {}
    confidences: dict[str, int] = {}
    last_updated = ""

    for r in records:
        st = r.get("source_type", "")
        if st:
            sources[st] = sources.get(st, 0) + 1
        cf = r.get("confidence", "")
        if cf:
            confidences[cf] = confidences.get(cf, 0) + 1
        ts = r.get("updated_at", "")
        if ts > last_updated:
            last_updated = ts

    # Deduplicate distinct fields
    distinct_fields = len({r.get("field_name") for r in records})

    return {
        "field_count": distinct_fields,
        "record_count": len(records),
        "sources": [
            {"type": k, "count": v}
            for k, v in sorted(sources.items(), key=lambda x: -x[1])
        ],
        "confidence": confidences,
        "last_updated": last_updated,
    }


def group_chip(chip_data: dict) -> dict:
    """Group a flat chip dict into 13 logical sections."""
    return _group_fields(chip_data, _CHIP_FIELD_GROUPS)


def group_model(model_data: dict) -> dict:
    """Group a flat model dict into 5 logical sections."""
    return _group_fields(model_data, _MODEL_FIELD_GROUPS)


def group_benchmark(bm: dict) -> dict:
    """Group a flat benchmark dict into 4 logical sections."""
    return _group_fields(bm, _BENCHMARK_FIELD_GROUPS)


def group_compat(comp: dict) -> dict:
    """Group a flat compatibility dict into 2 logical sections."""
    return _group_fields(comp, _COMPAT_FIELD_GROUPS)


# ---------------------------------------------------------------------------
# Write helpers — used by chip-list-extract and chip-data-enrich skills
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Current time as ISO 8601 string."""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _insert_provenance(db, table_name: str, row_id: int, field_name: str,
                       old_value, new_value: str, source: dict) -> None:
    """Internal: write one field_provenance row."""
    db.execute(
        "INSERT INTO field_provenance "
        "(table_name, row_id, field_name, field_label, old_value, new_value, "
        " source_type, source_url, source_detail, confidence, is_official, updated_at, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            table_name, str(row_id),
            field_name, source.get("field_label", field_name),
            old_value, new_value,
            source.get("source_type", ""),
            source.get("source_url", ""),
            source.get("source_detail", ""),
            source.get("confidence", ""),
            source.get("is_official", "0"),
            source.get("updated_at", _now_iso()),
            source.get("notes", ""),
        ),
    )


def _provenance_for_fields(db, table_name: str, row_id: int, fields: dict,
                           source: dict, old_values: dict | None = None) -> None:
    """Write one field_provenance row per field in ``fields``.

    All rows share the same ``source`` info.  If ``old_values`` is provided,
    each field gets its old_value from that dict (used for UPDATE paths).
    """
    for field_name, new_value in fields.items():
        if field_name in ("id", "created_at", "updated_at"):
            continue
        old = old_values.get(field_name) if old_values else None
        _insert_provenance(db, table_name, row_id, field_name,
                           old, str(new_value) if new_value is not None else "",
                           source)


# ── Chips write helpers ────────────────────────────────────────────────


def add_chip(db, fields: dict, source: dict) -> int:
    """Insert one chip row + provenance records.  ``source`` is shared
    across all fields — the caller does not build provenance lists.

    Args:
        db: An open writeable sqlite3.Connection.
        fields: Column → value dict.  ``id``, ``created_at``, ``updated_at``
                are auto-managed; you may omit them.
        source: Shared source dict with keys:
            ``source_type``, ``source_url``, ``source_detail``,
            ``confidence``, ``is_official``, ``field_label``, ``notes``.

    Returns:
        The new chip's row id.
    """
    now = _now_iso()
    inserts = {"created_at": now, "updated_at": now, **fields}
    if "id" in inserts:
        del inserts["id"]

    cols = list(inserts.keys())
    placeholders = ", ".join(["?" for _ in cols])
    values = [inserts[k] for k in cols]
    db.execute(f"INSERT INTO chips ({', '.join(cols)}) VALUES ({placeholders})",
               values)
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    source["updated_at"] = source.get("updated_at", now)
    _provenance_for_fields(db, "chips", row_id, inserts, source)
    return row_id


def update_chip_fields(db, chip_id: int, fields: dict, source: dict) -> None:
    """UPDATE multiple chip columns + write one provenance record per field.

    Automatically reads old values so provenance tracks the change.
    ``source`` is shared across all updated fields.

    Args:
        db: An open writeable sqlite3.Connection.
        chip_id: The chips.id to update.
        fields: {column_name: new_value, ...}
        source: Shared source dict (same shape as ``add_chip``).
    """
    now = _now_iso()
    source["updated_at"] = source.get("updated_at", now)

    # Batch-read current values
    col_list = ", ".join(fields.keys())
    cur = db.execute(
        f"SELECT {col_list} FROM chips WHERE id = ?", [chip_id]
    ).fetchone()
    if cur is None:
        raise ValueError(f"chip id={chip_id} not found")

    old_values = dict(zip(fields.keys(), cur))

    # Batch-update
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE chips SET {set_clause}, updated_at = ? WHERE id = ?",
        list(fields.values()) + [now, chip_id],
    )

    _provenance_for_fields(db, "chips", chip_id, fields, source,
                           old_values=old_values)


# ── Models write helpers ────────────────────────────────────────────────


def add_model(db, fields: dict, source: dict) -> int:
    """Insert one model row + provenance records.  Same contract as ``add_chip``."""
    now = _now_iso()
    inserts = {"created_at": now, "updated_at": now, **fields}
    if "id" in inserts:
        del inserts["id"]

    cols = list(inserts.keys())
    placeholders = ", ".join(["?" for _ in cols])
    values = [inserts[k] for k in cols]
    db.execute(f"INSERT INTO models ({', '.join(cols)}) VALUES ({placeholders})",
               values)
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    source["updated_at"] = source.get("updated_at", now)
    _provenance_for_fields(db, "models", row_id, inserts, source)
    return row_id


def update_model_fields(db, model_id: int, fields: dict, source: dict) -> None:
    """UPDATE multiple model columns + provenance.  Same contract as ``update_chip_fields``."""
    now = _now_iso()
    source["updated_at"] = source.get("updated_at", now)

    col_list = ", ".join(fields.keys())
    cur = db.execute(
        f"SELECT {col_list} FROM models WHERE id = ?", [model_id]
    ).fetchone()
    if cur is None:
        raise ValueError(f"model id={model_id} not found")

    old_values = dict(zip(fields.keys(), cur))

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE models SET {set_clause}, updated_at = ? WHERE id = ?",
        list(fields.values()) + [now, model_id],
    )

    _provenance_for_fields(db, "models", model_id, fields, source,
                           old_values=old_values)


# ── Benchmark & compatibility write helpers ─────────────────────────────


def add_benchmark(db, fields: dict, source: dict) -> int:
    """Insert one benchmark row + provenance records."""
    now = _now_iso()
    inserts = {"created_at": now, **fields}
    if "id" in inserts:
        del inserts["id"]

    cols = list(inserts.keys())
    db.execute(
        f"INSERT INTO chip_model_benchmarks ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?' for _ in cols])})",
        [inserts[k] for k in cols],
    )
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    source["updated_at"] = source.get("updated_at", now)
    _provenance_for_fields(db, "chip_model_benchmarks", row_id, inserts, source)
    return row_id


def add_compat(db, fields: dict, source: dict) -> int:
    """Insert one compatibility row + provenance records."""
    now = _now_iso()
    inserts = {"created_at": now, **fields}
    if "id" in inserts:
        del inserts["id"]

    cols = list(inserts.keys())
    db.execute(
        f"INSERT INTO chip_model_compatibility ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?' for _ in cols])})",
        [inserts[k] for k in cols],
    )
    row_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    source["updated_at"] = source.get("updated_at", now)
    _provenance_for_fields(db, "chip_model_compatibility", row_id, inserts, source)
    return row_id
