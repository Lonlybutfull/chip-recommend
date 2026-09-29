---
name: model-activity
description: 当明确安排的模型 API 任务只刷新下载量、点赞量和最后修改时间，而不改变模型规格时使用。
metadata:
  hermes:
    version: 2.0.0
    tags: [aishperf, model, api, activity]
    related_skills: [model-catalog]
---

# 模型活跃度（独立 API 流程）

当前芯片开放互联网流程不得调用。仅在独立模型 API 同步中更新平台活跃度，不使用网页爬取结果替代 API。

## 何时使用

- 使用：任务明确给出模型平台 API 响应、抓取时间和完整模型 ID。
- 不使用：芯片开放网络测试、搜索结果页、模型规格更新或芯片×模型实测。

## 输入契约

当前 Hermes worker 没有独立 activity API 任务，收到旧调度任务时应 `rejected`。未来输入应包含 API `input.url`、平台、抓取时间、完整响应和 `entity_key={"model_id":"组织/完整模型名"}`；输出 `source_url` 复制 `input.url`。优先复用同轮 `model-catalog` 响应。

## 负责字段

目标表 `models`：`downloads`、`likes` 为非负整数文本，`last_modified` 为平台返回的可解析时间字符串。派生热门集合不覆盖模型规格。

## 执行流程

1. 确认完整模型 ID 与平台命名空间。
2. 从同一响应读取下载、收藏和更新时间，记录抓取时间。
3. 为每个字段生成 `facts`，带 `source_url` 和原始 `evidence_text`。
4. 发现身份/架构/参数变化时写入 `inbox` 给 `model-catalog`。
5. API 返回的官方资源可放入 `discovered_sources`，但不送入芯片网页流程。

## 输出契约

返回 `facts`、`inbox`、`discovered_sources`。事实使用 `target_table: "models"`、完整 `entity_key`、`source_url`、`evidence_text`、准确 `source_type` 和枚举置信度；`inbox` 为 `{to_skill,topic,payload}`，来源为 `{url,description,category,vendor,role}`。未获授权的任务返回空数组并以 `rejected` finish envelope 回写。

## 拒绝与失败

- 当前芯片开放网络任务：`rejected`。
- API 不可达、未授权或时间字段不合法：`failed`。
- 只有网页显示的模糊热度：不生成事实。

## 完成标准

三项活跃字段均可回溯到指定 API 和抓取时间，未污染模型规格，也未加入芯片开放互联网任务；不直接改库。
