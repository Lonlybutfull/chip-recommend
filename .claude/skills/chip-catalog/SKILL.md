---
name: chip-catalog
version: 2.0.0
description: Extract AI accelerator chips from source CSVs and web search, deduplicate, insert into chips table with field_provenance tracking.
allowed-tools:
  - Bash
  - Read
  - Write
  - Grep
  - Glob
  - AskUserQuestion
  - WebSearch
  - WebFetch
triggers:
  - chip catalog
  - extract chip list
  - build chip list
  - seed chip names
  - populate chips table
---

## When to invoke

Extracts AI accelerator chip identities from source materials (CSV files, web search, industry reports). Normalizes and deduplicates into a canonical catalog, then inserts each chip with identity fields + `field_provenance` records using `database.insert_chip()`. This is **Step 1** — hardware specs come next (/chip-enrich).

## Working directory

All commands run from the `芯片+模型/` directory:

```bash
cd 芯片+模型
```

## Files this skill depends on

| File | Purpose |
|------|---------|
| `信息来源链接库_final.csv` | Source CSV (474 rows of chip/model/test/price references) |
| `schema.sql` | DDL reference for the 78 chip columns |
| `database.py` | Contains `insert_chip()`, `get_db()` — **use these, never raw SQL** |
| `data.db` | SQLite database |

If any required file is missing, tell the user and stop.

## What "identity fields" means

This skill writes ONLY these fields. Hardware specs are left NULL for `/chip-data-enrich`.

| # | Field | Source |
|---|-------|--------|
| 1 | `vendor` | Normalized from CSV "涉及厂商" column |
| 2 | `vendor_display` | Chinese display name for the vendor |
| 3 | `vendor_region` | `domestic` or `foreign` |
| 4 | `chip_series` | Canonical series name |
| 5 | `chip_model` | Canonical model name (include VRAM suffix when known) |
| 6 | `chip_type` | `GPU` / `NPU` / `DCU` / `TPU` / `LPU` / `ASIC` / `IPU` |
| 7 | `usage` | `训推一体` / `训练` / `推理` |
| 8 | `tier` | `datacenter` / `consumer` / `edge` |
| 9 | `production_status` | `已量产` / `已发布` / `待发布` / `EOL` |
| 10 | `is_released` | `"1"` or `"0"` |
| 11 | `expected_release_date` | Only for pre-release chips (is_released="0") |
| 12 | `created_at` | ISO timestamp |
| 13 | `updated_at` | ISO timestamp |

## Exclusion rules

These categories appear in the CSV but are **NOT chips** — skip them:

| Category | Keywords / patterns | Why |
|----------|---------------------|-----|
| Servers / enclosures | Atlas 800, Atlas 900, SuperPoD, REX, CloudMatrix, 服务器, 集群, Pod, 机柜 | These are server products, not chips |
| CPU-only | 鲲鹏, 飞腾, 龙芯, Kunpeng, Phytium, Loongson, C86-4G, C86-5G, ARM CPU, 服务器CPU | Not AI accelerators |
| Edge/auto SoC | 征程, Journey, 华山, SG2380, 黑芝麻, 地平线, 智能座舱 | Edge/automotive SoCs, not datacenter AI |
| IP licensing | 芯原, VIP, NPU IP | IP cores, not physical chips |
| HF model IDs | Anything matching `org/model-name` pattern | Models, not chips |

**Rule of thumb before inserting any chip**: "Would someone use this to train or serve a 7B+ parameter LLM in a datacenter?" If the answer is no, skip it.

## Normalization rules

1. **One canonical `chip_model` per variant.** Include VRAM when known: `"H100 SXM5 80GB"` not `"H100 SXM"`
2. **`vendor` is the English/romanized slug** (if available), `vendor_display` is the Chinese display name
3. **Sub-variants are separate rows.** 昇腾910B B1, B2, B4 → three rows, all with `chip_series="昇腾910B"`
4. **Pre-release chips get `is_released="0"`** and `expected_release_date` set
5. **Standard vendor names**: `华为(昇腾)` / `寒武纪` / `壁仞科技` / `摩尔线程` / `沐曦股份` / `燧原科技` / `昆仑芯(百度)` / `海光信息` / `景嘉微` / `天数智芯` / `NVIDIA` / `AMD` / `Intel` / `Google` / `AWS` / `Microsoft` / `Meta` / `Groq` / `Cerebras` / `SambaNova` / `Graphcore`

## Workflow

### Step 1 — Read the source CSV

Read `信息来源链接库_final.csv`. Parse the "描述" column for chip name patterns:

- `硬件规格 - CHIP_NAME | ...` → extract CHIP_NAME
- `CHIP_NAME 硬件规格参考` → extract CHIP_NAME
- `CHIP_NAME 生态评估` → extract CHIP_NAME
- Also scan "涉及厂商" for vendor names

Build a raw candidate set. Also check for vendor/product pages in the URL column.

### Step 2 — Supplement with WebSearch

Search for chips that may be missing from the CSV:

```
国产AI加速芯片 全景图 2025 2026 算力芯片 GPU NPU 列表
AI accelerator chip catalog 2025 2026 datacenter GPU NPU TPU list
site:jygpu.com AI芯片 国产 GPU 列表
```

Add chips found from web results that are real datacenter AI accelerators and not already in the candidate set.

### Step 3 — Deduplicate and normalize

Group raw references by canonical chip identity. Apply naming rules from the normalization section above.

When presenting for user review, group by vendor with counts:

```
=== CURATED CHIP LIST ===
NVIDIA (foreign): 14 chips
  Released: A100 SXM4 80GB, A100 PCIe 80GB, H100 SXM5 80GB, H100 NVL 94GB, ...
  Pre-release: —

华为(昇腾) (domestic): 6 chips
  Released: 昇腾910B B1 (64GB), 昇腾910B B2 (64GB), 昇腾910B B4 (64GB), 昇腾910C (OAM 128GB)
  Pre-release: 昇腾950PR (128GB)

=== STATS ===
Total: N chips across M vendors
  Released: R  |  Pre-release: U
  Domestic: D  |  Foreign: F
```

Ask the user to confirm with AskUserQuestion.

### Step 4 — Insert into database

Use a Python script that calls `database.add_chip()`. The script pattern:

```python
import sqlite3
from datetime import datetime
from database import add_chip

CHIPS = [
    # (vendor, vendor_display, vendor_region, chip_series, chip_model,
    #  chip_type, usage, tier, production_status, is_released, expected_release_date)
]

SOURCE = {
    "source_type": "community",
    "source_url": "信息来源链接库_final.csv + curated from web search",
    "source_detail": "curated chip catalog v2",
    "confidence": "medium",
    "is_official": "0",
}

def run():
    conn = sqlite3.connect("data.db")
    conn.execute("PRAGMA journal_mode=WAL")
    now = datetime.now().isoformat()

    inserted = 0
    for chip in CHIPS:
        (vendor, vendor_display, vendor_region, chip_series, chip_model,
         chip_type, usage, tier, prod_status, is_released, exp_rel) = chip

        # Skip if chip_model already exists (idempotent)
        existing = conn.execute(
            "SELECT id FROM chips WHERE chip_model = ?", (chip_model,)
        ).fetchone()
        if existing:
            print(f"  SKIP (exists): {chip_model}")
            continue

        fields = {
            "vendor": vendor,
            "vendor_display": vendor_display,
            "vendor_region": vendor_region,
            "chip_series": chip_series,
            "chip_model": chip_model,
            "chip_type": chip_type,
            "usage": usage,
            "tier": tier,
            "production_status": prod_status,
            "is_released": is_released,
            "expected_release_date": exp_rel,
        }

        row_id = add_chip(conn, fields, SOURCE)
        inserted += 1
        print(f"  INSERT [{row_id}] {vendor_display} — {chip_model}")

    conn.commit()
    conn.close()
    print(f"\nDone: {inserted} chips inserted")

if __name__ == "__main__":
    run()
```

Write this script as `_seed_chip_list.py` and execute it:

```bash
python _seed_chip_list.py
```

### Step 5 — Verify

```bash
python cli.py --db-path data.db db status
```

Report the result. Show chip count per vendor_region.

## Completion status

- **DONE** — chips inserted, provenance records written, verified via `db status`
- **BLOCKED** — source CSV missing, database not initialized
- **NEEDS_CONTEXT** — ambiguous chip identity requiring user resolution
