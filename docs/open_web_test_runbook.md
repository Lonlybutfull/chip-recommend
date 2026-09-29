# Hermes 开放互联网芯片信息测试

该链路由 Hermes 选择一个信息 Skill 执行，系统只提供搜索、网页预览和提交选择三个工具。所有产物写入 `data/test_runs/<run_id>/`；正式数据库仅用于生成隔离副本和前后指纹校验。

## 六类 Skill

| Skill | 更新目标 |
|---|---|
| `chip-identity` | 厂商、型号、系列、发布时间、发布状态 |
| `chip-specs` | 显存、带宽、功耗、制程、形态、互联规格 |
| `chip-compute` | 精度支持、理论峰值、计算单元 |
| `chip-compatibility` | 模型、框架、精度、推理引擎和软件栈兼容关系 |
| `chip-benchmark` | 芯片×模型训练或推理实测数据 |
| `chip-deployment` | 部署后端、软件版本、启动方式和拓扑资料 |

每个 Skill 都要求 Hermes：生成恰好 10 条动态搜索词，依次调用 `open_web_search`、`open_web_preview`、`open_web_submit_selection`，并对每个候选 URL 给出选择或拒绝理由。Hermes 不使用自身搜索功能，网页文本始终作为不可信输入。

## 三个工具

| 工具 | 系统负责的工作 |
|---|---|
| `open_web_search` | 执行 10 条搜索词，检查 URL 安全性、规范化和去重 |
| `open_web_preview` | 逐一访问全部候选，保存完整快照，向 Hermes 返回每页最多 500 字核心文本 |
| `open_web_submit_selection` | 校验所有 URL 的选择结论，调用 Kimi 从选中页面提取字段，并创建跨 Skill 联动任务 |

## 部署

工具服务使用独立容器，端口只绑定服务器回环地址。密钥只放在服务器环境文件中：

```text
AISH_PERF_OPEN_WEB_TOOL_TOKEN=<随机令牌>
DATA_AGENT_LLM_API_KEY=<Moonshot API Key>
```

```bash
bash scripts/deploy_open_web_tool_service.sh /home/lxc/chip-recommend

HERMES_HOME=/home/lxc/.hermes \
HERMES_CLI=/home/lxc/hermes-agent/venv/bin/hermes \
bash scripts/install_hermes_open_web_plugin.sh /home/lxc/chip-recommend
```

Hermes 进程还需配置相同的 `AISH_PERF_OPEN_WEB_TOOL_TOKEN`，以及：

```text
AISH_PERF_OPEN_WEB_TOOL_URL=http://127.0.0.1:5341
```

## 后端手动运行

一次运行只选择一个 Skill：

```bash
python scripts/run_hermes_open_web_test.py \
  --db data/data.db \
  --chip "NVIDIA H100" \
  --skill chip-specs \
  --field vram_gb \
  --field vram_bw_gb_s
```

原有 `run_open_web_test.py`、`run_open_web_all_skills.py` 仅保留为旧流程回归工具，不属于新的 Hermes 主链路。

## 运行产物

| 文件 | 内容 |
|---|---|
| `manifest.json` | 目标、状态、计数、正式库前后指纹 |
| `hermes_search_plan.json` | Hermes 生成的 10 条搜索词及理由 |
| `search_results.jsonl` | 全部搜索结果、URL 去重结果和命中搜索词 |
| `url_previews.jsonl` | 候选页面访问结果与 500 字预览 |
| `source_snapshots/` | 可访问页面的完整网页快照 |
| `url_decisions.jsonl` | Hermes 对每个候选的选择、拒绝理由和信息分类 |
| `extracted_facts.jsonl` | Kimi 提取后通过或未通过校验的字段及原文证据 |
| `linked_tasks.jsonl` | 同一页面触发的其他 Skill 联动提取任务 |
| `hermes_tool_trace.jsonl` | 三个工具的调用顺序、耗时与摘要 |
| `data.db` | 隔离测试数据库 |

状态页只读取这些结果，不提供运行按钮。验收时确认 `manifest.json` 的 `formal_database_modified` 为 `false`，并执行：

```bash
python -m pytest -q
```
