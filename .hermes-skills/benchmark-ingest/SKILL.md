---
name: benchmark-ingest
description: 提取芯片×模型训练和推理实测，按完整测试条件去重。
---

# 实测数据

每条记录必须包含芯片、模型、场景、精度、卡数、框架、批量/序列/并发条件、指标、来源和测试日期。实测、厂商声明和理论估算使用不同来源类型；业务唯一键包含测试条件，不能只按芯片和模型去重。

候选的 `target_table` 是 `chip_model_benchmarks`。`entity_key` 必须带现有库中的精确 `chip_model`、完整 `model_id`，以及 `workload_type`（`training`/`inference`/`quantization`）、`suite_name`、正整数 `chip_count` 和 `precision`。只有 `{"chip":"H800","model":"DeepSeek-V3"}` 不够：这不是表字段，也不能区分测试条件。不要把新闻中的数量、场景或框架猜成已验证实测；缺测试条件时给出 `rejected` 原因。已有相同测试条件的记录不重复发布。MFU 的 `mfu_pct` 为 0–100 的百分数。
