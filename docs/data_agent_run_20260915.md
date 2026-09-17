# 数据抓取智能体运行日志（2026-09-15，主服务器）

数据源：主服务器 `61.172.167.201:5340` 的 `/api/v1/source-updates`、`/api/v1/data-agent/status`。本报告是运行中状态快照，不是成功发布记录；北京时间以服务器 UTC 时间换算。周期 ID `1`，来源抓取 ID `2`。

- 周期开始：2026/9/15 12:44:24（北京时间）；当前状态：`awaiting_agent`；周期结束：未结束。
- 首轮访问：2026/9/15 12:44:24（北京时间） 至 2026/9/15 12:49:31（北京时间）。选中 50 个种子；新快照 33、失败 13、等待重试 3、格式不支持 1。
- URL 发现：14 个任务，完成 1（未找到相关目标）、拒绝 1（微信验证拦截）、等待智能体 12；正式候选 URL 0，目标页二轮访问 0。
- 字段提取与发布：候选事实 0、正式发布 0；领域提取仍有 68 个等待智能体任务。

## 1. 种子 URL 与访问结果

表中的“快照”是原始响应文件名 SHA-256 前 12 位；完整路径存在数据库 `source_checks.snapshot_path` 和任务结果中。HTTP 200 不等于页面可用，例如微信验证页被判为拦截。

| # | 请求的种子 URL | HTTP / 结果 | 响应字节 | 原始快照 |
| ---: | --- | --- | ---: | --- |
| 1 | http://m.mydrivers.com/newsview/1094106.html | 200 / new | 60037 | 874b5cf8294e |
| 2 | http://mp.weixin.qq.com/s?__biz=MzA3NDI4OTA3OQ==&mid=2450336102 | 200 / new | 18186 | c52d347e1b94 |
| 3 | http://www.chinaaet.com/article/3000110706 | 200 / new | 42734 | d662e576c552 |
| 4 | http://www.cloudhin.com/xk/showproduct.php?id=270 | 200 / new | 39242 | 9f5756341b16 |
| 5 | http://www.graphcore.ai/hubfs/MK2-%20The%20Graphcore%202nd%20Generation%20IPU%20Final%20v7.14.2020.pdf | 200 / unsupported（unsupported_content_type） | 0 | — |
| 6 | http://www.puchao.com/index.php/2025/03/22/919426f93e9533c794fbdf3949e5987/ | 200 / new | 149549 | 6932497c8071 |
| 7 | https://36kr.com | 200 / new | 17603 | acd7fde40e62 |
| 8 | https://ai.gitee.com/docs/compute/clusters_gpu/mx_gpu | 200 / new | 75343 | d6f122e26323 |
| 9 | https://ai.meta.com | — / retry_wait（connection_error） | 0 | — |
| 10 | https://aimultiple.com/multi-gpu | 200 / new | 333895 | 92fc5e412020 |
| 11 | https://aipfia.zhidx.com/p/253907.html | — / failed（tls_error） | 0 | — |
| 12 | https://artificialanalysis.ai | 200 / new | 1774795 | 3ee569dc01be |
| 13 | https://artificialanalysis.ai/models/gpt-oss-120b/providers | 200 / new | 1049485 | 1bd2aa2896a9 |
| 14 | https://arxiv.org/abs/2412.19437 | 200 / new | 73356 | 870dc015b882 |
| 15 | https://arxiv.org/abs/2506.12708 | 200 / new | 50594 | 71ea7760cd9f |
| 16 | https://arxiv.org/abs/2508.06471 | 200 / new | 68087 | 01748080d16d |
| 17 | https://arxiv.org/abs/2604.09752 | 200 / new | 39814 | c7a71fc029d2 |
| 18 | https://arxiv.org/html/2505.09343 | 200 / new | 314161 | d34324115a7b |
| 19 | https://arxiv.org/html/2505.09388 | 200 / new | 986155 | cf5c79d6f479 |
| 20 | https://aws.amazon.com/machine-learning/inferentia/ | 200 / new | 460686 | eb6639893fec |
| 21 | https://aws.amazon.com/machine-learning/trainium/ | 200 / new | 430584 | 03498f1636cc |
| 22 | https://awsdocs-neuron.readthedocs-hosted.com | 200 / new | 246785 | 346eb5f3004a |
| 23 | https://azure.microsoft.com | 200 / new | 436727 | 920ed6b9940f |
| 24 | https://baike.baidu.com | 200 / failed（empty_content） | 0 | — |
| 25 | https://baike.baidu.com/item/华为昇腾910C/67777523 | 403 / failed（http_403） | 0 | — |
| 26 | https://baike.baidu.com/item/壁砺166系列/67163995 | 403 / failed（http_403） | 0 | — |
| 27 | https://baike.baidu.com/item/寒武纪 MLU370-X8/60369997 | 403 / failed（http_403） | 0 | — |
| 28 | https://baike.baidu.com/item/思元590/67245650 | 403 / failed（http_403） | 0 | — |
| 29 | https://baike.baidu.com/item/昆仑芯 | 403 / failed（http_403） | 0 | — |
| 30 | https://baike.baidu.com/item/昆仑芯P800/67786426 | 403 / failed（http_403） | 0 | — |
| 31 | https://baike.baidu.com/item/昇腾950PR芯片/66772899 | 403 / failed（http_403） | 0 | — |
| 32 | https://baike.baidu.com/item/曦云C600/66256324 | 403 / failed（http_403） | 0 | — |
| 33 | https://baike.baidu.com/item/海光C86 | 403 / failed（http_403） | 0 | — |
| 34 | https://baike.baidu.com/item/深算二号 | 403 / failed（http_403） | 0 | — |
| 35 | https://baike.baidu.com/item/邃思2.0 | 403 / failed（http_403） | 0 | — |
| 36 | https://blog.ailemon.net/2025/10/31/national-ai-chip-param-info-collection/ | 200 / new | 154096 | 521a7e2e880f |
| 37 | https://blog.csdn.net/weixin_42405819/article/details/134774705 | 200 / new | 188451 | 84ee8ee8bf76 |
| 38 | https://blog.csdn.net/wujianing_110117/article/details/119265576 | 200 / new | 395949 | 6c2b4b943aee |
| 39 | https://blog.heim.xyz/huawei-ascend-910c/ | 200 / new | 24779 | 70585d1bf291 |
| 40 | https://cambricon.com/index.php?a=lists&c=index&catid=360 | 200 / new | 38103 | c9c92759b35f |
| 41 | https://cloud.baidu.com/article/3715556 | 200 / new | 38268 | 0e611329de1d |
| 42 | https://cloud.google.com/blog/products/compute/ironwood-tpu-age-of-inference | — / retry_wait（connection_error） | 0 | — |
| 43 | https://cloud.google.com/tpu | — / retry_wait（connection_error） | 0 | — |
| 44 | https://cloud.tencent.cn/developer/article/2695456 | 200 / new | 152362 | 14db458215d8 |
| 45 | https://cloud.tencent.com.cn/developer/article/2690422 | 200 / new | 117090 | 75146f892552 |
| 46 | https://damodev.csdn.net/68633c36b93e2f417961cb68.html | 200 / new | 73065 | e1af9d4c9869 |
| 47 | https://data.eastmoney.com/notices/detail/300474/AN202501171642300918.html | 200 / new | 35580 | 9974d3d78474 |
| 48 | https://developer.baidu.com | 200 / new | 89582 | 1a7ee5d13e25 |
| 49 | https://developer.baidu.com/article/detail.html?id=3715606 | 200 / new | 148627 | d96ca7025c65 |
| 50 | https://developer.baidu.com/article/detail.html?id=3731963 | 200 / new | 141484 | 8444cf590f72 |

## 2. URL 发现与目标访问

首轮成功页面已提取原始 `outbound_links`，例如沐曦 Gitee 文档页提取 64 条、aimultiple 多 GPU 页提取 228 条。它们只是页面链接清单，尚未通过相关性筛选、安全校验和去重，**不能称作候选目标 URL**。当前 `discovered_sources` 为 0，故目标页没有发起二轮访问，也没有目标访问状态。

| URL 发现任务 | 种子 URL | 状态 | 正式目标数 |
| ---: | --- | --- | ---: |
| 55 | http://m.mydrivers.com/newsview/1094106.html | succeeded | 0 |
| 56 | https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=[redacted]&target_url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%3F__biz%3DMzA3NDI4OTA3OQ%3D%3D%26mid%3D2450336102 | rejected：快照仅为微信“环境异常、完成验证后即可继续访问”的验证拦截页(164 字节), 无任何正文与 outbound_links, 属于反爬拦截而非可用内容, 无法发现目标 URL。 | 1 |
| 65 | http://www.puchao.com/index.php/2025/03/22/919426f93e9533c794fbdf3949e5987/ | awaiting_agent | 1 |
| 74 | https://aimultiple.com/multi-gpu | awaiting_agent | 1 |
| 75 | https://artificialanalysis.ai/ | awaiting_agent | 1 |
| 76 | https://artificialanalysis.ai/models/gpt-oss-120b/providers | awaiting_agent | 1 |
| 77 | https://arxiv.org/abs/2412.19437 | awaiting_agent | 1 |
| 78 | https://arxiv.org/abs/2506.12708 | awaiting_agent | 1 |
| 80 | https://arxiv.org/abs/2604.09752 | awaiting_agent | 1 |
| 83 | https://aws.amazon.com/cn/ai/machine-learning/inferentia/ | awaiting_agent | 1 |
| 121 | https://cloud.tencent.cn/developer/article/2695456 | awaiting_agent | 1 |
| 122 | https://cloud.tencent.com.cn/developer/article/2690422 | awaiting_agent | 1 |
| 135 | https://developer.baidu.com/article/detail.html?id=3715606 | awaiting_agent | 1 |
| 136 | https://developer.baidu.com/article/detail.html?id=3731963 | awaiting_agent | 1 |

已完成的任务 55 认为快科技页面讨论的是集群/机柜，而非芯片、模型、实测或部署详情；任务 56 遇到微信验证拦截页。因此两者都没有生成目标。其余任务仍待 Hermes 处理。

## 3. 抓取内容保存在哪里

- 原始页面：容器内 `/app/data/source_snapshots/<哈希前两位>/<原始哈希>.html`，或来源对应的其他响应格式。
- 清洗正文：`/app/data/source_snapshots/normalized/<哈希前两位>/<正文哈希>.txt`。
- 访问状态、HTTP 状态、最终 URL、异常和快照路径：数据库 `source_checks`；运行时间线：`update_run_events`；任务输入/输出：`data_agent_jobs`。
- 实例：种子 `http://m.mydrivers.com/newsview/1094106.html` 的原始响应保存于 `/app/data/source_snapshots/87/874b5cf8294e538471f93fda80ab3d2ccc0eec1b1b62994b2777b3071a401e6b.html`；清洗正文保存于 `/app/data/source_snapshots/normalized/e4/e46ac1a0f6bf93130a6a12939970bacd8d91127d630bd13d64e6eed28625b80a.txt`。
- 当前抓取器保存网页响应、清洗文本与链接清单；此次没有形成可信的芯片显存、带宽、精度性能等字段事实。原文快照不等于已写入业务表。

## 4. 大模型提取了什么

截至本快照：**字段候选事实 0、跨 Skill inbox 0、业务数据发布 0**。大模型仅完成了上述 1 个 URL 发现任务并返回“无相关目标链接”；领域提取任务没有完成。当前周期 `awaiting_agent` 且没有结束时间，不能写成“爬取→提取→注入”闭环成功。

当 Hermes Worker 恢复后，应在同一周期继续处理待领任务。每条提取事实需记录实体、字段、原文证据、来源 URL、置信度、验证与发布结果，才能补齐这部分日志。部署记录指出 Hermes 的 DeepSeek 凭据曾收到 HTTP 401；这与当前未产生字段事实相符，但仅凭状态 API 不能证明 401 是今天全部待处理任务的唯一原因，需要在 Hermes 端核对最新模型调用日志。

## 后续执行记录（北京时间 21:30—22:00）

上文是 21:30 前的状态快照，不代表现在仍没有二轮访问。Hermes 任务 `268fc87564c3` 于 21:30:35 开始、21:40:00 结束，状态为 `completed`。本次领取 5 批、共 25 条任务：6 条成功（其中 5 条 URL 发现）、19 条因证据不足或来源不合规则拒绝，没有新增字段候选，也没有业务表写入。Hermes 原始执行报告保存在主服务器 `/root/.hermes/cron/output/268fc87564c3/2026-09-15_21-40-00.md`。

本轮成功从 `https://aimultiple.com/multi-gpu` 的实际出站链接中选出 11 条目标，包括 `gpu-benchmark`、`gpu-index`、`cloud-gpu-pricing`、`methodology` 和 vLLM 文档；还从 Artificial Analysis 和 arXiv 的页面中筛选了模型评测与论文全文链接。目标 URL 先入 `source_discovery_edges`，再由抓取器访问并写入 `source_checks` 与 `data/source_snapshots`；后续 `agent_extract` / `agent_url_discovery` 任务指向对应的清洗正文快照。至 22:00，发现链路 35 条：31 条二轮访问形成新快照，1 条等待重试，3 条暂不支持。目标详情页示例：`https://aimultiple.com/cloud-gpu-pricing` 的清洗正文在容器内 `/app/data/source_snapshots/normalized/b8/b89afb5cd8e855f446e6598b17de101fae417ba716768554a32405e5866e3b0e.txt`。

领域提取已实际运行：媒体转载的边缘芯片、反爬拦截页、HTTP 渠道商页面、与库内完全一致的芯片规格被拒绝；arXiv 论文的 H800 互联信息已核对，但已有值相同，节点级网络配置也不能误写成单卡字段。此时 `extraction_candidates=0`、业务数据发布 0 是证据校验结果，不能描述成“已经注入”。

21:40 执行到工具调用上限时，5 条 Agent 租约和 3 条二轮抓取曾停在 `running`。22:00 上线中断恢复入口后，5 条 Agent 任务已重新排队；3 条二轮抓取通过门控续跑，数据库中不再有停滞的目标抓取任务。当前周期仍未结束，剩余领域提取由同一 Hermes 任务继续处理。在线任务每 30 分钟检查一次；门控仍保证北京时间 02:00 后每天最多新开一个周期，完成后当天静默。

## 第二次执行与候选复核（北京时间 22:04—22:33）

Hermes 再运行 22:04:28—22:08:39，原始报告 `/root/.hermes/cron/output/268fc87564c3/2026-09-15_22-08-39.md`。新增 5 条目标 URL，发现链路累计 40 条；领域 Skill 从 arXiv 的 H800×DeepSeek-V3 训练论文和 AWS Trainium3 官网，提出了 18 条逐字段候选。与上面的 22:00 快照不同，这一轮确实完成了大模型提取，但**提取不等于已入库**。

发布前发现：H800×模型候选的实体键仅为 `chip` / `model` 名称，缺少数据库里的完整模型 ID、场景、套件、精度和卡数，且现有库已经有相同 MFU 实测；Trainium3 候选用泛称“AI加速器”、布尔 `true` 与英文 `announced`，与项目的具体芯片类型、`0/1` 和中文发布状态不兼容。为防止将同一来源的剩余字段零散发布，18 条按来源和实体分组全部拒绝，逐条保存 `rejection_reason`，审计事件在 `update_run_events`，**正式发布 0、业务库未修改**。

主站 `61.172.167.201:5340` 已部署实体键、字段枚举和成组发布复核，部署前备份 `/root/chip-recommend/backups/20260915-223236/data.db`，新镜像 `chip-recommend:codex-20260915-223236`，旧容器保留为回滚容器。`/status` 的“系统状态 & 数据更新”可展开每条任务查看访问链接、快照位置、时间、候选链接、提取证据、拒绝原因；展开与阅读位置在轻量刷新后保留。周期仍有等待 Agent 的任务，不能称为全部业务数据已更新。Hermes 定时任务已恢复为每半小时检查进行中周期，新周期仍是每天 02:00 后最多一次。

后续页面拒绝原因展示与候选去重索引已补齐，最终镜像为 `chip-recommend:codex-20260915-224150`，对应在线数据库备份为 `/root/chip-recommend/backups/20260915-224150/data.db`。索引从按“周期、来源差异、Skill、字段、值”去重，改为同时包含目标表、实体键和来源 URL，避免不同芯片同值字段被误合并。隔离数据库副本的预发布健康检查、主站 `/`、`/chips`、`/models`、`/recommend`、`/status`、SQLite `quick_check` 均通过。Hermes Gateway 正在运行，定时任务 `268fc87564c3` 已启用，下一次为北京时间 23:00。本周期 API 报告 255 条任务、40 条发现链路、18 条候选（均带拒绝原因）、134 条等待 Agent；这是进行中快照。

## 真实提取与发布验证（22:46—23:00）

再次触发 Hermes 受限批次，报告 `/root/.hermes/cron/output/268fc87564c3/2026-09-15_22-50-33.md`。本批完成 34 条领域提取任务，主要针对 AWS Trainium/Neuron、Azure 门户、第三方芯片博客和寒武纪官网。Agent 拒绝了门户页无规格、付费墙、不确定推测、超范围嵌入式芯片及与现库相同的值；网页里的诱导 Agent 指令被作为不可信正文处理。

从 AWS [Trainium 官方页](https://aws.amazon.com/cn/ai/machine-learning/trainium/) 提取并自动发布现有 `chips.id=34` Trainium3 两个字段：`vram_type` 从 `HBM` 改为 `HBM3e`（证据“达到 144 GB 的 HBM3e 内存”），`process_node_nm` 从空值改为 `3`（证据“AWS 的首款 3 纳米人工智能芯片”）。候选 #19/#20 的原文、官方 URL、置信度和实体键均已保存；`candidate_publications` 为 `success`，发布前库备份 `/app/backups/candidate-publisher/candidate-19-20260915-144816.db`，影子测试通过数据库完整性、外键、状态、JSON 序列化和芯片搜索，`field_provenance` 写入旧值、新值与来源。由此首次观察到真实的“目标访问→提取→验证→写入”闭环，而非仅有准备日志。

本批结束时服务端仍有 5 条由同一 worker 持有的 `running` 租约与 99 条 `awaiting_agent`；同 worker 的下一次 claim 会幂等取回原批次，租约过期亦会重排。23:00 的自动调度确已由 Hermes Gateway 启动（`source=builtin`、`running`），不是本地电脑触发。周期尚未完结，不应称全量抓取完成。

执行报告还发现历史数据质量风险：现有 `chips.id=24` 的身份为 `Trainium2/AWS`，但 `vendor_display=寒武纪`、`software_stack=Cambricon Neuware`，`description` 实际描述寒武纪 MLU290。已通过只读数据库复核；本次不凭推断覆盖这些旧字段，应由后续数据质量任务以官方来源逐字段纠正并补溯源。
