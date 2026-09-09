# 心跳上下文约定

预检查脚本最后一行必须是以下两种结果之一：

```json
{"wakeAgent": false}
```

或：

```json
{
  "wakeAgent": true,
  "context": {
    "source_refresh": {
      "trigger": "hermes_ticker",
      "run_id": 12,
      "status": "partial",
      "started_at": "2026-09-09T02:00:01+08:00",
      "finished_at": "2026-09-09T02:00:18+08:00",
      "duration_ms": 17000,
      "counts": {
        "selected": 19,
        "new": 0,
        "changed": 1,
        "unchanged": 14,
        "failed": 4
      },
      "changed_sources": [],
      "new_sources": [],
      "failing_sources": [],
      "business_tables_modified": false
    }
  }
}
```

## 状态含义

- `new`：首次成功获取，建立对比基线。
- `changed`：规范化正文变化，已生成候选差异，等待人工复核。
- `unchanged`：正文未变化或服务端返回 304。
- `failed`：来源返回不可恢复错误，例如 404。
- `retry_wait`：超时、429 或 5xx，已进入退避。
- `unsupported`：当前抓取器不支持该内容类型。

## 与业务字段的关系

候选字段目前覆盖显存容量、显存带宽、功耗、精度性能、制程、互联带宽、架构和计算单元。候选变化只有经过人工确认并通过现有功能测试后，才能写入正式业务表和 `field_provenance`。

