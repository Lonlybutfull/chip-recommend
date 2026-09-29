---
name: chip-compute
description: 当需要从开放互联网补充或核验某颗 AI 芯片支持的数值精度、理论峰值算力或计算单元时使用。
metadata:
  version: 1.2.0
  mode: test-only
---

# 芯片算力指标资料发现

## 概述

查找芯片的理论计算能力。核心原则是同时保留“精度、数值、单位和计算条件”，并严格区分理论峰值、稀疏峰值和模型实测吞吐。

## 何时使用

- 使用：核验支持精度、各精度峰值、计算单元、Tensor Core 或 SM 数量。
- 不使用：模型 tokens/s、QPS、时延等实测数据交给 `chip-benchmark`；显存和功耗交给 `chip-specs`。
- 只有一个无精度、无单位的“算力数字”时不生成字段。

## 输入与运行入口

可检查全部算力字段，也可只指定待核验字段：

```bash
python scripts/run_open_web_test.py --skill chip-compute --chip "NVIDIA H100"
python scripts/run_open_web_test.py --skill chip-compute --chip "NVIDIA H100" --field precision_perf
```

共享运行器负责搜索、URL 安全、去重、访问、网页快照和隔离保存。目标表是 `chips`。

## 负责字段

| 字段 | 含义 | 记录要求 |
|---|---|---|
| `precision_support` | 支持的数值精度 | 如 FP32、TF32、BF16、FP16、FP8、INT8 |
| `precision_perf` | 各精度理论峰值 | 精度与数值成对记录，保留 TFLOPS/TOPS 和稀疏条件 |
| `compute_units` | 通用计算单元数 | 必须明确厂商口径和对应型号 |
| `tensor_cores` | 张量计算单元数 | 不与普通核心或矩阵引擎混算 |
| `sm_count` | SM/流多处理器数量 | 仅在页面明确使用该口径时记录 |

## 执行流程

1. 用芯片名、目标精度或字段，以及“peak performance / architecture whitepaper / 理论峰值”等来源词生成搜索计划。
2. 粗筛要求同时出现目标芯片和精度、TFLOPS/TOPS、计算单元等算力线索；排除只有模型吞吐或营销比较的页面。
3. 优先访问官方规格书、架构白皮书和厂商产品页。运行器执行 HTTPS 校验、去重，并保存网页快照。
4. 精确复核正文：每个峰值都必须能对应精度和芯片型号；稠密、稀疏、矩阵、向量、Boost 等条件分别保留，不能择取最大数字后丢掉限定词。
5. 为字段保存逐字证据。允许确定性单位规范化，但不得从频率、核心数自行推算理论值。
6. 页面同时出现实测或部署信息时写入 `information_categories`；当前 `extracted_fields` 只保留本 Skill 字段，再由运行器校验字段白名单、格式和证据。

## 输出与留痕

- `url_assets.jsonl`：保存 URL、搜索词、粗筛结果、网页快照、`information_categories` 和 `extracted_fields`。
- `extracted_facts.jsonl`：保存精度、数值、单位、条件、逐字证据及拒绝原因。
- 测试记录只进入隔离数据库的 `test_url_classifications` 与 `test_extracted_records`。

本 Skill 不写正式数据库，也不把理论算力自动转换成模型性能。

## 常见错误

- 把 FP8 稀疏峰值记录成普通 FP8 峰值。
- 把 tokens/s、QPS 或 benchmark 分数写入 `precision_perf`。
- 将 CUDA Core、Tensor Core、SM 和厂商自定义核心相互换算。
- 把整机或多卡算力当成单芯片值。

## 完成标准

每个通过值均能明确回答“哪颗芯片、哪种精度、多少算力、什么单位、是否稀疏或有其他限定”；字段有网页快照中的逐字证据；跨类别信息没有混写；正式数据库保持不变。
