---
name: chip-recommend-cli
description: AISHPerf CLI Skill — chip compute selection command-line tool for querying the chips/models/benchmarks knowledge graph.
version: 2.0.0
metadata:
  hermes:
    tags: [chip, cli, query, benchmark, recommendation]
    related_skills: [chip-selector-agent]
---

# AISHPerf CLI Skill

## Quick Reference

```bash
cd /root/chip-recommend
python scripts/run_cli.py <group> <command> [flags]
```

## Command Groups

### chip — Chip Query

```bash
# Search chips (fuzzy + multi-filter)
python scripts/run_cli.py chip search [--search TEXT] [--vendor TEXT] [--region domestic|foreign]
    [--usage train|inference|both] [--vram-min GB] [--vram-max GB] [--tdp-max W]
    [--price-max WAN] [--interconnect-min GB/s] [--tier datacenter|consumer|all]
    [--min-maturity 0-5] [--for-model TEXT] [--limit N] [--offset N]

# Chip full profile (specs + ecosystem + benchmarks + compatibility + provenance)
python scripts/run_cli.py chip profile <name_or_id> [<name_or_id> ...]

# Recommend chips (v4.2 9-dimension scoring engine)
python scripts/run_cli.py chip recommend --model TEXT [--scenario train|inference]
    [--training-days N] [--training-tokens N] [--sla-tps N]
    [--tier datacenter|all] [--max-cards N] [--min-cards N]
    [--max-price N] [--min-maturity 0-5] [--domestic] [--prefer-vendor TEXT]
    [--limit N]
```

### model — Model Query

```bash
python scripts/run_cli.py model search [--search TEXT] [--author TEXT]
    [--pipeline TYPE] [--architecture Dense|MoE] [--params-min B] [--params-max B]
    [--for-chip TEXT] [--limit N]

python scripts/run_cli.py model profile <name_or_id> [<name_or_id> ...]
```

### benchmark — Benchmark Search

```bash
python scripts/run_cli.py benchmark search [--chip TEXT] [--model TEXT]
    [--workload inference|training] [--suite TEXT] [--limit N]
```

### compat — Compatibility Query

```bash
python scripts/run_cli.py compat search [--chip TEXT] [--model TEXT]
    [--status verified|vendor_claimed|community] [--limit N]
```

### provenance — Provenance Tracking

```bash
python scripts/run_cli.py provenance show --table chips|models|benchmarks|compat [--row-id N] [--field TEXT]
python scripts/run_cli.py provenance stats
```

### db — Database Management

```bash
python scripts/run_cli.py db status
```

## Output Format

All commands output JSON. Key fields:
- `chip search`: `{count, chips: [{id, chip_model, vendor_display, vram_gb, precision_perf, ...}]}`
- `chip recommend`: `{model, requirements, scoring_meta, candidates: [{chip, recommend, scoring}]}`
- `chip profile`: `{chip: {identity, architecture, memory, ...}, benchmarks, compatibilities}`
- `benchmark search`: `{count, benchmarks: [{chip_model, model_id, throughput_tok_s, mfu_pct, ...}]}`
- `db status`: `{tables: {chips: {count}, models: {count}, ...}}`

## Scoring v4.2 (9 Dimensions, 3 Categories)

| Category | Weight | Dimensions |
|----------|--------|------------|
| Compute (50%) | | compute_perf, node_efficiency, bandwidth_ratio |
| Cost-Efficiency (20%) | | cost_efficiency, power_efficiency |
| Ecosystem (30%) | | ecosystem_maturity, framework_toolchain, source_authenticity, production_readiness |

## Key DB Facts

- 1098 chips (27 vendors, 17 domestic + 10 foreign)
- 1370 models (LLM x53, VLM x31, Embedding x16, BERT x18, Audio x11)
- 2103 benchmark records (2081 inference, 22 training)
- 2720 compatibility records
- All data traceable via field_provenance (11613 records, 65% official_datasheet)

## Usage Patterns

**Pattern A: User has model + workload -> recommendations**
```
chip recommend --model <name> --scenario train|inference
```

**Pattern B: User has hardware constraints -> filter**
```
chip search --vram-min 80 --tier datacenter --region domestic
```

**Pattern C: User wants to know about specific chip**
```
chip profile <name>
```

**Pattern D: User wants benchmark data**
```
benchmark search --chip X --model Y
```
