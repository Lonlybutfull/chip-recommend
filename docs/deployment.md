# 当前线上部署

更新时间：2026-09-09

## 访问入口

- 平台首页：http://81.70.231.92/
- 算力推荐：http://81.70.231.92/recommend
- 芯片搜索：http://81.70.231.92/chips
- 模型搜索：http://81.70.231.92/models
- 健康检查：http://81.70.231.92/api/v1/health

公网通过服务器现有 Nginx 的 80 端口转发到应用容器。应用仍在服务器本机的 5340 端口监听，但该非标准端口未对公网开放。

## 服务器部署信息

- SSH：`lxc@81.70.231.92`
- 项目根目录：`/home/lxc/chip-recommend`
- 当前发布目录：`/home/lxc/chip-recommend/releases/20260909-150900`
- 持久化数据库：`/home/lxc/chip-recommend/data/data.db`
- Docker 容器：`chip-recommend`
- Docker 镜像：`chip-recommend:codex-20260909-150900`
- 容器端口：`0.0.0.0:5340 -> 8000/tcp`
- 重启策略：`unless-stopped`
- 本次数据库备份：`/home/lxc/chip-recommend/backups/20260909-150900/data.db`
- 调度切换备份：`/home/lxc/chip-recommend/backups/20260909-143438/crontab.before-hermes`
- 回滚容器：`chip-recommend-previous-20260909-150900`（已停止）

## 自动数据自检

- 执行时间：每天北京时间 `02:00`
- 调度方：`61.172.167.201` 上的 Hermes Gateway（开机自启）
- Hermes 任务：`AISHPerf 数据更新心跳`（ID `268fc87564c3`）
- 调用方式：Hermes 使用固定来源 IP、固定主机指纹和强制命令的受限 SSH 密钥，只能调用 `/home/lxc/chip-recommend/run_source_refresh.sh`
- 互斥锁：`/home/lxc/chip-recommend/source-refresh.lock`，避免重复运行
- 运行日志：`/home/lxc/chip-recommend/logs/source-refresh.log`
- 页面入口：http://81.70.231.92/status

任务只记录来源页面的首次基线和后续差异，不直接修改芯片、模型等业务表。无变化时门控保持安静；发现页面变化或来源连续失败达到阈值时才唤醒 Agent 生成中文通知。确认发生实际变化后，可在“系统状态 & 数据更新”页面查看。

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
