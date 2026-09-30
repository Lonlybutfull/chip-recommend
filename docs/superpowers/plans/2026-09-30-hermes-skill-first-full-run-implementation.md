# Hermes 单 Skill 全芯片任务实施计划

对应设计：`docs/superpowers/specs/2026-09-30-hermes-skill-first-full-run-design.md`

## 实施原则

- 测试先行；每个阶段先补失败测试，再完成最小实现。
- `.agents/skills` 是六个 Hermes Skill 的唯一源码，部署时同步到 Hermes。
- 一个父运行覆盖所选 Skill 的全部芯片；内部子单元只是实现细节，不要求人工分批。
- 新主链路不调用 `run_open_web_all_skills.py`，也不使用关键词粗筛或全局前 10 条规则。
- 所有真实网络结果只进入隔离测试目录，正式数据库只读并进行前后指纹核验。

## 阶段 1：扩展运行状态模型

涉及文件：

- `chip_model/pipeline/hermes_open_web_state.py`
- `chip_model/pipeline/hermes_open_web_contracts.py`
- `tests/test_hermes_open_web_state.py`
- `tests/test_hermes_open_web_contracts.py`

任务：

1. 将父运行范围建模为 `scope=single|all`，`target_chip` 改为可选。
2. 全量运行启动时从正式数据库读取、去重并冻结芯片名称快照。
3. 新增父运行统计：总数、完成数、成功/部分成功/失败数、URL 与字段统计。
4. 在父目录下创建 `units/<unit_id>/`，每个芯片和开放发现任务各有独立 manifest 和产物。
5. 为工具请求增加 `unit_id` 和 `scope_type=chip|discovery`；单芯片旧请求保持兼容。
6. 增加原子检查点和状态迁移校验，禁止已完成单元重复执行。

测试：单芯片兼容、全量快照、重复芯片去重、空数据库、非法 unit、原子恢复和父统计聚合。

## 阶段 2：把第 2～7 步写入六个 Skill

涉及文件：

- `.agents/skills/chip-identity/SKILL.md`
- `.agents/skills/chip-specs/SKILL.md`
- `.agents/skills/chip-compute/SKILL.md`
- `.agents/skills/chip-compatibility/SKILL.md`
- `.agents/skills/chip-benchmark/SKILL.md`
- `.agents/skills/chip-deployment/SKILL.md`
- `tests/test_open_web_skill_contracts.py`

每个 Skill 必须包含：

1. 单芯片与开放发现搜索词规则；
2. `open_web_search → open_web_preview → open_web_submit_selection` 的强制调用顺序；
3. 候选 URL 选择与拒绝标准；
4. `UrlDecision` JSON 示例；
5. 当前领域字段与证据要求；
6. 其他类别识别和 `suggested_skills` 规则；
7. 无结果、访问失败、模型失败、重试和完成条件；
8. 禁止 Hermes 自带搜索、编造 URL、跳过候选或直接写数据库。

测试读取六个真实 Skill，验证必需章节、工具名、JSON 字段、当前 Skill 专属字段和禁止事项，不只检查关键词是否出现。

## 阶段 3：工具服务支持父运行和子单元

涉及文件：

- `chip_model/open_web_tool_server.py`
- `chip_model/pipeline/open_web_test.py`
- `tests/test_open_web_tool_api.py`

任务：

1. 三个工具按 `run_id + unit_id` 解析工作目录。
2. 强制状态机：未搜索不能预览，未完成预览不能提交，提交必须覆盖全部候选。
3. 搜索结果同时与本轮和已有链接库去重，并保留“已存在/新 URL”标记。
4. 父任务内增加基于规范化 URL 和内容哈希的快照缓存；相同 URL 只访问一次。
5. 提交工具继续完成正文提取、证据校验、URL 资产和关联 Skill 记录。
6. 发现新芯片时写 `new_chip_candidates.jsonl`，禁止写正式 `chips` 表。
7. 每个工具完成后原子更新子单元和父任务统计。

测试覆盖正常顺序、跳步、重复提交、重复 URL 复用、发现单元、跨 Skill 建议和失败重试。

## 阶段 4：实现全芯片父编排器

涉及文件：

- `chip_model/pipeline/hermes_open_web_orchestrator.py`
- `scripts/run_hermes_open_web_test.py`
- `tests/test_hermes_open_web_orchestrator.py`

任务：

1. CLI 的 `--chip` 改为可选；省略时创建全量父任务。
2. 父编排器为全部芯片建立单元，并额外建立一个开放发现单元。
3. 每个单元启动一次 Hermes oneshot，加载同一个 Skill 和 `aishperf_open_web` toolset。
4. Hermes 启动提示词只传运行上下文，不再重复第 2～7 步；业务流程以 Skill 为唯一来源。
5. 默认有限并发，配置来自服务器环境；对外仍表现为一个运行。
6. 单元失败在本轮内自动重试；重试耗尽后继续其他单元。
7. 父任务等待全部单元终态，聚合为 success/partial/failed，并核对正式库指纹。
8. 重启时读取检查点，只继续未完成单元。

测试使用假的 Hermes runner 覆盖全部芯片、定向芯片、开放发现、并发上限、失败不中断、续跑和最终聚合。

## 阶段 5：更新 Hermes 插件协议

涉及文件：

- `.hermes-plugins/aishperf-open-web/__init__.py`
- `.hermes-plugins/aishperf-open-web/plugin.yaml`
- `tests/test_hermes_open_web_plugin.py`

任务：

1. 三个插件工具透传 `unit_id`、可选 `target_chip` 和 `scope_type`。
2. 更新参数说明，让 Hermes 从 Skill 获取步骤而不是从插件描述猜测流程。
3. 对服务端合同错误返回清晰、可恢复的错误，不泄露令牌和内部路径。

测试插件请求体、可选芯片、发现单元、错误传播和敏感信息清理。

## 阶段 6：API 与状态页展示

涉及文件：

- `chip_model/server.py`
- `chip_model/pipeline/open_web_test.py`
- `static/run-history.js`
- `static/run-history.css`
- `tests/test_api.py`

任务：

1. 运行列表返回 Skill、范围、总芯片、完成进度和父状态。
2. 详情接口按需返回芯片单元摘要，不一次传输全部网页正文。
3. 页面顶部展示六阶段流程和总体进度。
4. 支持按芯片搜索最终 URL、选择理由、字段和失败原因。
5. 单列展示新芯片候选和关联 Skill；工具调试轨迹默认收起。
6. 保持页面无公开启动按钮，任务仍从后端启动。

测试 API 分页、父子聚合、空结果、部分失败、页面文案和旧运行兼容。

## 阶段 7：文档和部署脚本

涉及文件：

- `docs/open_web_test_runbook.md`
- `scripts/install_hermes_open_web_plugin.sh`
- `scripts/deploy_open_web_tool_service.sh`
- 需要时更新 `docs/deployment.md`

任务：

1. 文档改为“Skill 必选、芯片可选”。
2. 写明一次全量任务的含义、产物、恢复方式、停止方式和资源影响。
3. 部署脚本把六个 Skill 从 `.agents/skills` 同步到 Hermes 私有目录。
4. 部署前备份当前插件、Skill、容器和配置，保留可回滚版本。

## 阶段 8：验证与正式部署

1. 运行协议、状态、工具、插件和编排器定向测试。
2. 运行完整 `python -m pytest -q`。
3. 本地使用假的搜索和模型执行包含多个芯片的完整父任务，验证没有全局前 10 截断。
4. 部署到 `81.70.231.92` 的新 release 和新镜像，保留旧容器。
5. 同步六个 Skill 和 Hermes 插件，重启工具服务及主站。
6. 先执行“两个芯片 + 一个 Skill”的真实隔离冒烟测试，核对搜索、预览、选择、字段和关联任务。
7. 验证站点健康、页面展示、正式数据库哈希和现有推荐功能。
8. 冒烟通过后再启动一个 Skill 的全部芯片任务；记录 session ID，不恢复自动心跳。

## 回滚条件

出现以下任一情况立即回滚服务，但保留隔离运行产物用于诊断：

- 正式数据库指纹发生变化；
- 现有推荐、搜索或画像接口回归失败；
- Hermes 可以跳过预览直接提交；
- 父任务漏掉芯片快照中的任何芯片且没有失败记录；
- 页面或 API 无法区分已有 URL、新 URL 和新芯片候选。
