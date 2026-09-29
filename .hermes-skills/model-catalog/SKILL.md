---
name: model-catalog
description: 当明确安排的模型 API 同步任务需要在芯片爬虫之外规范化模型身份、架构、参数、标签和原始元数据时使用。
metadata:
  hermes:
    version: 2.0.0
    tags: [aishperf, model, api, catalog]
    related_skills: [model-activity, data-quality-review]
---

# 模型目录（独立 API 流程）

当前芯片开放互联网流程不得调用。本 Skill 为后续/独立模型 API 同步保留；模型更新应通过指定 API 与代码执行，而不是复用芯片网页搜索任务。

## 何时使用

- 使用：调度器明确创建模型 API 同步任务，并提供可信 API 响应。
- 不使用：芯片开放网络测试、普通 HuggingFace 网页、模型性能新闻或无法访问的外网页面。

## 输入契约

当前 Hermes worker 不领取可执行的模型 API 任务；planner 遗留的 `agent_discovery` 只有 `scope/max_entities`，必须 `rejected`。未来接通时，输入应包含 API `input.url`、抓取时间、平台、规范化响应和完整模型 ID；输出 fact 的 `source_url` 复制 `input.url`。API 原始响应保留，网页摘要不替代 API 数据。

## 负责字段

目标表 `models`：`author`、`pipeline_tag`、`library_name`、`tags`、`architecture_family`、`total_params_b`、`config_json`、`card_data_json`。

## 执行流程

1. 使用 `entity_key={"model_id":"组织/完整模型名"}`，不按显示名合并。
2. 解析明确字段；MoE 的总参数与激活参数不得混淆。
3. 将原始配置保存在 JSON 字段，派生值必须能回溯到 API。
4. 只生成本 Skill `facts`；活跃度进入 `inbox` 给 `model-activity`。
5. API 中的官方仓库/文档可写入 `discovered_sources`，但不自动转入芯片爬虫。

## 输出契约

返回 `facts`、`inbox`、`discovered_sources`。`total_params_b` 为纯数值；`config_json`、`card_data_json` 必须是有效 JSON 字符串。事实包含 `target_table: "models"`、精确 `entity_key`、`source_url`、原始 `evidence_text`、真实 `source_type` 和枚举置信度。`inbox` 为 `{to_skill,topic,payload}`，来源为 `{url,description,category,vendor,role}`；未经独立 API 调度时三数组为空并以 `rejected` 回写 finish envelope。

## 拒绝与失败

- 输入来自芯片开放互联网任务：`rejected`。
- API 401/超时/响应结构异常：`failed`，不回退到网页猜测。
- 模型 ID 不完整或参数口径不明：拒绝相关字段。

## 完成标准

模型实体唯一、原始 API 元数据可追溯、参数口径清楚，且没有把模型任务混入芯片抓取流程；不直接执行 SQL。
