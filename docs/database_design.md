# AI 芯片生态知识图谱 — 数据库设计文档

## 跟踪标记说明

**全部字段跟踪。** 所有字段每次写入或更新时均在 field_provenance 记录一行，追溯来源URL、旧值、新值、更新时间、置信度。不区分"需跟踪"和"不需跟踪"。

例外：`created_at`、`updated_at`（系统时间戳）和 field_provenance 自身字段不跟踪。

---

## 表结构总览（5 张表）

```
chips                      芯片主表（硬件规格 + 价格 + 发布状态 + 芯片介绍 + 生态评估）
models                     模型表（照搬 HuggingFace API 格式）
chip_model_benchmarks      芯片×模型 测试表（训练/推理实测数据）
chip_model_compatibility   芯片×模型 兼容性表（厂商声明与实测验证）
field_provenance           字段级来源追溯表（所有字段的来源URL、旧值新值、更新时间、置信度）
```

---

## 逐表详解

### 1. chips — 芯片主表

一张表覆盖芯片全生命周期：硬件规格、价格、发布状态、定性介绍、生态评估。

**标识**

| 字段 | 说明 |
|---|---|
| `vendor` | 厂商（NVIDIA / 华为(昇腾) / AMD / Intel / Hygon / ...） |
| `vendor_display` | 厂商显示名 |
| `vendor_region` | domestic / foreign |
| `chip_series` | 系列名（H100 / 昇腾910B） |
| `chip_model` | 具体型号（H100 SXM / 昇腾910B B1） |
| `chip_type` | 芯片类型（GPU / NPU / DCU / TPU / LPU / ASIC） |
| `usage` | 用途（训练 / 推理 / 训推一体） |
| `tier` | 级别（datacenter / consumer / edge） |

**架构**

| 字段 | 说明 |
|---|---|
| `architecture` | 架构名称（Hopper / Da Vinci / CDNA3） |
| `arch_codename` | 架构代号 |
| `generation` | 产品代际 |
| `process_node_nm` | 制程（nm） |
| `foundry` | 代工厂 |
| `die_size_mm2` | 芯片面积（mm²） |
| `transistors_b` | 晶体管数（亿） |
| `package_type` | 封装方式 |
| `is_chiplet` | 是否 Chiplet 设计（0/1） |

**显存**

| 字段 | 说明 |
|---|---|
| `vram_gb` | 显存容量（GB） |
| `vram_type` | 显存类型（HBM2e / HBM3e / GDDR6X） |
| `vram_bus_bit` | 显存位宽（bit） |
| `vram_bw_gb_s` | 显存带宽（GB/s） |
| `vram_clock_mhz` | 显存频率（MHz） |

**计算单元**

| 字段 | 说明 |
|---|---|
| `compute_units` | 计算单元数 |
| `tensor_cores` | Tensor 核心数 |
| `rt_cores` | RT 核心数 |
| `shading_units` | 着色单元数 |
| `sm_count` | SM 数量 |

**缓存**

| 字段 | 说明 |
|---|---|
| `l1_cache_kb` | L1 缓存（KB） |
| `l2_cache_mb` | L2 缓存（MB） |
| `on_chip_sram_mb` | 片上 SRAM（MB） |

**精度**

| 字段 | 说明 | 示例 |
|---|---|---|
| `precision_support` | 硬件支持的精度列表 | `"FP32, FP16, BF16, FP8, INT8"` |
| `precision_perf` | 各精度对应算力 | `"FP16=312TF, BF16=624TF, FP8=1248TF, INT8=624TOPS"` |

**频率**

| 字段 | 说明 |
|---|---|
| `base_clock_mhz` | 基础频率（MHz） |
| `boost_clock_mhz` | 加速频率（MHz） |

**功耗与物理**

| 字段 | 说明 |
|---|---|
| `tdp_w` | TDP（W） |
| `max_power_w` | 最大功耗（W） |
| `psu_w` | 建议电源功率（W） |
| `power_connector` | 供电接口类型 |
| `board_length_mm` | 卡长（mm） |
| `board_width_mm` | 卡宽（mm） |
| `slot_width` | 插槽宽度 |
| `form_factor` | 外观规格（SXM / PCIe / OAM） |
| `bus_interface` | 总线接口（PCIe 4.0 / PCIe 5.0） |

**互联**

| 字段 | 说明 |
|---|---|
| `interconnect_bw_gb_s` | 互联带宽（GB/s） |
| `interconnect_tech` | 互联技术（NVLink / HCCS / MatrixLink） |
| `network_interface` | 网络接口 |

**软件生态**

| 字段 | 说明 |
|---|---|
| `software_stack` | 推荐软件栈 |
| `compatible_frameworks` | 兼容框架列表 |

**生命周期 & 发布状态**

| 字段 | 说明 |
|---|---|
| `release_date` | 发布日期 |
| `production_status` | 量产状态（已量产 / 已发布 / EOL） |
| `eol_date` | EOL 日期 |
| `target_market` | 目标市场 |
| `is_released` | 0=待发布/传闻中，1=已发布/量产 |
| `expected_release_date` | 预计发布时间（待发布芯片用） |
| `known_specs` | 已确认的规格（待发布芯片用） |
| `unconfirmed_items` | 尚未确认的信息（待发布芯片用） |

**价格**

| 字段 | 说明 |
|---|---|
| `price_usd` | 参考价格（USD） |
| `price_cny_wan` | 参考价格（万元/片） |
| `price_period` | 价格对应的时间/时期 |
| `price_notes` | 价格说明 |

**芯片介绍**

| 字段 | 说明 |
|---|---|
| `description` | 芯片概述（一段话） |
| `highlights` | 核心亮点 |
| `limitations` | 已知局限/短板 |
| `target_workloads` | 适合场景（训练 / 推理 / 边缘 / HPC） |
| `typical_deployment` | 典型部署形态（单卡 / 8卡 / 集群 / 云端） |
| `competitor_comparison` | 与竞品的对比说明 |

**生态评估**

| 字段 | 说明 |
|---|---|
| `ecosystem_notes` | 生态成熟度详细说明 |
| `maturity_level` | 生态成熟度评分（0-5） |
| `framework_compat` | 兼容框架列表 |
| `sw_stack` | 推荐软件栈 |
| `cuda_compat` | CUDA 兼容程度 |
| `cloud_available` | 是否云上可用（0/1） |
| `cluster_scale` | 已知集群规模 |
| `key_strength` | 生态核心优势 |
| `key_weakness` | 生态核心短板 |

**时间戳**

| 字段 | 说明 |
|---|---|
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

---

### 2. models — 模型表

照搬 HuggingFace `/api/models/{model_id}` 返回格式，存原始 JSON 加少量解析字段方便查询。

**HF 标识**

| 字段 | 说明 | 示例 |
|---|---|---|
| `model_id` | HuggingFace 模型 ID | `Qwen/Qwen2.5-72B-Instruct` |
| `author` | 作者/组织 | `Qwen` |
| `pipeline_tag` | 任务类型 | `text-generation` / `image-text-to-text` / `sentence-similarity` |
| `library_name` | 库名称 | `transformers` / `diffusers` |
| `tags` | HF 标签，逗号分隔 | `"llm, chat, qwen"` |

**基础统计**

| 字段 | 说明 |
|---|---|
| `downloads` | 下载量 |
| `likes` | 点赞数 |
| `last_modified` | 最后修改时间 |

**权限**

| 字段 | 说明 |
|---|---|
| `private` | 是否私有（true/false） |
| `gated` | 是否需要审核访问（true/false） |

**架构**（从 config.json 解析，方便不解析 JSON 直接查询）

| 字段 | 说明 |
|---|---|
| `architecture_family` | 架构家族（Dense / MoE） |
| `total_params_b` | 总参数量（B） |

**原始数据**（HF API 完整返回）

| 字段 | 说明 |
|---|---|
| `config_json` | 模型 config.json 全文 |
| `card_data_json` | README 的 YAML frontmatter |
| `api_response_json` | HF `/api/models/{model_id}` 完整返回 JSON |

**时间戳**

| 字段 | 说明 |
|---|---|
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

---

### 3. chip_model_benchmarks — 芯片×模型测试表

存储某芯片运行某模型的实测性能数据。

**关联**

| 字段 | 说明 |
|---|---|
| `chip_model` | 芯片型号 |
| `model_id` | 模型 HF ID |

**测试套件**

| 字段 | 说明 |
|---|---|
| `suite_name` | 测试套件名称（MLPerf / vendor_doc / community） |

**测试条件**

| 字段 | 说明 |
|---|---|
| `workload_type` | 工作负载类型（inference / training） |
| `scenario` | 场景（serving / offline / training） |
| `task` | 任务（LLM dialogue / Code generation） |
| `hardware_config` | 硬件配置描述 |
| `chip_count` | 卡数 |
| `framework` | 框架（TensorRT-LLM / vLLM / mindspore） |
| `precision` | 精度（FP16 / FP8 / INT8） |
| `batch_size` | 批次大小 |
| `input_seq_length` | 输入 token 数 |
| `output_seq_length` | 输出 token 数 |
| `concurrency` | 并发数 |

**推理指标**（Prefill / Decode 拆分）

| 字段 | 说明 |
|---|---|
| `prefill_throughput` | Prefill 阶段 tokens/s |
| `decode_throughput` | Decode 阶段 tokens/s |
| `throughput_tok_s` | 吞吐（tokens/s） |
| `throughput_samples_s` | 吞吐（samples/s） |
| `time_to_first_token_ms` | 首 token 延迟（ms） |
| `tpot_ms` | TPOT 延迟（ms） |
| `inter_token_latency_ms` | token 间平均延迟（ms） |
| `memory_peak_mb` | 显存峰值（MB） |

**训练指标**

| 字段 | 说明 |
|---|---|
| `mfu_pct` | MFU（%） |
| `gpu_hours` | GPU 小时数 |
| `training_tokens_T` | 训练数据量（T tokens） |
| `training_gpu_count` | 训练用卡数 |
| `training_workload_type` | 训练类型 | pretrain（全量预训练）/ SFT（监督微调）/ LoRA（低秩适配）/ full_finetune（全参微调） |

**其他**

| 字段 | 说明 |
|---|---|
| `test_date` | 测试日期 |
| `notes` | 备注 |
| `created_at` | 创建时间 |

---

### 4. chip_model_compatibility — 芯片×模型兼容性表

| 字段 | 说明 | 可选值 |
|---|---|---|
| `chip_model` | 芯片型号 | |
| `model_id` | 模型 HF ID | |
| `compat_status` | 兼容状态 | `verified` / `vendor_claimed` / `community` / `unknown` / `unsupported` |
| `framework` | 验证时使用的框架 | |
| `precision` | 验证时使用的精度 | |
| `verified_at` | 验证时间 | |
| `notes` | 备注 | |
| `created_at` | 创建时间 | |

---

### 5. field_provenance — 字段级来源追溯表

**所有数据表的所有字段**（除 created_at / updated_at 和 provenance 自身），每次写入或更新时在此 INSERT 一行，记录来源URL、旧值、新值、置信度。

```
H100 芯片 (id=3) 写入 price_cny_wan=18：

   table_name  = chips          哪个表
   row_id      = 3              哪一行
   field_name  = price_cny_wan  哪个字段
   field_label = 参考价格(万元)  中文含义

   old_value   = NULL           首次写入为空
   new_value   = 18             新写入的值

   source_type = community      来源类型
   source_url  = https://reddit.com/...
   source_detail = 回帖#42       来源具体位置
   confidence  = low            置信度
   is_official = 0              非官方

   updated_at  = 2026-07-27
```

| 字段 | 说明 |
|---|---|
| `table_name` | 哪个表（chips / models / chip_model_benchmarks / chip_model_compatibility） |
| `row_id` | 对应表里的 id |
| `field_name` | 字段名 |
| `field_label` | 字段中文含义 |
| `old_value` | 变更前的值（首次写入为 NULL） |
| `new_value` | 变更后的值 |
| `source_type` | 来源类型（official_datasheet / official_news / paper / community / vendor_claim / benchmark_suite） |
| `source_url` | 来源 URL |
| `source_detail` | 来源里的具体位置 |
| `confidence` | 置信度 |
| `is_official` | 是否官方来源（0/1） |
| `updated_at` | 更新时间 |
| `notes` | 备注 |
