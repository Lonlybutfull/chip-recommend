---
name: chip-enrich
version: 2.0.0
description: Fill detailed hardware specs for chip rows using LLM knowledge + WebSearch + WebFetch, writing every field with field_provenance tracking via database.update_chip_fields().
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
  - chip enrich
  - enrich chip data
  - fill chip specs
  - search chip hardware
  - add hardware details
---

## When to invoke

For each chip, searches for specs across all 14 field groups, then writes values with `field_provenance` records using `database.update_chip_fields()`. Chips must already exist in the database (from `/chip-catalog`).

## Working directory

All commands run from the `芯片+模型/` directory:

```bash
cd 芯片+模型
```

## Files this skill depends on

| File | Purpose |
|------|---------|
| `parse1.db` | Must have chip rows from `/chip-catalog` |
| `database.py` | Contains `update_chip_field()`, `get_db()`, `get_db_stats()`, `get_chip_profile()` — **use these, never raw SQL** |
| `schema.sql` | DDL reference (78 chip columns, value format conventions) |
| `cli.py` | For verification queries |

## Field format conventions

Every value stored in the database is a TEXT string. Follow these formats exactly:

| Field | Format | Example |
|-------|--------|---------|
| `vram_gb` | Bare number | `"80"` |
| `vram_bw_gb_s` | Bare number | `"3350"` |
| `tdp_w` | Bare number | `"700"` |
| `process_node_nm` | Bare number | `"4"` |
| `die_size_mm2` | Bare number | `"814"` |
| `transistors_b` | Bare number | `"80"` (means 80 billion) |
| `precision_support` | Comma-separated tags | `"FP32,FP16,BF16,FP8,INT8,INT4"` |
| `precision_perf` | Comma-separated `TAG=VALUE` pairs | `"BF16=1980TF,FP8=3960TF,INT8=3960TOPS"` |
| `price_cny_wan` | Bare number (万元/片) | `"18"` |
| `price_usd` | Bare number | `"24000"` |
| `maturity_level` | Integer string 0-5 | `"5"` |
| `cloud_available` | `"0"` or `"1"` | `"1"` |
| `is_chiplet` | `"0"` or `"1"` | `"1"` |
| `base_clock_mhz` | Bare number | `"1530"` |
| `interconnect_bw_gb_s` | Bare number | `"900"` |
| `compute_units` | Bare number | `"16896"` |

## Source quality rules

### Confidence levels

| Level | Criteria |
|-------|----------|
| `high` | Manufacturer datasheet, official product page, MLPerf results |
| `medium` | Reputable tech site (AnandTech, Tom's Hardware, ServeTheHome, SemiAnalysis), industry consensus |
| `low` | Single community source, rumor, unverified forum post, LLM inference |

### `is_official` flag

`"1"` only when the source is the chip manufacturer's own website or published datasheet. Everything else is `"0"`.

### LLM-curated constraint (critical)

**LLM-curated values (`source_type="llm_curated"`, `source_url="LLM curated"`) are ONLY allowed for these non-critical groups:**

- `description` (description, highlights, limitations, target_workloads, typical_deployment, competitor_comparison)
- `ecosystem` (maturity_level, key_strength, key_weakness, ecosystem_notes, framework_compat)
- `lifecycle` (production_status, target_market)

**For all other groups, every value MUST come from a WebSearch + WebFetch cycle that retrieves an actual published page.** If no page is found, leave the field NULL — do NOT fill from LLM training data. A NULL field with no provenance record is honest; a guessed number with a provenance record is data corruption.

### Leave NULL when unknown

If WebSearch returns no reliable source for a field, leave it NULL. Do not:
- Copy specs from a different chip (H100 specs are not H200 specs)
- Invent numbers that "seem reasonable"
- Use LLM knowledge to fill core hardware fields (vram, precision, tdp, etc.)

For pre-release chips (`is_released="0"`), expect most hardware fields to be NULL or sourced from rumors with `confidence="low"`.

## The 14 field groups (from database.py `_CHIP_FIELD_GROUPS`)

Process one group at a time per chip. Use the exact group names below — they match `_CHIP_FIELD_GROUPS`:

| # | Group | Fields | Critical? |
|---|-------|--------|-----------|
| 1 | `memory` | vram_gb, vram_type, vram_bus_bit, vram_bw_gb_s, vram_clock_mhz | **Yes** |
| 2 | `precision` | precision_support, precision_perf | **Yes** |
| 3 | `clock_power_physical` | tdp_w, max_power_w, psu_w, power_connector, board_length_mm, board_width_mm, slot_width, form_factor, bus_interface, base_clock_mhz, boost_clock_mhz | **Yes** |
| 4 | `architecture` | architecture, arch_codename, generation, process_node_nm, foundry, die_size_mm2, transistors_b, package_type, is_chiplet | **Yes** |
| 5 | `interconnect` | interconnect_bw_gb_s, interconnect_tech, network_interface | **Yes** |
| 6 | `compute_units` | compute_units, tensor_cores, rt_cores, shading_units, sm_count | **Yes** |
| 7 | `cache` | l1_cache_kb, l2_cache_mb, on_chip_sram_mb | **Yes** |
| 8 | `software` | software_stack, compatible_frameworks | **Yes** |
| 9 | `pricing` | price_usd, price_cny_wan, price_period, price_notes | **Yes** |
| 10 | `description` | description, highlights, limitations, target_workloads, typical_deployment, competitor_comparison | No (LLM OK) |
| 11 | `ecosystem` | ecosystem_notes, maturity_level, framework_compat, sw_stack, cuda_compat, cloud_available, cluster_scale, key_strength, key_weakness | No (LLM OK) |
| 12 | `lifecycle` | release_date, production_status, eol_date, target_market, is_released, expected_release_date, known_specs, unconfirmed_items | No (LLM OK) |
| 13 | `identity` | vendor, vendor_display, vendor_region, chip_series, chip_model, chip_type, usage, tier | Already filled by Step 1 |
| 14 | `meta` | created_at, updated_at | System-managed, skip |

Groups 1-9 are **critical** — no LLM curation allowed. Groups 10-12 are **non-critical** — LLM curation permitted.

## The write function — always use `database.update_chip_fields()`

```python
from database import update_chip_fields

# All fields from the same source page share one source dict.
# The function automatically writes one field_provenance row per field.
source = {
    "source_type": "official_datasheet",
    "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
    "source_detail": "Specs table > Memory",
    "confidence": "high",
    "is_official": "1",
    "field_label": "H100 Datasheet",
    "notes": "",
}

# Batch-update all specs found on this page in one call.
fields = {
    "vram_gb": "80",
    "vram_type": "HBM3",
    "vram_bw_gb_s": "3350",
    "tdp_w": "700",
    "precision_support": "FP32,FP16,BF16,FP8,INT8,INT4",
    "precision_perf": "BF16=1980TF,FP8=3960TF,INT8=3960TOPS,INT4=7920TOPS",
}

update_chip_fields(conn, chip_id=3, fields=fields, source=source)
# Provenance records for all 6 fields written automatically.
```

**Never write `UPDATE chips SET ...` or `INSERT INTO field_provenance ...` directly.**
Use `update_chip_fields()` — it reads old values, applies the UPDATE, and writes one
provenance record per field, all sharing the same source.

## Workflow

### Step 1 — Find chips to enrich

```bash
python cli.py --db-path parse1.db chip search --limit 200
```

Identify chips that have identity fields but NULL hardware specs (`vram_gb` is NULL, `precision_perf` is NULL, etc.). These need enrichment.

Also get the total count:

```bash
python cli.py --db-path parse1.db db status
```

### Step 2 — Choose scope

Ask the user how many chips to process. Show the total count and recommend a batch size:

> N chips in database. Each chip takes 3-6 WebSearch + WebFetch cycles for critical groups (1-9). Non-critical groups (10-12) can be filled from LLM knowledge if allowed by the user.

Options:
- Process 3-5 high-priority chips first (recommended — validates pipeline)
- Process all chips
- Let me pick a specific vendor or chip name

### Step 3 — For each chip, enrich group by group

Process one chip at a time. For each chip, work through the 14 groups in priority order (1 → 14, skip identity/meta).

For each **critical** group (1-9):

1. **Search.** Use WebSearch with queries like:
   ```
   "ChipModel" specifications datasheet vram tdp
   "ChipModel" precision fp16 bf16 fp8 performance tflops
   site:manufacturer-domain.com "ChipModel" specs
   "厂商名 ChipModel" 规格 参数 显存 功耗
   ```

2. **Fetch the best result.** Use WebFetch on the most authoritative URL (manufacturer page first, then reputable tech site).

3. **Extract specs.** Parse the page for exact values. Match each value to the correct field name.

4. **Write each field.** Call `update_chip_fields()` once per group with all extracted fields.
   Use the exact source URL, not a generic domain. All fields from the same page share one source dict.

5. **Track progress.** After writing, note what was found vs what's still NULL.

For each **non-critical** group (10-12):

1. First attempt WebSearch + WebFetch for official info.
2. If no results: use LLM knowledge, mark `source_type="llm_curated"`, `source_url="LLM curated"`, `confidence="medium"`, `is_official="0"`.

### Step 4 — Verify after each chip

```bash
python cli.py --db-path parse1.db chip profile "<exact chip_model>"
```

Check the output shows newly-filled groups. Show a one-line summary:

```
H100 SXM5 80GB: found memory (3 fields), precision (2), clock_power (2),
  architecture (2), interconnect (2), software (2), pricing (1), ecosystem (5 LLM).
  17 fields filled, 55 still NULL. 17 provenance records written.
```

### Step 5 — Handle partial results

If a search returns ambiguous or contradictory specs for a field, leave it NULL and note the discrepancy. Do NOT pick one value arbitrarily. If the user needs the data, flag it in the summary.

### Step 6 — Final report

```
=== ENRICHMENT COMPLETE ===
Chips processed: N
Fields filled: F (C from WebSearch, L from LLM)
Provenance records written: P
Groups with best coverage: memory (X%), precision (Y%), clock_power (Z%)
Groups needing manual research: pricing (A%), cache (B%)

Remaining chips: M (run /chip-enrich again to continue)
```

## Completion status

- **DONE** — all specified chips enriched, provenance records written, verified via CLI
- **DONE_WITH_CONCERNS** — enrichment completed but critical fields (vram, precision_perf) missing for some chips
- **BLOCKED** — database empty, no chips to enrich, or network unavailable
- **NEEDS_CONTEXT** — ambiguous spec values requiring user judgment (list the specific chips and fields)
