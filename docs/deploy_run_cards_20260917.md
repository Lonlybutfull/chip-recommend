# 独立运行卡片 Docker 部署记录

部署时间：2026-09-17。目标：Ubuntu，81.70.231.92。用户确认使用 HTTP。

## 部署结果

- 页面：http://81.70.231.92/status
- 容器：`chip-recommend`
- 镜像：`chip-recommend:run-cards-20260917-025403`
- 发布目录：`/home/lxc/chip-recommend/releases/20260917-025403`
- 端口：`5340 -> 8000`，由已有 Nginx 的 HTTP 80 端口转发。
- 数据目录：`/home/lxc/chip-recommend/data` 挂载到 `/app/data`。
- 重启策略：`unless-stopped`。

使用 ubuntu 用户执行已授权的 lxc 密码重置，随后以 lxc 用户完成全部 Docker 操作。文档和部署脚本均不保存密码。

部署包含当前工作区的前后端变更和候选基线兼容修复；上传白名单只包含应用代码、静态资源、入口脚本、技能、schema 与依赖文件，没有上传本地数据库、密钥、.env 或本地测试会话。release/manifest.json 保存上传文件 SHA-256。

镜像继承之前线上镜像 `chip-recommend:codex-20260916-150306`，保留运行环境，再安装当前 requirements（新增 openpyxl、et-xmlfile）。保留原容器环境变量、数据挂载、端口和启动命令；未改动 Nginx 其他站点。

## 验证

- 部署前 SQLite 在线备份：quick_check=ok。
- 独立临时容器使用数据库副本完成健康、状态页、JS/CSS、历史查询和芯片接口冒烟测试后才切换。
- 公网 `/api/v1/health`、`/status`、`/run-history.js`、`/run-history.css`、模型查询均 HTTP 200。
- 线上 4 个原有隔离会话正常枚举，每个详情接口 HTTP 200，无不可读会话警告。
- Playwright 验证状态页展示 4 张独立卡片，无 JavaScript 页面错误。
- 本地测试会话未上传；线上卡片来自服务器自己的历史数据。
- 业务核心五表与部署前备份按行哈希比较，确认没有因部署产生业务数据改动。

## 备份与回滚

- 备份目录：`/home/lxc/chip-recommend/backups/20260917-025403`
- 数据库在线快照：上述目录的 `data.db`，另在持久化目录 `data/backups/20260917-025403/data.db` 保存一份。
- 原容器配置：备份目录 `container.json`，权限 0600，可能含运行环境凭据，不要公开。
- 旧容器：`chip-recommend-previous-20260917-025403`，已停止保留。

应用回滚命令（以 lxc 执行）：

```bash
docker stop chip-recommend
docker rename chip-recommend chip-recommend-failed-20260917-025403
docker rename chip-recommend-previous-20260917-025403 chip-recommend
docker start chip-recommend
curl -fsS http://127.0.0.1:5340/api/v1/health
```

本次数据库变更仅增加候选基线表，旧版本可继续使用；常规应用回滚不恢复数据库，以免覆盖部署后新增数据。
