# 开放互联网芯片信息测试 Runbook

这套流程用于开发阶段验证“已有链接库检查 + 按信息类别搜索新网址 + 网页访问 + 字段提取”。运行结果只写入 `data/test_runs/<session_id>/`，不会把候选字段发布到正式业务表。

## 信息类别

| Skill | 内容 |
|---|---|
| `chip-identity` | 厂商、型号、系列、发布时间和发布状态 |
| `chip-specs` | 显存、带宽、功耗、制程、形态和互联规格 |
| `chip-compute` | 支持精度、理论峰值和计算单元 |
| `chip-compatibility` | 模型、框架、精度和软件栈兼容关系 |
| `chip-benchmark` | 芯片×模型的训练或推理实测数据 |
| `chip-deployment` | 部署后端、软件版本、命令和拓扑资料 |

## 后端手动运行

一次检查六类信息，并先扫描现有 `link_library`：

```bash
python scripts/run_open_web_all_skills.py \
  --db data/data.db \
  --chip "NVIDIA H100" \
  --search-limit 10 \
  --visit-limit 10
```

只检查一个类别和指定字段：

```bash
python scripts/run_open_web_test.py \
  --db data/data.db \
  --chip "NVIDIA H100" \
  --skill chip-specs \
  --field vram_gb \
  --field vram_bw_gb_s \
  --search-limit 10 \
  --visit-limit 10
```

模型接口不可用时，可以对已保存网页快照补做关键词分类：

```bash
python scripts/reclassify_open_web_session.py <session_id> --db data/data.db
```

模型接口配置只从环境读取：

```text
DATA_AGENT_LLM_BASE_URL=https://example.invalid
DATA_AGENT_LLM_API_KEY=<secret>
DATA_AGENT_LLM_MODEL=glm-5.3
```

密钥不得写入仓库、运行 JSON 或页面。

## API

读取最终一次六类检查结果：

```http
GET /api/v1/data-agent/open-web-runs?final_only=true
GET /api/v1/data-agent/open-web-runs/{session_id}
```

管理员也可以从后端队列启动单类测试：

```http
POST /api/v1/data-agent/manual-run
X-Data-Agent-Admin: <admin-token>
Content-Type: application/json

{
  "mode": "test",
  "pipeline": "open_web",
  "skill": "chip-specs",
  "chips": ["NVIDIA H100"],
  "queries": [],
  "target_fields": ["vram_gb", "vram_bw_gb_s"],
  "limit": 10
}
```

该写接口要求管理员令牌和安全传输；公网状态页只读取结果，不提供启动按钮。

## 运行产物

每个会话目录包含：

| 文件 | 说明 |
|---|---|
| `manifest.json` | 运行参数、状态、计数和正式库前后指纹 |
| `search_queries.json` | 搜索词、目标 Skill、芯片和构词策略 |
| `url_candidates.jsonl` | 所有搜索结果、粗筛分数和入选状态 |
| `source_snapshots/` | 实际访问页面的原文与规范化快照 |
| `url_assets.jsonl` | 已有链接和新网址的访问、分类与来源记录 |
| `extracted_facts.jsonl` | 通过或未通过校验的字段候选及原文证据 |
| `events.jsonl` | 按时间记录的执行事件 |
| `full_audit_report.json` | 六类完整检查的最终汇总 |
| `data.db` | 正式库只读复制得到的隔离测试数据库 |

状态页的“最新检查结果”读取这些产物，展示搜索词、发现 Skill、链接访问结果、六类信息标记和已校验字段。页面刷新不会启动任务。

## 验证

```bash
python -m pytest -q
```

重点检查 `manifest.json` 中 `formal_database_modified` 为 `false`，并确认正式数据库 `PRAGMA quick_check` 返回 `ok`。
