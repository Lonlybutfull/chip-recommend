---
name: chip-recommend-cli
version: 2.0.0
description: 使用 AISHPerf 命令行查询芯片、模型、实测、兼容性与来源信息，并生成芯片算力推荐结果。
allowed-tools:
  - Bash
  - Read
triggers:
  - 芯片命令行
  - 查询芯片
  - 芯片推荐
  - 查询评测
  - 查询来源
---

# AISHPerf 命令行使用指南

## 何时使用

需要通过项目 CLI 查询知识图谱、核验数据或生成算力推荐时使用。所有命令从项目根目录运行，输出为 JSON，不得根据记忆补造查询结果。

## 快速入口

```bash
cd E:/BUPT_PS/P_0/chip-recommend
python scripts/run_cli.py <命令组> <命令> [参数]
```

## 命令组

### 芯片查询与推荐

```bash
# 模糊搜索与多条件筛选
python scripts/run_cli.py chip search [--search TEXT] [--vendor TEXT] [--region domestic|foreign] \
  [--usage train|inference|both] [--vram-min GB] [--vram-max GB] [--tdp-max W] \
  [--price-max 万元] [--interconnect-min GB/s] [--tier datacenter|consumer|all] \
  [--min-maturity 0-5] [--for-model TEXT] [--limit N] [--offset N]

# 完整画像：规格、生态、评测、兼容与溯源
python scripts/run_cli.py chip profile <name_or_id> [<name_or_id> ...]

# 算力推荐
python scripts/run_cli.py chip recommend --model TEXT [--scenario train|inference] \
  [--training-days N] [--training-tokens N] [--sla-tps N] \
  [--tier datacenter|all] [--max-cards N] [--min-cards N] \
  [--max-price N] [--min-maturity 0-5] [--domestic] [--prefer-vendor TEXT] \
  [--limit N]
```

### 模型查询

```bash
python scripts/run_cli.py model search [--search TEXT] [--author TEXT] \
  [--pipeline TYPE] [--architecture Dense|MoE] [--params-min B] [--params-max B] \
  [--for-chip TEXT] [--limit N]
python scripts/run_cli.py model profile <name_or_id> [<name_or_id> ...]
```

### 实测与兼容性

```bash
python scripts/run_cli.py benchmark search [--chip TEXT] [--model TEXT] \
  [--workload inference|training] [--suite TEXT] [--limit N]

python scripts/run_cli.py compat search [--chip TEXT] [--model TEXT] \
  [--status verified|vendor_claimed|community] [--limit N]
```

### 来源追溯与数据库状态

```bash
python scripts/run_cli.py provenance show --table chips|models|benchmarks|compat \
  [--row-id N] [--field TEXT]
python scripts/run_cli.py provenance stats
python scripts/run_cli.py db status
python scripts/run_cli.py config show
python scripts/run_cli.py config set --key KEY --value VALUE
```

## 常用处理方式

1. 用户给出模型与场景：先运行 `chip recommend`，训练补充天数和 tokens，推理补充吞吐目标。
2. 用户给出硬件约束：运行 `chip search`，使用显存、地区、用途、层级、功耗或价格筛选。
3. 用户询问某颗芯片：运行 `chip profile`，不要只返回搜索列表的摘要字段。
4. 用户询问实测结果：运行 `benchmark search`，核对 `workload_type`、模型、精度、卡数和吞吐/MFU。
5. 推荐前后需要解释来源时：使用 `provenance show` 或 `provenance stats`。

## 输出解读

- `chip search`：`count` 与 `chips` 列表。
- `chip recommend`：`model`、`requirements`、`scoring_meta` 和 `candidates`。
- `chip profile`：芯片身份、架构、显存、评测、兼容性与来源。
- `benchmark search`：芯片、模型、吞吐、MFU、精度、卡数和测试条件。
- `db status`：各数据表的统计信息。

CLI 输出中文时按 UTF-8 读取。量化模型如 GGUF、GPTQ、AWQ 只用于推理场景；消费级芯片需显式使用 `--tier all`。

## 推荐结果说明

推荐结果应根据当前 CLI 返回的评分版本解释，不在 Skill 中硬编码过时的权重。回答至少包含：

- 前三名候选及总分；
- 显存与卡数是否满足；
- 影响排序的主要评分项；
- 实测数据是否存在；
- 关键取舍和可调整约束。

## 完成标准

- 命令成功执行且 JSON 可解析；
- 回答中的型号、规格、分数和卡数均来自本次命令输出；
- 查询失败时返回实际错误与可重试建议，不用旧数据伪装成功。
