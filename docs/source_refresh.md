# 来源增量复查

`source_refresh` 用于定期检查 `link_library` 中已确认的来源是否变化。巡检本身是 check-only：只写运行状态、来源快照和差异审计记录。发现变化后，由 Hermes Agent 调用独立的自动发布器做二次核验；只有官方证据、字段格式、影子库写入和现有功能测试全部通过，才会更新芯片字段并写入字段溯源。

## 两分钟快速开始

先确认要检查的 `link_library.id`，然后预览：

```bash
python scripts/run_source_refresh.py --link-ids 20,21 --dry-run
```

预览不会发起网络请求，也不会写数据库或文件。确认选择范围后执行：

```bash
python scripts/run_source_refresh.py --link-ids 20,21 --apply-state
```

如需代理，通过参数或环境变量传入，不再硬编码本机代理：

```bash
set SOURCE_REFRESH_PROXY=http://127.0.0.1:7897
python scripts/run_source_refresh.py --link-ids 20,21 --apply-state
```

Linux/macOS 使用 `export SOURCE_REFRESH_PROXY=...`。

## 输出与退出码

命令在标准输出打印 JSON。摘要包含运行 ID、选中数量、各结果计数、耗时、逐来源状态以及 `business_tables_modified: false`。

| 退出码 | 含义 |
|---:|---|
| 0 | dry-run 或全部来源检查成功 |
| 1 | 配置、数据库或整轮执行错误 |
| 2 | 本轮完成，但有来源等待重试、不支持或失败 |

来源结果：

- `new`：首次成功获取并保存快照。
- `changed`：规范化正文哈希变化，保存新快照并生成新旧正文 diff、中文摘要和候选字段。
- `unchanged`：HTTP 304，或重新获取后正文哈希不变。
- `not_due`：尚未到 `next_check_at`，本轮未发网络请求。
- `retry_wait`：超时、连接失败、408、429 或 5xx，已安排退避。
- `failed`：普通 4xx、空正文、响应过大或快照写入失败。
- `unsupported`：第一阶段不支持的 PDF、二进制或其他内容类型。

## 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--link-ids` | 必填 | 逗号分隔的链接 ID 白名单 |
| `--limit` | 无 | 只处理白名单前 N 条 |
| `--apply-state` | 关闭 | 发请求并写控制表/快照 |
| `--force` | 关闭 | 忽略 `next_check_at` 强制复查 |
| `--db` | `data/data.db` | 数据库路径 |
| `--snapshot-dir` | `data/source_snapshots` | 内容寻址快照目录 |
| `--connect-timeout` | 5 秒 | 连接超时 |
| `--read-timeout` | 20 秒 | 读取超时 |
| `--max-bytes` | 5 MiB | 单响应大小上限 |
| `--max-attempts` | 2 | 可重试错误最大尝试次数 |
| `--max-redirects` | 5 | 重定向上限 |
| `--json-output` | 无 | 将相同 JSON 额外写入文件 |
| `--verbose` | 关闭 | 致命错误时打印堆栈 |

## 查看具体变化

每次 `changed` 结果会返回：

- `diff_summary`：新增/删除行数和少量关键变化行。
- `diff_path`：受 200 行、20,000 字符上限保护的统一 diff 文件。
- `normalized_snapshot_path`：去除脚本、导航等噪声后的本次正文。
- `candidate_fields`：根据变化行保守识别的可能相关字段，例如显存、带宽、功耗、精度性能、制程和互联。

同一信息也写入 `source_diffs`，便于按 `link_id` 和时间追溯。候选字段只是 Agent 二次核验的线索，并不直接等于已验证数据。若历史版本只有旧格式快照，工具会尝试回建规范化正文；无法可靠回建时仍记录哈希变化，但明确标注本轮没有逐行 diff。

Web 页面可通过 `/status` 的“系统状态 & 数据更新”入口查看。页面顶部始终显示最近一次执行的抓取开始时间、结束时间、状态、运行模式、成功/失败数量、变化数量和耗时；同时显示 Hermes 心跳每天北京时间 02:00 调度以及 Gateway/Skill 状态。即使没有发现正文变化，也能确认巡检已经运行。“这次检查与项目的联系”说明当前运行处于来源巡检、字段识别、Agent 自动核验、溯源写入和业务使用中的哪一步，并明确本轮是否修改业务数据。“运行轨迹”按时间展示选源、网页访问、HTTP 结果、快照、正文变化与候选字段提取、Agent 校验、影子库测试和正式发布；任务执行中页面每 15 秒轻量获取进度，展开明细时暂停自动刷新，阅读位置和展开状态不重置。“本轮来源明细”列出每个厂商来源的原始抓取链接、重定向后的最终地址、检查时间、耗时、请求次数、响应大小、分类、HTTP 状态、结果、错误或候选字段。下方变化列表显示来源、时间、变化行数、摘要、候选字段和自动写入结果；“查看新旧差异”会展开红色删除行与绿色新增行。接口分别为 `GET /api/v1/source-updates` 和 `GET /api/v1/source-updates/{id}`。

## Hermes 定时任务

数据更新链路已完整迁移到 `81.70.231.92`：Hermes Gateway、任务 Worker、`chip-recommend` 容器和持久化数据库位于同一台服务器，通过本机 Docker 固定入口通信，不依赖本地电脑或旧服务器。当前正式巡检和开发测试两个 Cron 任务均保持禁用，只允许页面手动触发隔离测试；如以后恢复正式巡检，门控仍按“北京时间 02:00 后每天最多开启一个新周期、未完成周期每 30 分钟续跑、完成后当天静默”执行。重新安装本机链路请在主服务器执行：

```bash
cd "$(cat /home/lxc/chip-recommend/current-release)"
bash scripts/install_hermes_data_heartbeat_primary.sh "$PWD"
```

现有 Hermes 任务 ID `268fc87564c3` 保持不变，但当前为禁用状态；恢复后由门控负责“02:00 开新周期、未完成半小时续跑、当天不重复建周期”，并自动恢复超时的二轮抓取。安装脚本备份旧配置，部署本机门控、Worker 和 `data-update-orchestrator` Skill。只有显式指定其他镜像目标时才使用 `install_hermes_data_heartbeat_remote.sh`，它不是主要平台的安装入口。以下独立来源心跳与直写桥接说明仅作为历史设计保留；其线上配置现已禁用，候选字段统一走 Candidate Publisher。

线上任务配置如下：

```bash
hermes cron create "0 2 * * *" \
  "根据注入的 source_refresh 上下文处理来源变化；按 aishperf-data-heartbeat Skill 自动校验并发布明确的官方字段更新，然后用中文报告。" \
  --skill aishperf-data-heartbeat \
  --script hermes_source_refresh_gate.py \
  --workdir /root/.hermes \
  --deliver local \
  --name "AISHPerf 数据更新心跳"
```

`hermes_source_refresh_gate.py` 是零模型成本的预检查：无变化时输出 `wakeAgent:false`，Hermes 不调用模型、不发送消息；只有正文发生变化、首次来源被配置为通知，或某来源连续失败达到阈值时，才唤醒 Agent。唤醒时会把本轮全部抓取链接及逐来源结果注入 Agent 上下文，并额外携带变化来源的 diff 摘要、差异路径和可能涉及的候选字段；巡检命令整体失败会以非零状态触发错误告警。

### 自动发布规则

Agent 只能提交 `source_update_id`、唯一芯片型号、候选字段和值，以及取自新增差异行的逐字段原文证据。自动发布器目前只接受显存、显存带宽、功耗、精度性能、制程、互联带宽、架构和计算单元等白名单字段，并执行以下检查：

1. 变化记录必须真实存在且状态为 `changed`，来源必须是 HTTPS 官方域名。
2. 芯片型号必须在数据库中精确且唯一匹配；字段和值必须符合白名单和格式范围。
3. 证据必须逐字出现在新增 diff 中，且能对应芯片与新值；无法唯一确认时直接拒绝，不进入人工待审队列。
4. 在临时数据库副本中调用正式数据接口写入，并验证字段溯源、数据库完整性、芯片搜索、芯片画像、系统状态、推荐候选和 JSON 输出。
5. 全部通过后先备份正式数据库，再用事务更新业务字段、写入 `field_provenance` 和 `source_auto_applies` 审计记录；同一条差异与同一芯片不能重复发布。

Hermes 侧通过以下桥接命令提交 JSON，SSH 端只允许 `source-refresh` 与 `source-auto-apply` 两个固定命令：

```bash
python3 ~/.hermes/scripts/hermes_source_auto_apply.py <<'JSON'
{"source_update_id":123,"chip_model":"示例芯片","fields":{"vram_gb":"96"},"evidence":{"vram_gb":"官方页面中的新增原文"}}
JSON
```

```bash
hermes cron run 268fc87564c3
hermes cron runs 268fc87564c3 --limit 20
hermes cron doctor
```

默认配置位于 `~/.hermes/scripts/source_refresh_gate.json`。首次基线的 `new` 默认不通知，避免安装后一次性发送大量消息；可以通过 `notify_on_new` 调整。退出码 2 代表部分来源失败，已经成功的来源不会重复执行。

同一时间只能有一个 apply-state 任务。异常退出留下的 `running` 批次默认六小时后可由下一次运行标记为 `abandoned`；确认旧进程已停止后，可以通过 `--stale-after-minutes` 调整恢复阈值。

## 上线与回滚

上线前先备份 `data/data.db`，运行完整测试，再用 2 条官方来源做小流量试跑。巡检阶段只增加控制表并写入 `data/source_snapshots`；自动发布阶段只有在全部机器校验通过后才修改 `chips` 和 `field_provenance`，并保留更新前数据库备份。来源跳转时每一级地址都会校验，拒绝跳转到 localhost、私有或保留 IP。

如需停止，先关闭 Hermes 心跳任务，再回滚代码即可。控制表和历史快照可以保留用于审计，不影响现有搜索、推荐和画像功能；不要在未备份时直接删除控制表。

## 故障排查

- `已有 source_refresh 正在运行`：确认旧进程是否仍存在；不要同时启动多个写入者。
- `HTTP 429`：工具会尊重 `Retry-After` 或按失败次数退避，无需立即循环重跑。
- `unsupported`：首期只支持静态 HTML、JSON 和纯文本；PDF、登录态和 JS 渲染页面暂不处理。
- `snapshot_write_error`：检查快照目录权限和磁盘空间；该次成功哈希不会写入状态表。
- `无法取得 SQLite 单写入锁`：等待当前数据库写入完成后重试，事务不会跨网络请求。

## 测试

```bash
pytest -q tests/test_source_refresh.py
pytest -q tests/test_source_auto_apply.py
pytest -q
```

巡检测试使用临时数据库和模拟 HTTP 响应，并校验巡检前后五张业务表内容哈希完全一致。自动发布测试覆盖官方来源限制、原文证据、精确匹配、字段溯源、备份、幂等保护，以及现有搜索、画像、状态和推荐功能的影子库冒烟测试。
