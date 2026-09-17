# 本地数据爬取智能体测试（2026-09-17）

代码版本：a7837f9；环境：Windows、Python 3.13.13。

结论：两个真实网页的确定性抓取成功；语义提取未完成。提供的 API 凭证被网关拒绝，且仓库的本地 Hermes Worker 入口缺少配置，并非可直接接入任意聊天 API 的独立执行器。本次没有修改业务代码、发布数据或启动正式巡检。

## 实测结果

| 环节 | 结果 |
| --- | --- |
| API 非流式请求 | HTTP 401，authentication_error / api_key_not_found |
| API 按用户示例流式请求 | HTTP 401，同样提示“ApiKey不正确，请使用正确的ApiKey” |
| NVIDIA H100 产品页 | HTTP 200，原始快照 287758 字节 |
| AWS Trainium 产品页 | HTTP 200，重定向至中文产品页，原始快照 429937 字节 |
| 抓取任务 | 2 个 succeeded，0 个 failed |
| 语义任务 | 6 个 awaiting_agent：每个页面分配 chip-catalog、chip-basic、chip-compute |
| 提取候选 / 发布 | 0；未完成真实模型提取和发布验证 |
| 相关自动测试 | 23 passed，耗时 7.82 秒 |

API 地址为 `https://ai.ctaigw.cn/v1/chat/completions`，模型为 `glm-5.3`。Bearer 后的字符串作为密钥使用，没有将密钥写入项目文件。两种请求均到达网关并收到鉴权错误，不能据此判断模型是否可用。需要该网关认可的有效凭证才能继续模型阶段。

## 实际运行流程

1. `scripts/run_data_agent.py test` 调用 `start_test_cycle`，要求明确选择芯片 URL 族或配置芯片测试种子；没有种子会抛出 ValueError。这是有意的范围限制。
2. `create_test_workspace` 使用 SQLite 在线备份创建隔离数据库，将复制来的未完成任务置为不可续跑，在副本中配置种子并清空选定来源的历史抓取基线。
3. `DataAgentOrchestrator.run_cycle(mode="test")` 规划来源任务，调用 SourceRefresher 抓取，保存快照、内容哈希、访问记录和事件。
4. 对可提取页面按领域生成语义任务，周期转入 awaiting_agent。当前测试模式限制为芯片清单、基础规格、算力提取，不进行模型同步。
5. 外部语义 Agent 经 Worker 领取任务、读取受限快照，完成语义分析后调用 finish 返回候选事实。Worker 脚本本身只桥接任务领取、快照读取、完成和发布，不负责调用聊天模型。
6. finish 阶段校验任务归属、实体键、字段值和证据等，再进入候选队列；Publisher 将合格候选写入业务表并记录字段来源。该后半段本轮没有通过真实模型跑通。

列表页另有 URL 发现和第二轮目标访问分支。本轮明确选择两个详情页，未覆盖该分支，不能将本次结果称为全链路成功。

## 复现的阻塞点

- 未配置来源时执行 `python scripts/run_data_agent.py test --limit 2 --max-attempts 1`，提示必须先选择芯片来源。本轮通过隔离基线数据库专用种子配置解决，未改动日常测试来源配置。
- 提供的 API 凭证在流式和非流式请求中均被拒绝，错误码 api_key_not_found。此问题发生于模型执行前，调整提取提示词或解析代码无法解决。
- 执行 `python scripts/hermes_data_agent_worker_local.py claim`，出现 FileNotFoundError：缺少 `scripts/local_docker_worker.json`。
- 即使补配置，当前 Worker 的 local_docker 分支仍使用 `/usr/bin/docker`、固定容器 `chip-recommend` 和 `/app/data/test_runs/.../data.db` 路径。这是 Linux Docker 部署约定，不能直接当作 Windows 原生 Worker 使用；另一分支使用受限 SSH。
- 因此，仅提供 API key 不会使队列自动消费。后续需要有效凭证及实际语义 Agent 运行时，或单独实现连接此 API 的 Windows 本地适配器（领取任务 → 读取快照 → 模型 JSON 输出 → finish）。

## 测试产物与验证

所有运行产物位于 Git 忽略目录 `data/test_runs/local-api-smoke-20260917/`：

- `baseline.db`：正式库的只读备份，用于建立测试基线。
- `run.json`：隔离运行路径、周期 ID、计数和状态。
- `source_checks.json`、`data_agent_jobs.json`：抓取记录和任务明细。
- `test_runs/20260917-020234-87ebb57e/data.db`：本轮隔离库。
- 同一会话下 `source_snapshots/`：两个网页的原始快照。

正式数据库与基线副本的 SQLite iterdump SHA-256 相同，验证逻辑内容未变化。在线备份可能改变数据库文件头，不能用文件字节哈希直接判断逻辑数据变化。

执行的自动测试：

```powershell
python -m pytest -q tests/test_data_agent.py tests/test_manual_data_agent.py tests/test_hermes_test_runtime.py tests/test_candidate_publisher.py
```

用户授权后执行了 `codegraph init -i`，索引 88 个文件。`.codegraph/` 为新增本地索引目录。

## Moonshot 重跑与本地网页（后续测试）

更换为 `https://api.moonshot.cn/v1/chat/completions`、`kimi-k2.6` 后，鉴权成功，最小请求 HTTP 200。密钥通过测试进程标准输入传入，没有写入项目文件。

使用一次性诊断脚本 `data/test_runs/local-api-smoke-20260917/moonshot_smoke.py` 连接仓库的 claim/finish 接口。该脚本只允许标记过的隔离数据库，读取已有领域 Skill 约束，调用模型后将 JSON 提交给原有 finish 校验；不是常驻 Hermes 服务，也未接入网页“手动执行”调度。

第一次提取使用默认思考模式，输出额度 5000 tokens，其中 4999 为 reasoning tokens，finish_reason=length，content 为空，造成 JSON 解析失败。停止这轮诊断并终止旧周期后，按[官方参数说明](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart)设置 `thinking: {"type": "disabled"}`，新建隔离会话、重新抓取两个页面并执行六个提取任务。

最终结果：两个网页抓取成功；六个语义任务中两个成功、四个失败，共产生六个候选，零发布。H100 的目录、基础规格、算力任务以及 Trainium 算力任务均被原文证据校验拒绝。模型使用省略号拼接片段、改写文本或拼接表格，违反连续原文匹配要求；代码正确拒绝了这些结果。

Trainium 目录与基础规格任务通过机械校验，但不代表事实都可靠。例如模型用一般“芯片”描述推断 ASIC，又把“即将推出”映射为“未公开发布”。当前 substring + 字段枚举检查不能保证证据语义蕴含字段结论，仍需语义复核及芯片变体对应检查。因此没有执行 Publisher，没有写正式业务数据；这次是模型提取及候选校验测试，不是全链路发布成功。

运行明细：同一产物目录下 `moonshot-run.json`、`moonshot-results.json`、`moonshot-job-*.json`。前两者记录新会话路径和任务最终结果，后者保存模型响应（不包含密钥）。

### 网页运行

前端是由 FastAPI 托管的 SPA，无需单独启动前端开发服务器。服务监听 `127.0.0.1:8000`，本轮启动 PID 32904。

- 首页：http://127.0.0.1:8000/
- 芯片搜索：http://127.0.0.1:8000/chips
- 状态及抓取记录：http://127.0.0.1:8000/status
- API 文档：http://127.0.0.1:8000/docs

初次启动报 `sqlite3.OperationalError: attempt to write a readonly database`。检查确认 `data/data.db` 带 Windows ReadOnly 属性，而 FastAPI lifespan 会执行 ensure_schema。通过 `DATA_DB_PATH` 指向可写的 `data/test_runs/local-api-smoke-20260917/baseline.db` 启动成功，未解除原库只读属性。

重启命令（项目根目录 PowerShell）：

```powershell
$env:HOST = '127.0.0.1'
$env:PORT = '8000'
$env:DATA_DB_PATH = (Resolve-Path 'data/test_runs/local-api-smoke-20260917/baseline.db').Path
python scripts/run_server.py
```

已验证首页、状态页、健康检查、芯片与模型查询接口均 HTTP 200；Playwright 打开首页和状态页，未出现页面 JavaScript 错误；点击芯片搜索可看到 1093 条芯片的分页列表。截图为产物目录中的 `home.png`、`status.png`（截图时提取任务尚在运行）。网页智能助手的独立 DeepSeek 配置和 Hermes 手动调度没有改为 Moonshot，本次只验证网页查询与数据更新记录展示。
