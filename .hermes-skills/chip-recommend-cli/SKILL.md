---
name: chip-recommend-cli
description: 使用 AISHPerf 命令行查询芯片、模型、实测、兼容性与来源信息，并生成可解释的芯片算力推荐。
version: 2.0.0
metadata:
  hermes:
    tags: [chip, cli, query, benchmark, recommendation]
    related_skills: [chip-selector-agent]
---

# AISHPerf 命令行使用指南

## 快速入口

```bash
cd /root/chip-recommend
python scripts/run_cli.py <命令组> <命令> [参数]
```

## 芯片查询与推荐

```bash
python scripts/run_cli.py chip search [--search TEXT] [--vendor TEXT] [--region domestic|foreign] \
  [--usage train|inference|both] [--vram-min GB] [--vram-max GB] [--tdp-max W] \
  [--price-max N] [--interconnect-min GB/s] [--tier datacenter|consumer|all] \
  [--min-maturity 0-5] [--for-model TEXT] [--limit N] [--offset N]

python scripts/run_cli.py chip profile <name_or_id> [<name_or_id> ...]

python scripts/run_cli.py chip recommend --model TEXT [--scenario train|inference] \
  [--training-days N] [--training-tokens N] [--sla-tps N] \
  [--tier datacenter|all] [--max-cards N] [--min-cards N] \
  [--max-price N] [--min-maturity 0-5] [--domestic] [--prefer-vendor TEXT] \
  [--limit N]
```

## 模型、实测与兼容性

```bash
python scripts/run_cli.py model search [--search TEXT] [--author TEXT] \
  [--pipeline TYPE] [--architecture Dense|MoE] [--params-min B] [--params-max B] \
  [--for-chip TEXT] [--limit N]
python scripts/run_cli.py model profile <name_or_id> [<name_or_id> ...]

python scripts/run_cli.py benchmark search [--chip TEXT] [--model TEXT] \
  [--workload inference|training] [--suite TEXT] [--limit N]

python scripts/run_cli.py compat search [--chip TEXT] [--model TEXT] \
  [--status verified|vendor_claimed|community] [--limit N]
```

## 来源与状态

```bash
python scripts/run_cli.py provenance show --table chips|models|benchmarks|compat \
  [--row-id N] [--field TEXT]
python scripts/run_cli.py provenance stats
python scripts/run_cli.py db status
```

## 使用规则

- 所有命令输出 JSON；必须根据本次结果回答，不得使用记忆补造。
- 模型与场景明确时先用 `chip recommend`；硬件约束明确时用 `chip search`。
- 查询具体芯片时使用 `chip profile`，查询真实表现时使用 `benchmark search`。
- 推荐结果应说明前三名、显存与卡数、主要评分项、实测证据和方案取舍。
- 评分权重以当前 CLI 返回的 `scoring_meta` 为准，不在 Skill 中硬编码旧版本。
- 量化模型只用于推理；消费级芯片需显式指定 `--tier all`。
- 中文输出按 UTF-8 解析。

## 完成标准

命令成功、JSON 可解析，回答中的型号、规格、分数和卡数来自本次输出。失败时报告真实错误和可重试建议，不得以旧结果伪装成功。
