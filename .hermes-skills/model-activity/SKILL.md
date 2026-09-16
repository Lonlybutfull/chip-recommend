---
name: model-activity
description: 更新模型下载量、收藏量、最后修改时间和热门模型集合。
---

# 模型活跃度

只更新 `downloads`、`likes`、`last_modified` 及派生热门集合。保留抓取时间和平台来源，不用活跃度字段覆盖模型规格。优先复用 model-catalog 已抓取的 API 响应。
