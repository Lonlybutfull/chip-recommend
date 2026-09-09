# 来源增量复查

`source_refresh` 用于定期检查 `link_library` 中人工确认的来源是否变化。它是 check-only 工具：允许写运行状态、来源快照和差异审计记录，但不会修改芯片、模型、实测、兼容性或字段溯源数据。

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

同一信息也写入 `source_diffs`，便于按 `link_id` 和时间追溯。候选字段只是人工核对线索，不是已经验证的数据，也不会写入五张业务表。若历史版本只有旧格式快照，工具会尝试回建规范化正文；无法可靠回建时仍记录哈希变化，但明确标注本轮没有逐行 diff。

Web 页面可通过 `/status` 的“系统状态 & 数据更新”入口查看。页面顶部始终显示最近一次执行的抓取开始时间、结束时间、状态、运行模式、成功/失败数量、变化数量和耗时；同时显示 Hermes 心跳每天北京时间 02:00 调度以及 Gateway/Skill 状态。即使没有发现正文变化，也能确认巡检已经运行。“这次检查与项目的联系”说明当前运行处于来源巡检、字段识别、人工审核、溯源写入和业务使用中的哪一步，并明确本轮是否修改业务数据。“本轮来源明细”列出每个厂商来源的检查时间、耗时、分类、HTTP 状态、结果、错误或候选字段。下方变化列表显示来源、时间、变化行数、摘要和候选字段；“查看新旧差异”会展开红色删除行与绿色新增行。接口分别为 `GET /api/v1/source-updates` 和 `GET /api/v1/source-updates/{id}`。

## Hermes 定时任务

长期独立巡检由 Hermes Gateway 的定时心跳驱动，不依赖本地电脑或当前会话。Hermes 服务器通过受限 SSH 强制命令调用应用服务器的巡检入口；该密钥不能取得交互式终端，也不能执行其他命令。安装脚本会复制 `aishperf-data-heartbeat` Skill、预检查脚本并生成远程调用配置：

```bash
cd /root/aishperf-heartbeat-20260909-activation
bash scripts/install_hermes_data_heartbeat_remote.sh "$PWD"
```

线上任务配置如下：

```bash
hermes cron create "0 2 * * *" \
  "根据预检查脚本注入的 source_refresh 上下文生成中文巡检通知；必须包含开始、结束、耗时、数量、来源明细和业务影响；禁止修改业务数据。" \
  --skill aishperf-data-heartbeat \
  --script hermes_source_refresh_gate.py \
  --workdir /root/.hermes \
  --deliver local \
  --name "AISHPerf 数据更新心跳"
```

`hermes_source_refresh_gate.py` 是零模型成本的预检查：无变化时输出 `wakeAgent:false`，Hermes 不调用模型、不发送消息；只有正文发生变化、首次来源被配置为通知，或某来源连续失败达到阈值时，才唤醒 Agent。变化通知会携带 diff 摘要、差异路径和可能涉及的候选字段；巡检命令整体失败会以非零状态触发错误告警。

```bash
hermes cron run 268fc87564c3
hermes cron runs 268fc87564c3 --limit 20
hermes cron doctor
```

默认配置位于 `~/.hermes/scripts/source_refresh_gate.json`。首次基线的 `new` 默认不通知，避免安装后一次性发送大量消息；可以通过 `notify_on_new` 调整。退出码 2 代表部分来源失败，已经成功的来源不会重复执行。

同一时间只能有一个 apply-state 任务。异常退出留下的 `running` 批次默认六小时后可由下一次运行标记为 `abandoned`；确认旧进程已停止后，可以通过 `--stale-after-minutes` 调整恢复阈值。

## 上线与回滚

上线前先备份 `data/data.db`，运行完整测试，再用 2 条官方来源做小流量试跑。该工具只增加控制表并写入 `data/source_snapshots`，不修改五张业务表；来源跳转时每一级地址都会校验，拒绝跳转到 localhost、私有或保留 IP。

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
pytest -q
```

测试使用临时数据库和模拟 HTTP 响应，并校验五张业务表及 `field_provenance` 在运行前后内容哈希完全一致。
