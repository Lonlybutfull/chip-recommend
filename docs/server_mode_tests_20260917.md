# 服务器 detail / 父 URL 模式测试（2026-09-17）

服务器：81.70.231.92，Docker 容器 chip-recommend；使用 lxc 执行。
测试时间：北京时间 11:01–11:06。模型 kimi-k2.6，关闭 thinking，max_tokens=1200，每组并发 2。
两组使用独立测试数据库，禁用发布。父 URL 每个最多发现 1 个详情页，限定两轮抓取。

| 模式 | 首轮抓取 | 第二轮 | 提取任务成功/失败 | 候选字段 |
|---|---|---|---|---|
| detail | 10/10 | 无 | 21/9 | 45 |
| 父 URL | 5/5 | 3/3（2 个 HTTP 200，1 个 HTTP 304） | 4/2 | 5 |

任务成功包含合法的空 facts，不代表每个任务都有字段产出。候选数量不是已更新字段数量，尚未人工审核或发布。
父 URL 的 5 个发现任务均完成，其中 3 个返回目标，2 个返回空目标。NVIDIA、寒武纪未被模型选出目标，不能据此认定网页没有可用链接。
摩尔线程 S5000 返回 304，未生成新提取任务，因此第二轮有 3 个抓取任务、6 个提取任务。

## 校验失败与测试限制

初次诊断脚本额外限制每个响应最多 1 条事实，导致部分响应被拒绝。已对 14 个受此限制影响的任务使用原始响应按正式校验规则复核，没有重复调用模型。初始结果、原始日志、复核事件均保留。
最终 detail 失败 9 个：7 个证据不在可信正文，1 个缺少逐字段证据，1 个实体匹配失败；父 URL 失败 2 个，均为实体键或实体匹配问题。
这说明抓取与两轮调度可运行，但语义提取没有全部通过。当前任务状态 awaiting_publish 表示有候选等待处理，并不代表所有子任务成功。

## 结果入口与日志

[服务器状态页](http://81.70.231.92/status)。刷新后查看以下两个独立任务卡片：

### detail：20260917-030119-339b517b

[完整任务 API](http://81.70.231.92/api/v1/data-agent/runs/20260917-030119-339b517b/38)
服务器目录：`/home/lxc/chip-recommend/data/test_runs/20260917-030119-339b517b/`。
模型响应 30 次，记录 token 总数 69666。

抓取记录：

- HTTP 200 / new：https://www.cambricon.com/index.php?a=lists&c=index&catid=365
- HTTP 200 / new：https://www.cambricon.com/index.php?a=lists&catid=371
- HTTP 200 / new：https://www.metax-tech.com/prod.html?cid=106&id=34
- HTTP 200 / new：https://www.metax-tech.com/prod.html?cid=107&id=68
- HTTP 200 / new：https://www.mthreads.com/product/S5000
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/a100/
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/h100/
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/h200/
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/l4/
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/l40s/

失败记录：

- job 1216 / chip-catalog：候选字段 chip_model 的证据不在本轮可信正文中。
- job 1230 / chip-compute：候选字段 architecture 的证据不在本轮可信正文中。
- job 1231 / chip-catalog：芯片实体不能唯一匹配现有数据（匹配 0 条）。
- job 1234 / chip-catalog：候选字段 chip_model 缺少逐字段原文证据。
- job 1236 / chip-compute：候选字段 precision_perf 的证据不在本轮可信正文中。
- job 1238 / chip-basic：候选字段 vram_gb 的证据不在本轮可信正文中。
- job 1239 / chip-compute：候选字段 precision_perf 的证据不在本轮可信正文中。
- job 1242 / chip-compute：候选字段 precision_perf 的证据不在本轮可信正文中。
- job 1245 / chip-compute：候选字段 precision_perf 的证据不在本轮可信正文中。

### parent：20260917-030119-eb697baa

[完整任务 API](http://81.70.231.92/api/v1/data-agent/runs/20260917-030119-eb697baa/38)
服务器目录：`/home/lxc/chip-recommend/data/test_runs/20260917-030119-eb697baa/`。
模型响应 11 次，记录 token 总数 21206。

抓取记录：

- HTTP 200 / new：https://sambanova.ai
- HTTP 200 / new：https://www.cambricon.com
- HTTP 200 / new：https://www.metax-tech.com
- HTTP 200 / new：https://www.mthreads.com
- HTTP 200 / new：https://www.nvidia.com/en-us/data-center/technologies/hopper-architecture/
- HTTP 200 / new：https://sambanova.ai/products/rdu-ai-chips
- HTTP 200 / new：https://www.metax-tech.com/prod.html?cid=106&id=34
- HTTP 304 / unchanged：https://www.mthreads.com/product/S5000

失败记录：

- job 1219 / chip-catalog：芯片实体不能唯一匹配现有数据（匹配 0 条）。
- job 1221 / chip-compute：芯片实体键须有现有 id，或精确 chip_model + vendor。

### 日志文件

- `trace.jsonl`：模型调用、抓取阶段、初次结果与正式复核过程。
- `update_run_events.json`、`data_agent_jobs.json`、`source_checks.json`、`source_discovery_edges.json`：调度、任务、HTTP 检查与发现关系；这些导出包含测试库复制的历史记录，须按本次 cycle_run_id=38 及关联 source run 39/40 筛选。
- `job-<id>-prompt.json`、`job-<id>-response.json`：本次模型输入与原始响应。
- `source_snapshots/`：页面快照。
- `initial_result.json`：初次诊断结果；`result.json`：复核后最终结果。
- 服务器汇总：`/home/lxc/chip-recommend/data/diagnostics/server-batch-result.json`。

## 正式数据保护

五张正式业务表测试前后逐行 SHA-256 一致；两个测试均 publication_count=0。测试运行在克隆数据库中，不改变正式芯片、模型、评测、兼容性及字段溯源数据。
