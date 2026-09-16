# 数据更新：手动执行与测试模式

当前阶段只开放芯片来源隔离测试。到 `/status` → “数据更新” → “管理数据来源”，从父URL链接库选择一个芯片 URL 族，再手动勾选 1–9 个同族目标页并保存；随后点击“手动执行”。普通访客可查看日志；保存选择和触发运行需要独立管理员口令。

| 模式 | 种子保存位置 | 本轮写入位置 | 调度 |
|---|---|---|---|
| 正式巡检 | 不开放新增来源和手动运行 | 本地代码不写入 | 部署后主服务器正式数据 Agent 门控将读取 `paused` 配置；恢复需单独决定 |
| 芯片隔离测试 | `data/test_runs/chip_family_selection.json`，可另存手动测试来源 | 每轮新建的 `data/test_runs/<会话>/data.db` | 只访问明确选定的父页和芯片目标页；不会修改正式业务表 |

测试副本通过 SQLite 在线备份生成。选定父页在副本中标为 `listing`，其目标页标为 `detail` 并记录 `parent_link_id`；表中父页的“父URL=自身”只表示 URL 族，不会建立自环。父页先生成候选目标 URL，经去重、安全校验后进入第二轮访问；勾选的目标页也会直接访问，以免动态网页漏掉链接。模型 API 同步、模型发现、HF 链接及非芯片候选不在本轮任务中。复制来的未完成正式任务只在**副本中**标记为不可续跑；所选来源的复制哈希会在副本中清空，确保本轮提取能读取新快照。没有明确选择芯片来源时，测试拒绝启动，不会从全库随机抽取。

测试 Hermes 语义 Agent 若仍处于暂停状态，手动执行只会完成确定性访问，页面应显示“语义提取尚未执行”；不能把访问日志当成字段更新结果。启用测试 Agent 前，需要确认它只通过受限测试 Worker 使用该会话的隔离数据库，不能访问正式容器和正式数据库。正式 Hermes 数据抓取心跳当前不应启动新周期。

管理口令存于主服务器 `/home/lxc/chip-recommend/data/manual_control/admin_token`，文件权限 0600，和 SSH 密码不同。网站当前是明文 HTTP；请通过 SSH 隧道访问管理功能，避免口令在公网传输：

```powershell
ssh -i "E:\BUPT_PS\服务器\lxc.pem" -L 15340:127.0.0.1:5340 lxc@81.70.231.92
```

保持 SSH 窗口打开，浏览器访问 `http://localhost:15340/status`。登录服务器后可运行 `sudo cat /home/lxc/chip-recommend/data/manual_control/admin_token` 读取管理口令，将其输入页面弹窗。页面仅在当前标签页内存中保存口令，不写入浏览器持久存储。

手动请求的审计 JSON 在 `data/manual_control/requests/`；测试副本和日志在 `data/test_runs/`。执行器为服务器上的 `hermes-manual-run-dispatcher.service`，可用 `systemctl status hermes-manual-run-dispatcher.service` 查看状态。
