---
name: chip-enrich
description: Fill detailed hardware specs for chip rows using LLM knowledge + WebSearch + WebFetch, writing every field with field_provenance tracking via database.update_chip_fields().
version: 2.0.0
metadata:
  hermes:
    tags: [chip, data, enrichment, specs]
    related_skills: [chip-catalog]
---

## When to invoke

For each chip, searches for specs across all 14 field groups, then writes values with `field_provenance` records using `database.update_chip_fields()`. Chips must already exist in the database (from `chip-catalog`).

## Working directory

```bash
cd /root/chip-recommend
```

## Key Files

| File | Purpose |
|------|---------|
| `data/data.db` | Must have chip rows from chip-catalog |
| `chip_model/database.py` | `update_chip_fields()`, `get_db()` — **use these, never raw SQL** |
| `schema.sql` | DDL reference (78 chip columns) |

## Field Format Conventions (all TEXT)

| Field | Format | Example |
|-------|--------|---------|
| `vram_gb` | Bare number | `"80"` |
| `vram_bw_gb_s` | Bare number | `"3350"` |
| `tdp_w` | Bare number | `"700"` |
| `process_node_nm` | Bare number | `"4"` |
| `die_size_mm2` | Bare number | `"814"` |
| `transistors_b` | Bare number | `"80"` |
| `precision_support` | Comma-separated tags | `"FP32,FP16,BF16,FP8,INT8"` |
| `precision_perf` | `TAG=VALUE` pairs | `"BF16=1980TF,FP8=3960TF"` |
| `price_cny_wan` | Bare number (万元) | `"18"` |
| `maturity_level` | Integer 0-5 | `"5"` |
| `cloud_available` | `"0"` or `"1"` | `"1"` |

## Source Quality Rules

| Level | Criteria |
|-------|----------|
| `high` | Manufacturer datasheet, official product page, MLPerf |
| `medium` | Reputable tech site (AnandTech, ServeTheHome, SemiAnalysis) |
| `low` | Single community source, rumor, LLM inference |

**Critical rule**: LLM-curated (`source_type="llm_curated"`) ONLY allowed for: description, ecosystem, lifecycle groups. Core hardware fields MUST come from WebSearch+WebFetch.

## Write Function

```python
from chip_model.database import update_chip_fields
import sqlite3

conn = sqlite3.connect("data/data.db")
source = {
    "source_type": "official_datasheet",
    "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
    "source_detail": "Specs table > Memory",
    "confidence": "high",
    "is_official": "1",
    "field_label": "H100 Datasheet",
    "notes": "",
}
fields = {"vram_gb": "80", "vram_type": "HBM3", "vram_bw_gb_s": "3350", "tdp_w": "700"}
update_chip_fields(conn, chip_id=3, fields=fields, source=source)
conn.close()
```

## The 14 Field Groups

| # | Group | Critical? |
|---|-------|-----------|
| 1 | `memory` | YES — no LLM |
| 2 | `precision` | YES — no LLM |
| 3 | `clock_power_physical` | YES — no LLM |
| 4 | `architecture` | YES — no LLM |
| 5 | `interconnect` | YES — no LLM |
| 6 | `compute_units` | YES — no LLM |
| 7 | `cache` | YES — no LLM |
| 8 | `software` | YES — no LLM |
| 9 | `pricing` | YES — no LLM |
| 10 | `description` | Non-critical (LLM OK) |
| 11 | `ecosystem` | Non-critical (LLM OK) |
| 12 | `lifecycle` | Non-critical (LLM OK) |
| 13 | `identity` | Already filled |
| 14 | `meta` | System-managed |

## Workflow

1. Find chips needing enrichment: `python scripts/run_cli.py chip search --limit 50`
2. For each critical group: WebSearch -> WebFetch -> extract specs -> `update_chip_fields()`
3. For non-critical groups: try WebSearch first, then LLM knowledge as fallback
4. Verify: `python scripts/run_cli.py chip profile "<chip_model>"`
