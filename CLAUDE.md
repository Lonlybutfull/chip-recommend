# 芯片+模型 — AI 芯片 × 模型 知识图谱

## 项目定位

构建 AI 芯片 × 模型的知识图谱，提供芯片搜索、画像、筛选、推荐能力，底层为 SQLite 5 表 schema（V2）。

**数据来源**：
- 芯片：`data/信息来源链接库_final.csv`（473 个 URL）→ 爬取 → 提取 → 入库
- 模型：[HuggingFace API](https://huggingface.co/api/models) 实时拉取
- 基准测试：MLPerf + 社区实测
- 代理：`http://127.0.0.1:7897`（Clash，需先开梯子）

## 目录结构

```
芯片+模型/
├── chip_model/                    # 主 Python 包
│   ├── __init__.py                # 包初始化
│   ├── database.py                # 数据库连接 + 查询 + 写入接口
│   ├── server.py                  # FastAPI 后端（16 API 端点）
│   ├── cli_app.py                 # CLI 入口（Typer 框架，12 命令）
│   ├── config.py                  # 配置管理
│   ├── legacy.py                  # 历史批量扩充脚本
│   └── pipeline/                  # 数据处理流水线
│       ├── seed.py                # 统一播种脚本
│       ├── crawl.py               # 统一爬取脚本
│       ├── enrich.py              # 自动化扩充管道
│       ├── extract_chips.py       # 芯片数据提取
│       ├── extract_benchmarks.py  # 基准测试提取
│       └── extract_prices.py      # 价格数据提取
├── scripts/                       # 入口脚本
│   ├── run_server.py              # 启动 FastAPI 服务
│   ├── run_cli.py                 # CLI 入口
│   ├── run_seed.py                # 播种脚本入口
│   ├── run_crawl.py               # 爬取脚本入口
│   └── run_enrich.py              # 扩充脚本入口
├── docs/                          # 文档
│   ├── database_design.md         # 数据库字段说明
│   ├── cli_design.md              # CLI 规格书
│   ├── CLI_EXAMPLES.md            # CLI 示例
│   ├── CLI_TEST_DOC.md            # CLI 测试文档
│   ├── progress.md                # 项目进度
│   └── data_fixes.md              # 数据修正记录
├── data/                          # 数据文件
│   ├── parse1.db                  # SQLite 数据库
│   └── 信息来源链接库_final.csv     # 473 个信息来源 URL
├── schema.sql                     # 5 表 DDL
├── static/
│   └── index.html                 # 前端 SPA
├── tests/                         # 测试
├── .claude/skills/                # chip-catalog + chip-enrich 技能
├── Dockerfile                     # 容器化部署
├── requirements.txt               # Python 依赖
└── .gitignore
```

## 5 张核心表

| 表 | 字段数 | 说明 |
|---|---|---|
| `chips` | 78 | 芯片全生命周期：硬件规格 + 价格 + 发布状态 + 芯片介绍 + 生态评估 |
| `models` | 18 | 模型信息，HF API 实时拉取，含 config.json + api_response_json |
| `chip_model_benchmarks` | 32 | 芯片×模型 推理/训练实测数据 |
| `chip_model_compatibility` | 9 | 芯片×模型 兼容关系（verified/vendor_claimed/community） |
| `field_provenance` | 14 | **核心表** — 所有字段每次写入/更新记录来源 URL、新旧值、置信度 |

**关键设计决策**：
- 全部 TEXT 字段，无 NOT NULL / CHECK / 类型约束，无索引（灵活性优先）
- chips 一张表覆盖芯片全生命周期
- 字段级溯源：不挂 source_id，而是 field_provenance 逐字段记录
- 模型数据从 HF API 实时拉取，存储原始 JSON

## 当前数据状态

| 表 | 行数 | 说明 |
|---|---|---|
| chips | 49 | 27 家厂商（国产 17 + 海外 10），23 已量产 / 18 已发布 / 7 未公开发布 / 1 待发布 |
| models | 206 | LLM×53, VLM×31, Embedding×16, BERT×18, Audio×11, 其他×77 |
| chip_model_benchmarks | 22 | MLPerf + 社区推理/训练实测 |
| chip_model_compatibility | 23 | verified×15, vendor_claimed×7, community×1 |
| field_provenance | 5,598 | 65% official_datasheet，全部可追溯 |

### 数据质量

| 字段 | 覆盖率 | 说明 |
|---|---|---|
| architecture | 92% | 几乎全部芯片有架构信息 |
| vram_gb | 84% | 显存容量覆盖良好 |
| maturity_level | 78% | 生态成熟度评估 |
| tdp_w | 69% | 功耗数据 |
| precision_perf | 59% | 精度性能数据 |
| process_node_nm | 59% | 制程信息 |
| arch_codename | 51% | 架构代号 |
| vram_bw_gb_s | 37% | 显存带宽 — 国产芯片缺口大 |
| interconnect_bw_gb_s | 27% | 互联带宽 — 最难获取 |
| die_size_mm2 | 14% | 芯片面积 |
| transistors_b | 18% | 晶体管数 |
| compute_units | 16% | 计算单元数 |

**7 颗未公开发布芯片**：JM1100、TX510、SN40L、Goldwasser、燧原L600、珠海1号、沐曦N260 — 均标记 `is_released=0` + `production_status="未公开发布"`，前端半透明 + 红色 ⚠ 标签。

## CLI 命令（12 个）

| 组 | 命令 | 功能 |
|---|---|---|
| `chip` | `search` | 模糊搜索 + 多条件筛选 + 模型驱动显存推算 |
| | `profile` | 芯片完整画像（规格+生态+评测+兼容+溯源） |
| | `recommend` | 芯片推荐（9 维评分：算力/效率/价格/功耗/生态/互联/SLA/数据质量/就绪度） |
| `model` | `search` | 模型搜索（按名称/架构/参数量 + 按芯片反查） |
| | `profile` | 模型画像（HF 元数据 + 兼容芯片 + 溯源） |
| `benchmark` | `search` | 评测数据搜索（芯片×模型 推理/训练实测） |
| `compat` | `search` | 兼容性查询（按芯片/模型/状态） |
| `provenance` | `show` | 来源追溯查询（按表/行/字段筛选） |
| | `stats` | 来源追溯统计（按表/来源类型/置信度聚合） |
| `db` | `status` | 数据库统计信息 |
| `config` | `show / set` | 配置管理 |

## Web 前端 + API

### 启动

```bash
cd 芯片+模型
python scripts/run_server.py
# 浏览器打开 http://localhost:8000
# API 文档 http://localhost:8000/docs
```

### 5 个 Tab

| Tab | 功能 |
|---|---|
| 🔍 芯片搜索 | 厂商(中英文)/地区/用途/显存/成熟度 多条件筛选 + 详情弹窗(评测+兼容+溯源) |
| 🧠 模型搜索 | 按名称/架构/参数量/芯片反查 + 详情弹窗(兼容芯片) |
| 🎯 算力推荐 | 9 维评分引擎，国产优先/厂商偏好/卡数/价格/成熟度约束 |
| 🔗 兼容性 | 芯片↔模型兼容关系浏览 |
| 📊 系统状态 | 数据库全景统计 |

### API 端点（16 个）

```
GET  /api/v1/chips             芯片搜索（多条件筛选）
GET  /api/v1/chips/recommend    芯片推荐（9维评分）
GET  /api/v1/chips/{id}         芯片画像
POST /api/v1/chips/batch        批量芯片画像
GET  /api/v1/models             模型搜索
GET  /api/v1/models/{id}        模型画像
POST /api/v1/models/batch       批量模型画像
GET  /api/v1/benchmarks         评测数据搜索
GET  /api/v1/compat             兼容性搜索
GET  /api/v1/provenance         来源追溯
GET  /api/v1/provenance/stats   来源统计
GET  /api/v1/db/status          数据库状态
GET  /api/v1/health             健康检查
GET  /docs                      Scalar API 文档
```

### 使用方式

```bash
# 完整重建数据库
python scripts/run_seed.py --reset

# 仅爬取芯片页面
python scripts/run_crawl.py --pipe chips --max-chips 50

# 仅拉取 HF 模型
python scripts/run_crawl.py --pipe models --max-models 100

# 自动化全量扩充
python scripts/run_enrich.py
```

需要代理 `http://127.0.0.1:7897`。

## 关键规则

- 排除集群/服务器，只保留 GPU/NPU/DCU/TPU/PPU 等算力芯片
- 精度支持情况需记录完整（FP32→FP4）
- 所有数据变更必须写 field_provenance
- 实测数据、外推数据、理论估算使用不同标识和可信等级
- 模型数据从 HuggingFace API 获取
- CLI 输出 JSON，用 `ensure_ascii=False` + fallback 处理编码
- 未公开发布芯片标记为 `production_status="未公开发布"` + `is_released="0"`
- 前端卡片半透明 + ⚠ 未公开 红色标签
- 厂商筛选同时匹配 vendor 和 vendor_display（解决中英文混合问题）
- 169 条 llm_curated 仅用于非关键字段（description/ecosystem/lifecycle），核心硬件字段来自实际来源
- chip_type 需准确：华为昇腾系列是 NPU(达芬奇架构)，Google TPU 是 TPU，GPU/NPU/DCU/MLU/ASIC 不混用

## 数据修正记录

| 时间 | 芯片 | 修正 |
|---|---|---|
| v0.5.1 | MLU370-S4 | vram 5GB→24GB |
| v0.5.1 | 沐曦N100 | vram 64GB→16GB |
| v0.5.1 | 昇腾910C | vram_bw 2456→3200GB/s, tdp 310→600W |
| v0.5.1 | 昇腾950PR | chip_type GPU→NPU, tdp→900W, +ecosystem details |
| v0.5.1 | Ironwood TPU v7 | chip_type GPU→TPU, +Google Cloud official data |
| v0.5.1 | MLU690 | undisclosed→已量产 (7nm, 2025) |
| v0.5.1 | 镇岳810E PPU | undisclosed→已量产 (对标H20, 400+客户) |
| v0.5.1 | TX82 | undisclosed→待发布 (14nm全国产, 2026) |
| v0.5.1 | 4 chips | hallucinated description 清理 |
| v0.5.1 | 7 undisclosed | known_specs 精炼 |

## 项目进度

9 项任务（工作.md）全部完成。详见 [[project-progress]]。
