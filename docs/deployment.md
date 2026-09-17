# 当前线上部署

更新时间：2026-09-17

2026-09-17 已部署独立运行卡片、运行历史和字段对比，详见 `docs/deploy_run_cards_20260917.md`。用户确认使用 HTTP。

## 访问入口

- 主要平台首页：http://81.70.231.92/
- 算力推荐：http://81.70.231.92/recommend
- 芯片搜索：http://81.70.231.92/chips
- 模型搜索：http://81.70.231.92/models
- 系统状态：http://81.70.231.92/status
- 健康检查：http://81.70.231.92/api/v1/health

主要平台通过现有 Nginx 的 80 端口转发到本机容器；Hermes WebUI 在服务器内部监听 `8787`，但公网安全组尚未开放该端口。

## 81.70.231.92 主服务器

- SSH：`lxc@81.70.231.92`
- 项目根目录：`/home/lxc/chip-recommend`
- 当前发布目录：`/home/lxc/chip-recommend/releases/20260917-025403`
- 持久化数据库：`/home/lxc/chip-recommend/data/data.db`
- Docker 容器：`chip-recommend`
- Docker 镜像：`chip-recommend:run-cards-20260917-025403`
- 容器端口：`0.0.0.0:5340 -> 8000/tcp`
- 重启策略：`unless-stopped`
- Hermes：`hermes-gateway`、`hermes-webui`、`hermes-manual-run-dispatcher` 均在本机运行并开机自启

2026-09-15 已录入评测表中可核对模型、芯片、物理卡数与 1024 输入/1024 输出/并发 1 的三条 DeepSeek-V4-Flash 推理记录：沐曦 C550、昆仑芯 P800 (OAM)、海光 BW1000。每条记录写入字段级来源，并保留工作表、单元格、量化精度和部署方式；未能核实映射的其他表格行暂不导入。Flash 推理实测项权重为 60%，已实测芯片优先；其他模型继续使用常规 40% 生态、30% 实测验证、20% 算力、10% 性价比权重。17:32 先发布到镜像服务器；17:59 又同步到主要服务器 `61.172.167.201`，两台服务器公网接口均验证三条实测生效、其他模型维持常规排序。

## 自动数据自检

- 当前模式：正式巡检与开发测试 Cron 均禁用，只允许页面手动触发隔离测试
- 恢复正式巡检后的计划：北京时间 `02:00` 开启当天首轮；未完成周期每 30 分钟继续检查并续跑，完成后当天静默
- 调度方：`81.70.231.92` 上的 Hermes Gateway（开机自启）
- Hermes 任务：`AISHPerf 数据抓取智能体`（ID `268fc87564c3`）
- 调用方式：Hermes 预检查与 Worker 在 `81.70.231.92` 本机通过 `docker exec chip-recommend` 调用固定数据智能体入口，不依赖旧服务器
- 重叠保护：未完成周期优先续跑；北京时间 02:00 前及当天已开过周期时不新建周期；上次中断超过 30 分钟的第二轮抓取会重新排队
- 运行记录：主要服务器持久化数据库的 `update_runs`、`update_run_events` 和任务表
- 自动发布备份：主要服务器容器持久化目录 `data/backups`
- 页面入口：http://81.70.231.92/status

无变化时门控保持安静；发现页面变化或来源连续失败达到阈值时才唤醒 Agent。对于能够从官方新增原文中唯一确认的芯片字段变化，Agent 会调用受限发布器，在影子库完成溯源和现有功能测试、备份正式库后自动更新；证据不足、型号不唯一或字段不在白名单时直接拒绝。执行时间、抓取链接、新旧值、备份及业务影响可在“系统状态 & 数据更新”页面和 Hermes 运行记录中查看。

### 完整数据抓取智能体

完整调度器和以下受限入口已经部署；当前 Hermes 使用主服务器本机 Docker 桥接，远程入口只作为明确配置的镜像或回滚工具：

- `run_data_agent.sh`：执行一次确定性周期；
- `run_data_agent_claim.sh`：领取一批语义任务；
- `run_data_agent_snapshot.sh`：分页读取受限目录内的证据快照；
- `run_data_agent_finish.sh`：通过标准输入返回 Agent 结果；
- `run_candidate_publish.sh`：通过标准输入发布已经校验的候选事实。

远程入口由 `run_hermes_remote_command.sh` 的 ForceCommand 白名单限制；本机桥接只允许 `chip-recommend` 容器和固定脚本。种子页/列表页先提取实际链接，`url-discovery` 只从该清单中筛选目标 URL；服务端完成规范化、去重、安全校验后，在同一周期执行详情页第二轮访问，再生成领域提取任务。状态页会展示父 URL、目标 URL、深度和二轮抓取结果。

语义任务每批最多 5 条；重复 claim 会幂等返回原批次，快照每次最多读取 64KB。正式巡检恢复后，未完成周期会在下一次心跳优先续跑并阻止重叠周期。

## 已退出调度的旧服务器（61.172.167.201，历史记录）

- 平台入口：http://61.172.167.201:5340/
- 发布目录：`/root/chip-recommend/releases/20260915-224150`
- 持久化数据库：`/root/chip-recommend/data/data.db`
- Docker 镜像：`chip-recommend:codex-20260915-224150`
- 本次数据库备份：`/root/chip-recommend/backups/20260915-224150/data.db`（SQLite 在线快照，`quick_check=ok`）
- 回滚容器：`chip-recommend-previous-20260915-224150`（已停止；上一镜像为 `chip-recommend:codex-20260915-223614`）
- 数据更新迁移前数据库快照：`/root/chip-recommend/backups/20260915-182048/data.db`（`quick_check=ok`）
- Hermes 配置回滚目录：`/root/.hermes/backups/primary-migration-20260915-182342`
- Hermes 正式任务：`268fc87564c3`，每 30 分钟检查；门控确保每天北京时间 02:00 后只开启一个新周期，未完成则续跑；门控和 Worker 均指向本机 `chip-recommend` 容器
- Hermes Skill 备份：`/root/.hermes/backups/20260909-190238`

两台平台使用各自的持久化数据库，Flash 实测记录分别幂等导入并有字段级来源。2026-09-15 18:23 起，Hermes 每日任务改为在主要服务器本机调用数据智能体；镜像服务器历史数据不自动合并，旧来源直写通道已禁用。迁移后预检查验证续跑主要服务器现有周期 `run_id=1`，只读 Worker 验证能读取主要服务器的真实快照。主要服务器的未完成任务会先处理完，之后才开启新周期。

## Nginx

项目使用服务器原有的 `awards-nginx` 容器，通过 IP 专属虚拟主机转发；原域名站点配置保持不变。

- 配置源文件：`/home/lxc/awards/backend/nginx.conf`
- 上线前备份：`/home/lxc/awards/backend/nginx.conf.codex-backup-20260828-082611`
- 转发目标：`http://172.18.0.1:5340`

## 常用检查

```bash
docker ps --filter name=chip-recommend
docker logs --tail 100 chip-recommend
curl -fsS http://127.0.0.1:5340/api/v1/health
```

文档不保存 SSH 私钥内容或服务器密码。
