---
name: chip-specs
description: 当需要从开放互联网补充或核验某颗 AI 芯片的显存、带宽、功耗、制程、形态、架构或互联规格时使用。
metadata:
  version: 1.2.0
  mode: test-only
---

# 芯片基础参数资料发现

## 概述

查找可归属于单颗芯片或单张加速卡的硬件规格。核心原则是“型号、数值、单位和口径同时成立”；不能把整机总量、同系列其他 SKU 或营销形容词当作芯片参数。

## 何时使用

- 使用：补充显存、显存带宽、功耗、制程、产品形态、架构、主机接口或芯片互联。
- 不使用：理论峰值算力交给 `chip-compute`；吞吐和时延交给 `chip-benchmark`；部署步骤交给 `chip-deployment`。
- 型号不明确或页面只有服务器整机规格时，不生成字段。

## 输入与运行入口

先选择缺失、过期或待核验字段；不传 `--field` 时检查本 Skill 的全部字段：

```bash
python scripts/run_open_web_test.py --skill chip-specs --chip "AMD MI300X"
python scripts/run_open_web_test.py --skill chip-specs --chip "AMD MI300X" --field vram_gb --field vram_bw_gb_s
```

共享运行器负责搜索、URL 安全检查、去重、访问、网页快照和隔离保存。目标表是 `chips`。

## 负责字段

| 字段 | 含义 | 记录要求 |
|---|---|---|
| `vram_gb` | 单卡显存容量 | 数值，单位 GB；明确容量版本 |
| `vram_type` | 显存类型 | 如 HBM3、HBM3e、GDDR6 |
| `vram_bw_gb_s` | 单卡显存带宽 | 数值，统一为 GB/s，保留原单位证据 |
| `tdp_w` | 芯片或板卡功耗 | 数值，单位 W；保留 TDP/TBP 口径 |
| `process_node_nm` | 制程 | 数值，单位 nm；不得混入封装节点 |
| `form_factor` | 产品形态 | PCIe、SXM、OAM、模组等 |
| `bus_interface` | 主机接口 | 如 PCIe 5.0 x16、CXL |
| `architecture` | 架构 | 页面明确归属该型号的架构名称 |
| `interconnect_bw_gb_s` | 芯片互联带宽 | 区分单向/双向、单链路/总带宽 |
| `interconnect_tech` | 互联技术 | 如 NVLink、Infinity Fabric、HCCS |

## 执行流程

1. 用芯片名、目标字段和“official specifications / datasheet / 规格书”等来源词生成搜索计划。
2. 粗筛必须同时命中目标芯片和规格线索；排除纯评测、教程、整机配置、价格页面和无数值宣传页。
3. 来源优先级为厂商 Datasheet、官方产品规格页、架构白皮书，其次才是可信技术资料。运行器完成 HTTPS 校验、去重并保存网页快照。
4. 精确复核型号、容量版本和形态。正文必须明确给出数值、单位及适用型号；多 SKU 表格只提取能准确对应的一行。
5. 每个值附逐字证据。只允许确定性单位换算，例如 3.35 TB/s 转为 3350 GB/s；证据仍保留原文。
6. 页面含其他类别时写入 `information_categories`；本 Skill 的 `extracted_fields` 只能来自负责字段。字段白名单、实体、范围和证据由运行器再次校验。

## 输出与留痕

- `url_assets.jsonl`：记录搜索来源、粗筛、访问、网页快照、分类、`information_categories` 和 `extracted_fields`。
- `extracted_facts.jsonl`：记录候选值、单位、逐字证据、置信度及拒绝原因。
- 测试表只写隔离副本中的 `test_url_classifications` 和 `test_extracted_records`。

本 Skill 不写正式数据库，也不自动修改现有芯片规格。

## 常见错误

- 把 8 卡服务器的总显存除以 8 后当成单卡官方值。
- 把显存带宽、互联带宽和 PCIe 带宽混为一项。
- 把芯片 TDP、板卡 TBP 和整机功耗混用。
- 从同系列其他 SKU 复制缺失规格，或用“高带宽”推测数值。

## 完成标准

每个通过字段都能回答“哪一型号、哪个版本、什么数值、什么单位、什么口径、原文在哪里”；跨类别内容只标记不混写；访问和拒绝均可追溯；正式数据库保持不变。
