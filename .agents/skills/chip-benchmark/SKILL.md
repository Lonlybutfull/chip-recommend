---
name: chip-benchmark
description: 当需要从开放互联网查找带模型、卡数、精度、输入输出或并发条件的 AI 芯片训练与推理实测结果时使用。
metadata:
  version: 1.2.0
  mode: test-only
---

# 芯片实测资料发现

## 概述

查找能够解释和复核的芯片 × 模型实测记录。核心原则是指标不能脱离测试条件：芯片、模型、卡数、精度和场景无法对应时，单独的高吞吐数字没有入库意义。

## 何时使用

- 使用：查找训练或推理吞吐、时延、显存、MFU 等真实测试结果。
- 不使用：厂商理论峰值交给 `chip-compute`；只有支持关系交给 `chip-compatibility`；部署步骤交给 `chip-deployment`。
- 新闻转述的倍数提升、没有基线的排名和无测试条件宣传不得作为实测。

## 输入与运行入口

可限定目标指标，减少无关搜索和模型输出：

```bash
python scripts/run_open_web_test.py --skill chip-benchmark --chip "NVIDIA H100"
python scripts/run_open_web_test.py --skill chip-benchmark --chip "NVIDIA H100" --field throughput_tok_s --field time_to_first_token_ms
```

共享运行器负责搜索、URL 安全、去重、访问、网页快照、证据校验和隔离保存。目标表是 `chip_model_benchmarks`。

## 负责字段

| 分组 | 字段 |
|---|---|
| 测试对象 | `model_id`、`suite_name`、`workload_type` |
| 硬件与软件条件 | `hardware_config`、`chip_count`、`framework`、`precision` |
| 请求条件 | `batch_size`、`input_seq_length`、`output_seq_length`、`concurrency` |
| 性能指标 | `throughput_tok_s`、`prefill_throughput`、`decode_throughput`、`time_to_first_token_ms`、`inter_token_latency_ms`、`tpot_ms`、`memory_peak_mb` |
| 训练与补充 | `mfu_pct`、`test_date`、`notes` |

同一来源有多组卡数、精度或并发时必须分开理解，不能把不同表格行拼成一条“最佳配置”。未出现的条件保持为空，不猜默认值。

## 执行流程

1. 根据芯片名、目标指标和“benchmark / MLPerf / throughput / latency / 实测”等来源词生成搜索计划。
2. 粗筛必须同时出现目标芯片和评测、吞吐、时延、并发或输入输出线索；理论规格页和无数据的对比文章不通过。
3. 来源优先级为 MLPerf/MLCommons、论文、厂商测试报告、公开数据集和可复现实测记录。运行器完成 URL 安全检查、去重并保存网页快照。
4. 精确复核至少需要一个性能指标及其测试条件。表头、脚注和单位必须能与具体结果行对应；芯片数量、模型、精度缺失时记录拒绝原因。
5. 为每个字段复制网页中的逐字证据，保留原始单位。不要把每卡吞吐和总吞吐、TTFT 和 TPOT、训练与推理结果混为一项。
6. 页面还包含兼容或部署资料时加入 `information_categories`；本 Skill 的 `extracted_fields` 只来自负责字段，再由运行器校验实体、格式、范围和证据。

## 输出与留痕

- `url_assets.jsonl`：保存搜索词、URL、访问结果、网页快照、复核原因、`information_categories` 和 `extracted_fields`。
- `extracted_facts.jsonl`：逐字段保存候选值、单位、逐字证据、置信度和拒绝原因。
- 分类与事实只写隔离数据库的 `test_url_classifications`、`test_extracted_records`。

本 Skill 不写正式数据库；测试结果也不会因数值更高而自动覆盖已有 benchmark。

## 常见错误

- 将理论 TFLOPS/TOPS 当成模型实测。
- 把 8 卡总吞吐记录为单卡吞吐，或忽略张量并行/数据并行配置。
- 混用 TTFT、单 Token 时延和端到端时延。
- 只保留最好结果而丢弃对应输入输出、并发和精度。

## 完成标准

通过记录能够回答“什么模型、什么芯片、几卡、什么精度和请求条件、测得什么指标、来源在哪里”；字段有网页快照中的逐字证据；不完整或冲突配置明确拒绝；正式数据库保持不变。
