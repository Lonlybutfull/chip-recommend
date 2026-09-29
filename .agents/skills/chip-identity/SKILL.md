---
name: chip-identity
description: 当需要从开放互联网补充或核验某颗 AI 芯片的厂商、型号、系列、发布时间或发布状态时使用。
metadata:
  version: 1.2.0
  mode: test-only
---

# 芯片型号资料发现

## 概述

为一个明确的芯片产品查找身份资料。核心原则是先确认“这究竟是哪一颗芯片”，再记录身份字段；系列名、服务器名和相似 SKU 不能互相代替。

## 何时使用

- 使用：新增芯片名录，或核验厂商、型号、系列、发布日期和发布状态。
- 不使用：主要目标是硬件规格、理论算力、兼容、实测或部署资料；应改用对应 Skill。
- 不处理模型目录或 HuggingFace 模型页面。

## 输入与运行入口

至少提供芯片名称或自定义搜索词；只核验部分字段时重复传入 `--field`：

```bash
python scripts/run_open_web_test.py --skill chip-identity --chip "NVIDIA H100"
python scripts/run_open_web_test.py --skill chip-identity --chip "NVIDIA H100" --field release_date
```

运行器负责搜索、URL 安全检查、去重、访问、网页快照、格式校验和隔离保存。Skill 只定义判断与提取规则，目标表是 `chips`。

## 负责字段

| 字段 | 含义 | 接受条件 |
|---|---|---|
| `vendor` | 芯片厂商 | 页面明确给出厂商或品牌归属 |
| `chip_series` | 产品系列 | 系列与具体型号能够对应 |
| `chip_model` | 完整型号 | 保留后缀、形态或版本差异 |
| `release_date` | 发布时间 | 有明确日期、月份或季度 |
| `production_status` | 发布状态 | 已发布、待发布、未公开等有原文依据 |

## 执行流程

1. 根据芯片名称、身份字段和“official product / announcement / 产品发布”等来源词生成有限搜索计划。
2. 粗筛搜索结果：标题或摘要必须同时包含芯片/厂商线索和型号、系列或发布线索；排除服务器整机、集群、模型页和聚合搜索页。
3. 优先访问厂商产品页、官方新闻稿和正式产品简报。共享运行器完成 HTTPS 校验、规范化和去重，并保存网页快照。
4. 精确复核正文：必须能唯一对应目标芯片，并明确出现至少一个负责字段。仅在正文出现系列名时，不得猜测具体型号。
5. 每个字段保留网页中的逐字证据、单位或日期口径。页面还包含其他类别时，可写入 `information_categories`，但本 Skill 的 `extracted_fields` 只能包含上表字段。
6. 交给确定性校验器检查字段白名单、实体、证据和来源；证据不在网页快照中时拒绝。

## 输出与留痕

- `url_assets.jsonl`：记录所有实际访问的 URL、搜索词、粗筛原因、访问结果、`information_categories`、`extracted_fields` 和快照位置。
- `extracted_facts.jsonl`：记录通过或被拒绝的候选字段、逐字证据与原因。
- `test_url_classifications`、`test_extracted_records`：只存在于本次隔离数据库副本。

本 Skill 不写正式数据库，也不把测试 URL 自动晋升为正式链接资产。

## 常见错误

- 把 DGX、整机或集群名称当成芯片型号。
- 把系列名称自动补成某个具体 SKU。
- 依据文章发布时间推断芯片发布日期。
- 页面只提到芯片名称，却仍生成身份字段。

## 完成标准

访问和判定均有记录；通过的字段能唯一绑定芯片并有网页快照中的逐字证据；不属于身份域的信息只标记类别，不混入本 Skill 的字段结果；正式数据库保持不变。
