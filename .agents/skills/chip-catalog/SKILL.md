---
name: chip-catalog
version: 2.0.0
description: 从来源表、网页和行业资料中识别 AI 加速芯片，规范化并去重后写入芯片目录，同时保留字段级来源记录。
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
  - 芯片目录
  - 提取芯片清单
  - 构建芯片清单
  - 播种芯片名称
  - 写入芯片表
---

# 芯片目录构建

## 何时使用

需要从 CSV、网页或行业报告中整理“有哪些芯片”时使用。本 Skill 只建立芯片身份与生命周期信息；显存、算力、功耗等规格交给 `chip-enrich`。已有芯片不得重复插入。

## 工作目录与依赖

从项目根目录运行：

```bash
cd E:/BUPT_PS/P_0/chip-recommend
```

| 文件 | 用途 |
|---|---|
| `data/信息来源链接库_final.csv` | 已有来源链接与描述 |
| `schema.sql` | `chips` 与 `field_provenance` 字段定义 |
| `chip_model/database.py` | 统一数据库写入接口 |
| `data/data.db` | SQLite 数据库 |

缺少必需文件时停止并明确报告，不得改用猜测数据。

## 负责字段

仅负责：`vendor`、`vendor_display`、`vendor_region`、`chip_series`、`chip_model`、`chip_type`、`usage`、`tier`、`production_status`、`is_released`、`expected_release_date`、`created_at`、`updated_at`。

- `vendor_region`：`domestic` 或 `foreign`。
- `chip_type`：`GPU`、`NPU`、`DCU`、`TPU`、`LPU`、`ASIC`、`IPU`。
- `usage`：`训推一体`、`训练`、`推理`。
- `tier`：`datacenter`、`consumer`、`edge`。
- `is_released`：字符串 `"1"` 或 `"0"`；未发布时同时填写 `expected_release_date`。

## 排除与规范化规则

1. 排除服务器、集群、机柜、整机与 CPU，例如 Atlas 800、SuperPoD、CloudMatrix、鲲鹏、飞腾、龙芯。
2. 排除车载 SoC、纯 IP 授权和形如 `org/model-name` 的模型标识。
3. 只保留可用于 AI 训练或推理的实际加速芯片。
4. 每个具体变体只有一个规范 `chip_model`；已知显存时写入型号后缀，例如 `H100 SXM5 80GB`。
5. 子型号分别建行；厂商英文或罗马字写入 `vendor`，中文展示名写入 `vendor_display`。

## 执行流程

1. 读取来源 CSV，从“描述”“涉及厂商”和 URL 中提取候选型号。
2. 必要时用开放互联网补齐候选，优先厂商官网与官方产品页。
3. 按厂商、系列、型号、形态和显存变体规范化，合并别名并排除非芯片条目。
4. 查询 `chips`，以规范型号判断是否已存在，保证重复执行不会重复写入。
5. 调用 `chip_model.database` 中的统一接口写入芯片；禁止直接拼接原始 SQL 绕过来源记录。
6. 每个已写字段同步生成 `field_provenance`，记录具体 `source_url`、`source_type`、`confidence` 和 `is_official`。
7. 用 CLI 验证数量、厂商和发布状态：

```bash
python scripts/run_cli.py db status
python scripts/run_cli.py chip search --limit 20
```

## 来源与安全要求

- 官网或官方数据表：`confidence="high"`，并按实际情况设置 `is_official="1"`。
- 可信媒体或行业资料：通常为 `confidence="medium"`。
- 单一社区信息或传闻：`confidence="low"`；关键身份有冲突时不写入。
- 不得把其他芯片的型号或规格复制到当前实体，不得根据常识补造型号。

## 输出要求

报告候选数、排除数、去重数、新增数、已存在数和来源记录数，并按厂商列出新增芯片。对身份冲突项给出型号、冲突来源和待确认点。

## 完成标准

- **完成**：芯片身份已规范化写入，逐字段来源记录完整，CLI 验证通过。
- **带问题完成**：大部分写入成功，但存在明确列出的身份冲突或低可信候选。
- **阻塞**：来源文件缺失、数据库不可用或网络不可达。
- **需要确认**：同一型号存在无法自动判断的实体冲突。
