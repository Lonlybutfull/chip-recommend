# 两轮 URL 发现链路实测

2026-09-17，本地代码 a7837f9，使用 Moonshot kimi-k2.6 非思考模式。所有操作位于隔离数据库，不发布业务数据。

## 测试设计与验收

首轮只选定一个父页，不预先将详情页加入抓取任务。模型必须从首轮快照的 outbound_links 中选择最多两个同站芯片详情 URL；原有 finish 校验通过后创建 source_target_refresh 任务，再由 advance_target_phase 执行第二轮访问。

最终父页：`https://www.nvidia.com/en-us/data-center/technologies/hopper-architecture/`。

| 阶段 | 任务 / 来源运行 ID | 结果 |
| --- | --- | --- |
| 父页抓取 | 任务 1，来源运行 2 | HTTP 200 |
| 模型 URL 发现 | 任务 2 | 成功，从父页链接清单选出 H100、H200 |
| H100 详情页抓取 | 任务 3，来源运行 3 | HTTP 200 |
| H200 详情页抓取 | 任务 4，来源运行 3 | HTTP 200 |
| 字段提取 | 任务 5–10 | 4 个任务成功（其中 1 个无事实），2 个失败，生成 3 个候选 |

本轮已于北京时间 10:26:14 完成测试执行，无遗留运行中提取任务。任务 5 的模型 JSON 因输出长度限制被截断；任务 10 的精度性能证据不在原文中，被拒绝。3 个候选尚未发布，周期处于 awaiting_publish 不代表已写入业务表。候选仍需语义复核，例如 H100 精度性能输出没有保留稀疏口径，机械校验通过不等于可直接发布。

隔离库 PRAGMA quick_check=ok，foreign_key_check 无错误，candidate_publications=0。

两条 source_discovery_edges 均为 depth=1、role=detail、status=new，并具有 fetched_at。这里的 new 表示已取得新快照，不是仍在等待访问。

已通过真实浏览器展开状态页“两轮 URL 发现链路”，显示 2 / 2，父子 URL 正确；无页面 JavaScript 错误。页面“本轮来源明细（1）”是首轮 source_run_id=2 的摘要，不包含第二轮 source_run_id=3；查看完整访问需结合发现链路和 source_checks。

## 从哪里还原全过程

网页：<http://127.0.0.1:8000/status>，刷新后展开“两轮 URL 发现链路”。

本轮会话目录：

`data/test_runs/local-api-smoke-20260917/test_runs/20260917-022356-c8e1598d/`

- `trace.jsonl`：按时间记录建任务、首轮结束、模型请求/响应、发现校验、第二轮结束、提取校验及最终汇总。每条记录包含任务 ID，可关联数据库。
- `job-2-prompt.json` / `job-2-response.json`：发现任务的完整模型输入和输出，可确认 URL 来自 outbound_links，没有预先指定详情页任务。
- `job-5` 至 `job-10` 的 prompt / response 文件：各字段提取请求与响应。
- `source_snapshots/`：网页原始快照和标准化正文。
- `data.db`：权威测试记录，包括 source_checks、data_agent_jobs、source_discovery_edges、update_run_events、extraction_candidates。
- 完成时导出上述表的 JSON，便于不打开 SQLite 也能查看。
- `two-round-status.png`：展开两轮发现链路后的页面截图。

入口脚本：`data/test_runs/local-api-smoke-20260917/two_round_smoke.py`。脚本通过标准输入读取密钥，不保存密钥。当前是一项诊断测试，尚未接入网页“手动执行”调度。再次运行会创建新会话；共享入口 `two-round-run.json` 指向最新一轮，会话内证据独立保留。

## 前置试验揭示的问题

最初以 NVIDIA 数据中心总目录作为父页，模型选中 data-center-gpus 和 tesla-product-literature 两条旧链接；访问后分别重定向到 Rubin 页面和资源中心。该轮虽完成两轮 HTTP 访问，却没有找到目标芯片，因此未当作详情发现成功。

旧会话 `20260917-022240-856e49ff` 保留原始 trace.jsonl。为遵守两轮范围，额外产生的第三轮发现任务已明确拒绝。随后使用实际含 H100/H200 链接的 Hopper 父页重测，并要求发现结果只允许 detail。

本次验证了真实 URL 发现、校验、任务创建和二轮访问；字段候选是否可靠、自动发布和长期调度属于独立验收项。
