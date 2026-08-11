---
name: chip-catalog
description: Extract AI accelerator chips from source CSVs and web search, deduplicate, insert into chips table with field_provenance tracking.
version: 2.0.0
metadata:
  hermes:
    tags: [chip, data, catalog, database]
    related_skills: [chip-enrich]
---

## When to invoke

Extracts AI accelerator chip identities from source materials (CSV files, web search, industry reports). Normalizes and deduplicates into a canonical catalog, then inserts each chip with identity fields + `field_provenance` records using `database.insert_chip()`. This is **Step 1** — hardware specs come next (`chip-enrich`).

## Working directory

All commands run from `/root/chip-recommend/`:

```bash
cd /root/chip-recommend
```

## Key Files

| File | Purpose |
|------|---------|
| `data/信息来源链接库_final.csv` | Source CSV (474 rows) |
| `schema.sql` | DDL reference (78 chip columns) |
| `chip_model/database.py` | `insert_chip()`, `get_db()` — **use these, never raw SQL** |
| `data/data.db` | SQLite database |

## Identity Fields (this skill writes ONLY these)

vendor, vendor_display, vendor_region, chip_series, chip_model, chip_type, usage, tier, production_status, is_released, expected_release_date

## Exclusion Rules

| Category | Keywords | Why |
|----------|----------|-----|
| Servers | Atlas 800, Atlas 900, SuperPoD, 服务器, 集群, Pod | Not chips |
| CPU-only | 鲲鹏, 飞腾, 龙芯, Kunpeng, Phytium, Loongson | Not AI accelerators |
| Edge/auto SoC | 征程, Journey, 华山, 黑芝麻 | Edge SoCs |
| IP licensing | 芯原, VIP, NPU IP | IP cores, not chips |
| HF model IDs | `org/model-name` pattern | Models, not chips |

## Normalization Rules

1. One canonical `chip_model` per variant. Include VRAM when known
2. `vendor` = English slug, `vendor_display` = Chinese display name
3. Sub-variants are separate rows (e.g. 昇腾910B B1, B2, B4)
4. Pre-release chips: `is_released="0"`
5. Standard vendor names: NVIDIA, AMD, Intel, Google, 华为(昇腾), 寒武纪, 壁仞科技, 摩尔线程, 沐曦股份, 燧原科技, 昆仑芯(百度), 海光信息, 景嘉微, 天数智芯

## Insert Pattern

```python
import sqlite3
from chip_model.database import add_chip

conn = sqlite3.connect("data/data.db")
conn.execute("PRAGMA journal_mode=WAL")

SOURCE = {
    "source_type": "community",
    "source_url": "信息来源链接库_final.csv",
    "source_detail": "curated chip catalog v2",
    "confidence": "medium",
    "is_official": "0",
}

fields = {
    "vendor": "nvidia", "vendor_display": "NVIDIA", "vendor_region": "foreign",
    "chip_series": "H100", "chip_model": "H100 SXM5 80GB",
    "chip_type": "GPU", "usage": "训推一体", "tier": "datacenter",
    "production_status": "已量产", "is_released": "1", "expected_release_date": "",
}
row_id = add_chip(conn, fields, SOURCE)
conn.commit(); conn.close()
```

## Verification

```bash
python scripts/run_cli.py db status
```
