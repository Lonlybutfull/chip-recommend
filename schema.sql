-- ============================================================
-- AI 芯片生态知识图谱 — SQLite Schema (V2)
-- 全部 TEXT 字段，无 NOT NULL / CHECK / 类型约束，无索引
-- 字段级溯源：所有数据行不挂 source_id，改为 field_provenance 逐字段记录
-- ============================================================

-- ============================================================
-- 1. 芯片表（合并了价格/生态/待发布，一张表覆盖芯片全生命周期）
-- ============================================================
CREATE TABLE IF NOT EXISTS chips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 标识
    vendor          TEXT,
    vendor_display  TEXT,
    vendor_region   TEXT,
    chip_series     TEXT,
    chip_model      TEXT,
    chip_type       TEXT,
    usage           TEXT,
    tier            TEXT,

    -- 架构
    architecture    TEXT,
    arch_codename   TEXT,
    generation      TEXT,
    process_node_nm TEXT,
    foundry         TEXT,
    die_size_mm2    TEXT,
    transistors_b   TEXT,
    package_type    TEXT,
    is_chiplet      TEXT,

    -- 显存
    vram_gb        TEXT,
    vram_type      TEXT,
    vram_bus_bit   TEXT,
    vram_bw_gb_s   TEXT,
    vram_clock_mhz TEXT,

    -- 计算单元
    compute_units TEXT,
    tensor_cores  TEXT,
    rt_cores      TEXT,
    shading_units TEXT,
    sm_count      TEXT,

    -- 缓存
    l1_cache_kb     TEXT,
    l2_cache_mb     TEXT,
    on_chip_sram_mb TEXT,

    -- 精度（枚举，如 "FP32, FP16, BF16, FP8, INT8, INT4"）
    precision_support TEXT,   -- 硬件支持的精度列表
    precision_perf   TEXT,   -- 各精度对应算力，如 "FP16=312TF, BF16=624TF, FP8=1248TF, INT8=624TOPS"

    -- 频率
    base_clock_mhz  TEXT,
    boost_clock_mhz TEXT,

    -- 功耗与物理
    tdp_w           TEXT,
    max_power_w     TEXT,
    psu_w           TEXT,
    power_connector TEXT,
    board_length_mm TEXT,
    board_width_mm  TEXT,
    slot_width      TEXT,
    form_factor     TEXT,
    bus_interface   TEXT,

    -- 互联
    interconnect_bw_gb_s TEXT,
    interconnect_tech    TEXT,
    network_interface    TEXT,

    -- 软件生态
    software_stack        TEXT,
    compatible_frameworks TEXT,

    -- 生命周期 & 发布状态
    release_date      TEXT,
    production_status TEXT,   -- 已量产 / 已发布 / EOL ...（已发布芯片）
    eol_date          TEXT,
    target_market     TEXT,
    is_released       TEXT,   -- 0=待发布/传闻中，1=已发布/量产（合并了 upcoming_chips）
    expected_release_date TEXT,  -- 预计发布时间（待发布芯片用）
    known_specs       TEXT,   -- 已确认的规格（待发布芯片用）
    unconfirmed_items TEXT,   -- 尚未确认的信息（待发布芯片用）

    -- 价格（合并了 chip_price）
    price_usd     TEXT,
    price_cny_wan TEXT,
    price_period  TEXT,   -- 价格对应的时间/时期
    price_notes   TEXT,   -- 价格说明

    -- 芯片介绍 & 生态评估
    description           TEXT,   -- 芯片概述（一段话）
    highlights            TEXT,   -- 核心亮点
    limitations           TEXT,   -- 已知局限/短板
    target_workloads      TEXT,   -- 适合场景：训练 / 推理 / 边缘 / HPC
    typical_deployment    TEXT,   -- 典型部署形态：单卡 / 8卡 / 集群 / 云端
    competitor_comparison TEXT,   -- 与竞品的对比说明

    -- 生态评估（原 chip_ecosystem 合并进来）
    ecosystem_notes  TEXT,   -- 生态成熟度详细说明
    maturity_level   TEXT,   -- 生态成熟度评分（0-5）
    framework_compat TEXT,   -- 兼容框架列表
    sw_stack         TEXT,   -- 推荐软件栈
    cuda_compat      TEXT,   -- CUDA 兼容程度
    cloud_available  TEXT,   -- 0/1，是否云上可用
    cluster_scale    TEXT,   -- 已知集群规模
    key_strength     TEXT,   -- 生态核心优势
    key_weakness     TEXT,   -- 生态核心短板

    -- 时间戳
    created_at TEXT,
    updated_at TEXT
);

-- ============================================================
-- 2. 模型表（照搬 HF API，存原始 JSON + 少量解析字段）
-- ============================================================
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- HF 标识
    model_id     TEXT,   -- Qwen/Qwen2.5-72B-Instruct
    author       TEXT,
    pipeline_tag TEXT,   -- text-generation / image-text-to-text / ...
    library_name TEXT,   -- transformers / diffusers / ...
    tags         TEXT,   -- HF 标签，逗号分隔

    -- 基础统计
    downloads     TEXT,
    likes         TEXT,
    last_modified TEXT,

    -- 权限
    private TEXT,
    gated   TEXT,

    -- 架构（从 config 解析，方便直接查询）
    architecture_family TEXT,   -- Dense / MoE
    total_params_b      TEXT,   -- 总参数量（B）

    -- 原始数据
    config_json      TEXT,   -- 模型的 config.json 全文
    card_data_json   TEXT,   -- README 的 YAML frontmatter
    api_response_json TEXT,  -- HF GET /api/models/{model_id} 完整返回 JSON

    -- 时间戳
    created_at TEXT,
    updated_at TEXT
);

-- ============================================================
-- 3. 芯片×模型 测试表
-- ============================================================
CREATE TABLE IF NOT EXISTS chip_model_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    chip_model TEXT,
    model_id   TEXT,

    -- 测试套件
    suite_name TEXT,

    -- 测试条件
    workload_type    TEXT,
    scenario         TEXT,
    task             TEXT,
    hardware_config  TEXT,
    chip_count       TEXT,
    framework        TEXT,
    precision        TEXT,
    batch_size       TEXT,
    input_seq_length  TEXT,
    output_seq_length TEXT,
    concurrency      TEXT,

    -- 推理指标（Prefill / Decode 拆分）
    prefill_throughput     TEXT,
    decode_throughput      TEXT,
    time_to_first_token_ms TEXT,
    inter_token_latency_ms TEXT,
    memory_peak_mb         TEXT,
    throughput_tok_s       TEXT,
    throughput_samples_s   TEXT,
    tpot_ms               TEXT,

    -- 训练指标
    mfu_pct              TEXT,
    gpu_hours            TEXT,
    training_tokens_T    TEXT,
    training_gpu_count   TEXT,
    training_workload_type TEXT,   -- pretrain / SFT / LoRA / full_finetune ...

    -- 其他
    test_date  TEXT,
    notes      TEXT,
    created_at TEXT
);

-- ============================================================
-- 4. 芯片×模型 兼容性表
-- ============================================================
CREATE TABLE IF NOT EXISTS chip_model_compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    chip_model    TEXT,
    model_id      TEXT,

    compat_status TEXT,   -- verified / vendor_claimed / community / unknown / unsupported
    framework     TEXT,
    precision     TEXT,
    verified_at   TEXT,
    notes         TEXT,
    created_at    TEXT
);

-- ============================================================
-- 5. 字段级来源追溯表
-- ============================================================
-- 每次字段值变更时 INSERT 一行。
-- old_value / new_value 记录变化，首次写入时 old_value 为 NULL。
-- 当前主表字段的值 = 该字段最新一条记录的 new_value。
CREATE TABLE IF NOT EXISTS field_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 定位：哪个表的哪一行的哪个字段
    table_name  TEXT,   -- chips / models / chip_model_benchmarks / chip_model_compatibility
    row_id      TEXT,   -- 对应表里的 id
    field_name  TEXT,   -- 字段名
    field_label TEXT,   -- 字段中文含义

    -- 旧值 → 新值
    old_value TEXT,   -- 变更前的值（首次写入为 NULL）
    new_value TEXT,   -- 变更后的值

    -- 溯源
    source_type   TEXT,   -- 来源类型
    source_url    TEXT,   -- 来源 URL
    source_detail TEXT,   -- 来源里的具体位置
    confidence    TEXT,   -- 置信度
    is_official   TEXT,   -- 0/1，是否官方来源

    updated_at TEXT,
    notes      TEXT
);

-- ============================================================
-- Indexes — added for query performance (V3)
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_chips_model ON chips(chip_model);
CREATE INDEX IF NOT EXISTS idx_chips_vendor ON chips(vendor);
CREATE INDEX IF NOT EXISTS idx_chips_region ON chips(vendor_region);
CREATE INDEX IF NOT EXISTS idx_models_model_id ON models(model_id);
CREATE INDEX IF NOT EXISTS idx_models_author ON models(author);
CREATE INDEX IF NOT EXISTS idx_benchmarks_chip ON chip_model_benchmarks(chip_model);
CREATE INDEX IF NOT EXISTS idx_benchmarks_model ON chip_model_benchmarks(model_id);
CREATE INDEX IF NOT EXISTS idx_compat_chip ON chip_model_compatibility(chip_model);
CREATE INDEX IF NOT EXISTS idx_compat_model ON chip_model_compatibility(model_id);
CREATE INDEX IF NOT EXISTS idx_provenance_table_row ON field_provenance(table_name, row_id);
CREATE INDEX IF NOT EXISTS idx_provenance_source ON field_provenance(source_type);
