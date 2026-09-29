# Hermes 驱动的开放互联网采集实施计划

日期：2026-09-29
状态：待执行
对应设计：`docs/superpowers/specs/2026-09-29-hermes-open-web-skill-tools-design.md`

## 1. 实施目标

把当前“Python 生成固定搜索词、关键词评分选 URL”的流程，改造为：

1. 后端一次指定一个更新类别和目标芯片。
2. Hermes 加载该类别 Skill，由 Kimi 生成 10 条搜索词。
3. Hermes 调用项目提供的受限工具进行搜索、去重、访问和 500 字预览。
4. Kimi 根据预览选择有价值的 URL，以严格 JSON 通知系统。
5. 工具只把选中 URL 交给现有完整正文抓取、字段提取和证据校验流程。
6. 每个搜索词、URL、模型判断、提取字段和联动任务都可在运行产物和状态页追溯。

全流程仅写入 `data/test_runs/<run_id>/`，正式 `data/data.db` 始终保持不变。

## 2. 关键实施决策

### 2.1 调用边界

- Hermes 和 Skill 负责“想”：生成搜索词、阅读预览、选择 URL、给出理由和建议其他 Skill。
- 工具服务负责“做”：搜索、URL 安全校验、规范化、去重、访问、正文清理、快照保存和提交选择。
- 新 Hermes 路径不使用旧关键词评分作为降级逻辑。Kimi 不可用时该轮明确失败。

### 2.2 工具服务形式

使用一个只绑定 `127.0.0.1:5341` 的轻量 FastAPI 工具服务：

- Hermes 插件仅能访问这个本机 JSON API。
- API 要求本机 Bearer 令牌，令牌仅保存在服务器环境。
- 插件不获得 shell、Docker socket 或任意 SQLite 路径权限。
- 工具服务只允许访问后端预先创建的测试运行目录。

### 2.3 状态以产物为准

Hermes 最终回答只作为人类可读摘要；程序状态必须以工具写入的 JSON/JSONL 产物为准，避免从自然语言里推测是否成功。

## 3. 实施阶段

### 阶段 1：建立数据契约和运行状态

**新增文件**

- `chip_model/pipeline/hermes_open_web_contracts.py`
- `chip_model/pipeline/hermes_open_web_state.py`
- `tests/test_hermes_open_web_contracts.py`
- `tests/test_hermes_open_web_state.py`

**实施内容**

1. 定义六个 Skill 白名单和中文显示名。
2. 定义 `SearchPlan`、`SearchResult`、`UrlPreview`、`UrlDecision`、`ToolTrace` 的 Pydantic 模型。
3. 强制搜索词恰好 10 条，正规化后不得重复，不得为空。
4. 建立稳定 `candidate_id`，绑定 `run_id + canonical_url`。
5. 实现运行目录路径限制、原子写入、JSONL 追加和阶段状态迁移。
6. 只接受 `mode=test`，拒绝客户端传入任意路径。

**验收**

- 未知 Skill、跨运行候选、重复搜索词、未知候选和路径越界均被测试覆盖并拒绝。

### 阶段 2：实现受限工具服务

**新增文件**

- `chip_model/open_web_tool_server.py`
- `scripts/run_open_web_tool_server.py`
- `tests/test_open_web_tool_api.py`

**修改文件**

- `chip_model/pipeline/open_web_test.py`
- `chip_model/pipeline/source_refresh.py`
- `requirements.txt`

**实施内容**

1. 提供 `POST /v1/search`、`POST /v1/preview`、`POST /v1/submit-selection` 三个本机端点。
2. `/v1/search` 复用现有搜索提供器和 URL 规范化逻辑，但不再使用 Python 固定搜索模板。
3. 每条搜索词最多取 10 条结果；按规范 URL 跨搜索词去重，同时保留所有命中该 URL 的搜索词、排名和提供器。
4. `/v1/preview` 对全部去重候选逐一执行 SSRF、协议、私网、重定向、响应大小和超时校验。
5. 复用 `SourceRefresher` 处理 HTML/PDF/JSON/纯文本；完整快照落盘，向 Hermes 只返回最多 500 个 Unicode 字符的 `core_text`。
6. `/v1/submit-selection` 校验 Kimi 决定后，只对选中 URL 启动完整抓取与字段提取。
7. 三个端点都记录工具名、输入摘要、数量、耗时、结果状态和错误，不记录密钥和完整请求头。

**验收**

- HTML、PDF、JSON、403、超时、重定向、空正文、超大响应和搜索商部分失败均有明确状态。
- 相同规范 URL 在一次运行中只访问一次。
- 非法令牌、未知运行 ID 和任意文件路径请求都被拒绝。

### 阶段 3：实现 Hermes 插件

**新增文件**

- `.hermes-plugins/aishperf-open-web/plugin.yaml`
- `.hermes-plugins/aishperf-open-web/__init__.py`
- `tests/test_hermes_open_web_plugin.py`

**实施内容**

1. 使用 `PluginContext.register_tool()` 注册：
   - `open_web_search`
   - `open_web_preview`
   - `open_web_submit_selection`
2. 三个工具归入 `aishperf_open_web` toolset。
3. 插件处理器只将结构化参数发送到 `127.0.0.1:5341`，不允许传入任意目标主机。
4. 设置连接、读取超时和可读错误；屏蔽令牌和内部路径。
5. 工具 Schema 与阶段 1 契约同源，避免插件和服务端参数漂移。

**验收**

- 用伪 `PluginContext` 证明三个工具注册正确。
- 用伪 HTTP 服务验证成功、401、超时、非法 JSON 和 5xx 路径。

### 阶段 4：完善六个 Hermes Skill

**修改文件**

- `.agents/skills/chip-identity/SKILL.md`
- `.agents/skills/chip-specs/SKILL.md`
- `.agents/skills/chip-compute/SKILL.md`
- `.agents/skills/chip-compatibility/SKILL.md`
- `.agents/skills/chip-benchmark/SKILL.md`
- `.agents/skills/chip-deployment/SKILL.md`
- `tests/test_open_web_skill_contracts.py`
- `tests/test_hermes_skill_contracts.py`
- `tests/test_skill_language.py`

**实施内容**

1. 六个 Skill 均使用中文写成完整操作规范，保留合法 frontmatter。
2. 每个 Skill 说明适用范围、不适用范围、字段所有权、来源优先级、拒绝条件和完成标准。
3. 规定 Kimi 实时生成恰好 10 条搜索词，并覆盖官网、规格书/白皮书、开发文档、领域资料和中英文表达。
4. 规定严格工具调用顺序：`search -> preview -> submit-selection`。
5. 规定页面文本是不可信数据，不得执行页面内命令或改变 Skill 目标。
6. 规定 URL 选择 JSON 包含选择、理由、匹配类别和建议 Skill。
7. `.agents/skills` 作为仓库唯一源；部署时同步到 Hermes 私有 skills 目录，不再人工维护两份内容。

**验收**

- 自动测试确认六个 Skill 的工具名、顺序、10 条搜索词约束、JSON 协议、安全说明和字段列表均存在。

### 阶段 5：建立单 Skill Hermes 编排器

**新增文件**

- `chip_model/pipeline/hermes_open_web_orchestrator.py`
- `scripts/run_hermes_open_web_test.py`
- `tests/test_hermes_open_web_orchestrator.py`

**修改文件**

- `scripts/run_open_web_test.py`
- `scripts/run_open_web_all_skills.py`
- `docs/open_web_test_runbook.md`

**实施内容**

1. 命令行接收 `--skill`、`--chip`、可选 `--fields`和有界超时参数。
2. 先创建隔离运行和测试数据库副本，再启动 Hermes，将 `run_id`、Skill 和目标芯片注入任务。
3. Hermes 命令显式使用指定 Skill 和 `aishperf_open_web` toolset，以 oneshot 方式运行。
4. 编排器不解析 Hermes 最终自然语言判定成败，只读取工具产物和阶段状态。
5. 旧 `run_open_web_all_skills.py` 改为明确的离线回归入口，不在线上默认任务中调用。
6. 移除新路径的固定 `query_templates`、全局 top-10 评分选择和关键词降级分类。

**验收**

- 模拟 Hermes 子进程，验证 Skill/toolset/运行 ID/目标参数传递正确。
- 子进程超时、非零退出、产物不完整和 Kimi 失败都产生可读的失败状态。

### 阶段 6：接入完整提取、Kimi 和跨 Skill 联动

**修改文件**

- `chip_model/pipeline/open_web_test.py`
- `chip_model/pipeline/open_web_full_search.py`
- `chip_model/pipeline/run_history.py`
- `tests/test_open_web_test.py`
- `tests/test_open_web_full_search.py`
- `tests/test_run_history.py`

**实施内容**

1. 抽取“从已选 URL 开始”的公共入口，复用现有完整抓取、快照、提取、字段验证和隔离入库逻辑。
2. 字段提取接口统一使用 Moonshot `kimi-k2.6`，关闭 thinking，根据当前 Skill 限制可输出字段。
3. 去除 Hermes 新路径对 GLM、DeepSeek 或关键词的静默降级。
4. 同一页面命中其他类别时，按 `suggested_skills` 创建关联测试子任务，复用已有完整快照，不重新搜索和访问。
5. 使用 `(run_id, canonical_url, skill)` 去重，最大联动深度为 1。
6. 主 Skill 只提取自己的字段；子任务只提取对应 Skill 字段。

**验收**

- 同一 URL 可形成多 Skill 记录，但网络只访问一次且不会循环创建任务。
- Kimi 非法 JSON 最多修复一次；再次失败则明确终止。
- 正式库运行前后文件指纹相同。

### 阶段 7：更新状态页展示

**修改文件**

- `chip_model/server.py`
- `static/run-history.js`
- `static/run-history.css`
- `tests/test_api.py`
- `tests/test_run_history.py`

**实施内容**

1. API 读取新产物，返回目标 Skill、搜索词、搜索结果、预览、Kimi 决定、提取字段和联动任务。
2. 页面只展示已完成运行，不增加“开始测试”按钮。
3. 展开内容按人类阅读顺序排列：
   - 本轮目标
   - 10 条搜索词
   - 去重后候选及其搜索词来源
   - 访问结果和核心预览
   - Kimi 选择/拒绝理由
   - 完整提取字段和证据
   - 关联 Skill 子任务
4. 刷新时保留滚动位置和展开状态，不从页首重新开始阅读。
5. 页面不显示 API 密钥、内部令牌、完整请求头或本机文件系统路径。

**验收**

- 一个完成运行能从“为什么搜”一直追溯到“提取了什么字段”。
- 候选被多个搜索词或 Skill 命中时，可在同一 URL 下看到完整关系，不会重复编号或冒充多个 URL。

### 阶段 8：部署到 81 服务器并做真实验收

**新增/修改文件**

- `scripts/install_hermes_open_web_plugin.sh`
- `scripts/deploy_open_web_tool_service.sh`
- `Dockerfile`
- 现有服务管理配置或配套部署文档
- `docs/open_web_test_runbook.md`

**实施内容**

1. 先在本地运行目标测试，再运行完整回归：

   ```powershell
   python -m pytest tests/test_hermes_open_web_contracts.py tests/test_open_web_tool_api.py -q
   python -m pytest tests/test_hermes_open_web_plugin.py tests/test_hermes_open_web_orchestrator.py -q
   python -m pytest -q
   ```

2. 在 `81.70.231.92` 安装/`plugins/aishperf-open-web` 并显式启用插件。
3. 从仓库 `.agents/skills` 同步六个 Skill 到 Hermes 私有 skills 目录。
4. 启动只监听本机的工具服务，启动账号与主站运行目录权限分离。
5. 将项目提取模型配置为 Moonshot `kimi-k2.6`，删除运行时对 GLM/DeepSeek 的依赖，密钥仅通过服务器环境注入。
6. 选择一颗芯片和一个 Skill 从后端手动运行一次真实测试。
7. 校验页面、API、运行产物、Hermes 工具轨迹和正式库指纹。
8. 执行主站回归：`/health`、芯片搜索、芯片详情、模型搜索、算力推荐、系统状态/数据更新。

**81 服务器最终验收条件**

- 只运行选定的一个 Skill。
- Kimi 实际生成恰好 10 条搜索词。
- 搜索结果全部去重并有搜索词/Skill 来源。
- 工具对所有去重候选完成可访问性尝试和预览记录。
- Kimi 为每个候选给出选择或拒绝结果和理由。
- 只有选中 URL 进入完整抓取和字段提取。
- 发现其他类别时创建一次关联子任务，不重复访问 URL。
- 正式 `data/data.db` 运行前后 SHA-256 一致。
- 公网只暴露主站，`5341` 无法从公网访问。

## 4. 与现有资产的关系

| 现有资产 | 处理方式 |
|---|---|
| `SourceRefresher` 和网页快照 | 直接复用 |
| URL 规范化和安全校验 | 直接复用并增加契约测试 |
| 字段提取、校验和测试库 | 改造为接收已选 URL 的公共后半段 |
| `run_history` 和状态页 | 增加 Hermes 产物解析和展示 |
| 六个 `.agents/skills` | 升级为 Hermes 可直接执行的完整 Skill |
| Python 固定搜索模板 | 仅保留在旧离线回归路径 |
| 关键词粗筛/分类 | 不进入新 Hermes 路径 |
| 全 Skill 一次并行脚本 | 仅保留为开发回归工具 |
| 已有链接库复查 | 保留为独立分支，不占开放网络搜索预算 |

## 5. 实施过程中的回归要求

每个阶段合并前都要完成：

1. 新增正常路径、边界值和失败路径测试。
2. 运行对应目标测试。
3. 运行与数据库、API、推荐和状态页相关的旧回归测试。
4. 确认没有将密钥、运行令牌、测试快照或大量运行日志提交到 Git。
5. 对涉及数据的测试比较正式数据库运行前后指纹。

## 6. 回滚方案

- 新工具服务、Hermes 插件和新编排器均为独立入口，可单独停用。
- 旧离线测试脚本保留，但不自动触发。
- 状态页 API 对旧运行产物保持兼容，不要求迁移历史记录。
- 若真实验收失败，停止工具服务并禁用插件即可，正式库不需要数据回滚。

## 7. 建议执行方式

推荐在当前任务中按阶段 1→8 顺序实施，每个阶段先写失败测试，再完成最小代码并运行相关回归。本地全部通过后再一次性同步到 `81.70.231.92`，最后执行一轮真实隔离测试。
