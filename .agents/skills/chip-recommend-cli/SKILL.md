# AISHPerf CLI Skill — 芯片算力选型命令行工具

You are an expert user of the AISHPerf CLI (`parse1`), a chip & model knowledge graph query tool.
This skill documents every available command, flag, and output format so you can invoke the CLI to answer user questions.

## Quick Reference

```bash
cd e:\BUPT_PS\P_0\chip-recommend
python scripts/run_cli.py <group> <command> [flags]
```

## Command Groups

### 1. chip — 芯片查询与管理

```bash
# 搜索芯片 (模糊搜索 + 多条件筛选)
python scripts/run_cli.py chip search [--search TEXT] [--vendor TEXT] [--region domestic|foreign]
    [--usage train|inference|both] [--vram-min GB] [--vram-max GB] [--tdp-max W]
    [--price-max 万元] [--interconnect-min GB/s] [--tier datacenter|consumer|all]
    [--min-maturity 0-5] [--for-model TEXT] [--limit N] [--offset N]

# 芯片完整画像 (规格+生态+评测+兼容+溯源)
python scripts/run_cli.py chip profile <name_or_id> [<name_or_id> ...]

# 推荐芯片方案 (v2.0 10维评分引擎)
python scripts/run_cli.py chip recommend --model TEXT [--scenario train|inference]
    [--training-days N] [--training-tokens N] [--sla-tps N]
    [--tier datacenter|all] [--max-cards N] [--min-cards N]
    [--max-price N] [--min-maturity 0-5] [--domestic] [--prefer-vendor TEXT]
    [--limit N]
```

### 2. model — 模型查询与管理

```bash
# 搜索模型 (按名称/架构/参数量 + 按芯片反查)
python scripts/run_cli.py model search [--search TEXT] [--author TEXT]
    [--pipeline TYPE] [--architecture Dense|MoE] [--params-min B] [--params-max B]
    [--for-chip TEXT] [--limit N]

# 模型画像 (HF元数据 + 兼容芯片 + 溯源)
python scripts/run_cli.py model profile <name_or_id> [<name_or_id> ...]
```

### 3. benchmark — 评测数据查询

```bash
# 评测数据搜索 (芯片×模型 推理/训练实测)
python scripts/run_cli.py benchmark search [--chip TEXT] [--model TEXT]
    [--workload inference|training] [--suite TEXT] [--limit N]
```

### 4. compat — 兼容性查询

```bash
# 兼容性查询 (按芯片/模型/状态)
python scripts/run_cli.py compat search [--chip TEXT] [--model TEXT]
    [--status verified|vendor_claimed|community] [--limit N]
```

### 5. provenance — 来源追溯

```bash
# 来源追溯查询
python scripts/run_cli.py provenance show --table chips|models|benchmarks|compat [--row-id N] [--field TEXT]

# 来源统计
python scripts/run_cli.py provenance stats
```

### 6. db — 数据库管理

```bash
# 数据库统计信息
python scripts/run_cli.py db status
```

### 7. config — 配置管理

```bash
python scripts/run_cli.py config show
python scripts/run_cli.py config set --key KEY --value VALUE
```

## Key Usage Patterns for Chip Selection

### Pattern A: User describes their model + workload → get recommendations

```
1. Identify model: chip recommend --model <model_name>
2. Identify scenario: train or inference
3. If training: ask for training_days, training_tokens
4. If inference: ask for sla_tps (throughput requirement)
5. Run: chip recommend with all constraints
```

### Pattern B: User has hardware constraints → search chips

```
1. Use chip search with appropriate filters
2. Common filters: vram-min, tier, region, usage, tdp-max
```

### Pattern C: User wants to know about a specific chip

```
1. Use chip profile <name> for full details
```

### Pattern D: User wants benchmark data

```
1. Use benchmark search --chip X --model Y
2. Check workload_type and throughput/mfu
```

## Output Format

All commands output JSON. Key fields:
- `chip search`: `{count, chips: [{id, chip_model, vendor_display, vram_gb, precision_perf, ...}]}`
- `chip recommend`: `{model, requirements, scoring_meta, candidates: [{chip, recommend, scoring}]}`
- `chip profile`: `{chip: {identity, architecture, memory, ...}, benchmarks, compatibilities}`
- `benchmark search`: `{count, benchmarks: [{chip_model, model_id, throughput_tok_s, mfu_pct, ...}]}`
- `db status`: `{tables: {chips: {count}, models: {count}, ...}}`

## Scoring v2.0 (10 Dimensions)

When `chip recommend` runs, it scores every chip on 10 dimensions (0-10 each, weighted total 0-100):
1. compute_perf (15-20%) — FP16 TFLOPS
2. vram_sufficiency (15-20%) — VRAM headroom ratio
3. cost_efficiency (12-15%) — TFLOPS per 万元
4. power_efficiency (8%) — GFLOPS per Watt
5. interconnect_quality (8-12%) — Interconnect BW + tech tier
6. ecosystem_maturity (10-12%) — Maturity level + cloud + compat
7. sla_satisfaction (10%) — Target days/throughput margin
8. production_readiness (5%) — 量产/已发布/未公开
9. benchmark_evidence (7-8%) — Real benchmark data
10. domestic_priority (bonus) — Region/vendor preference

Response includes `scoring.dimensions.<id>.detail` with human-readable formula traces.

## Important Notes

- All Chinese text in chip_model/vendor fields uses UTF-8. Cat with `python -X utf8` if needed.
- The DB has 1098 chips (702 datacenter + 395 consumer + 1 edge)
- Consumer chips (RTX 4090, etc) are available with `--tier all`
- Quantized models (GGUF/GPTQ/AWQ) exist — use inference scenario only
- Training benchmarks: 22 records with MFU data
- Inference benchmarks: 2081 records with throughput data
