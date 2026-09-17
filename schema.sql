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

-- ============================================================
-- 6. 信息来源链接库
-- ============================================================
CREATE TABLE IF NOT EXISTS link_library (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT,
    description   TEXT,
    vendor        TEXT,
    category      TEXT,
    access_method TEXT,
    accessible    TEXT,
    needs_proxy   TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_link_library_category ON link_library(category);
CREATE INDEX IF NOT EXISTS idx_link_library_vendor ON link_library(vendor);

-- 链接角色与发现链路独立于旧 link_library，避免破坏已有数据格式。
CREATE TABLE IF NOT EXISTS source_registry (
    link_id           INTEGER PRIMARY KEY,
    canonical_url     TEXT NOT NULL,
    url_role          TEXT NOT NULL DEFAULT 'seed',
    crawl_depth       INTEGER NOT NULL DEFAULT 0,
    parent_link_id    INTEGER,
    discovery_job_id  INTEGER,
    discovered_at     TEXT,
    updated_at        TEXT,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_link_id) REFERENCES link_library(id) ON DELETE SET NULL
);

-- 原始评测工作簿逐行归档：保留所有非空行（包括测试说明、
-- 能耗、适配性和对比表），并将能识别的推理条件/指标结构化。
-- 缺失的模型、物理卡数不臆测；只有核实后的 benchmark 进入排序。
CREATE TABLE IF NOT EXISTS benchmark_workbook_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sha256 TEXT NOT NULL,
    source_workbook TEXT NOT NULL,
    source_url TEXT,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    row_kind TEXT NOT NULL,
    chip_model TEXT,
    model_id TEXT,
    physical_cards TEXT,
    nodes TEXT,
    deployment TEXT,
    precision TEXT,
    input_tokens TEXT,
    output_tokens TEXT,
    concurrency TEXT,
    output_throughput_tok_s TEXT,
    ttft_ms TEXT,
    tpot_ms TEXT,
    metrics_json TEXT NOT NULL,
    cells_json TEXT NOT NULL,
    formulas_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_sha256, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_source_registry_canonical
ON source_registry(canonical_url);

CREATE INDEX IF NOT EXISTS idx_source_registry_parent
ON source_registry(parent_link_id);

CREATE TABLE IF NOT EXISTS source_discovery_edges (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_run_id      INTEGER NOT NULL,
    discovery_job_id  INTEGER NOT NULL,
    parent_link_id    INTEGER,
    child_link_id     INTEGER,
    source_url        TEXT NOT NULL,
    canonical_url     TEXT NOT NULL,
    url_role          TEXT NOT NULL,
    crawl_depth       INTEGER NOT NULL,
    status            TEXT NOT NULL,
    reason            TEXT,
    created_at        TEXT NOT NULL,
    fetched_at        TEXT,
    FOREIGN KEY(cycle_run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_link_id) REFERENCES link_library(id) ON DELETE SET NULL,
    FOREIGN KEY(child_link_id) REFERENCES link_library(id) ON DELETE SET NULL,
    UNIQUE(cycle_run_id, canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_source_discovery_cycle
ON source_discovery_edges(cycle_run_id, status);

-- ============================================================
-- 7. 部署方案链接表
-- ============================================================
CREATE TABLE IF NOT EXISTS deployment_guides (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chip_model    TEXT,           -- NULL/空 = 不限芯片（通用方案）
    model_id      TEXT,           -- NULL/空 = 不限模型（芯片通用方案）
    backend       TEXT,           -- 运行时后端: cuda/rocm/ascend/mlu/biren/xpu/tpu/... (NULL=通用)
    url           TEXT NOT NULL,  -- 部署方案 URL
    title         TEXT,           -- 显示标题（如 "vLLM Ascend 部署指南"）
    source_type   TEXT,           -- official_doc / community_guide / vendor_doc
    notes         TEXT,           -- 备注（如适用的卡数、精度）
    created_at    TEXT,
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_deploy_chip ON deployment_guides(chip_model);
CREATE INDEX IF NOT EXISTS idx_deploy_model ON deployment_guides(model_id);
CREATE INDEX IF NOT EXISTS idx_deploy_backend ON deployment_guides(backend);

-- ============================================================
-- 8. 来源增量复查控制面（不属于业务事实，不写 field_provenance）
-- ============================================================
CREATE TABLE IF NOT EXISTS update_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type           TEXT,
    mode               TEXT,
    status             TEXT,
    requested_link_ids TEXT,
    started_at         TEXT,
    finished_at        TEXT,
    counts_json        TEXT,
    error_summary      TEXT,
    created_at         TEXT
);

-- 同一种任务同一时间只允许一个运行实例。
CREATE UNIQUE INDEX IF NOT EXISTS idx_update_runs_one_running
ON update_runs(run_type) WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_update_runs_started
ON update_runs(started_at);

-- Agent/Skill 共用的结构化运行轨迹。它记录过程，不保存业务事实；后续芯片、
-- 模型、生态、价格等更新任务都可以复用同一张表。
CREATE TABLE IF NOT EXISTS update_run_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    link_id      INTEGER,
    actor        TEXT NOT NULL,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    message      TEXT NOT NULL,
    details_json TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_update_run_events_run_time
ON update_run_events(run_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_update_run_events_link_time
ON update_run_events(link_id, created_at);

CREATE TABLE IF NOT EXISTS source_monitor_state (
    link_id              INTEGER PRIMARY KEY,
    etag                 TEXT,
    last_modified        TEXT,
    raw_hash             TEXT,
    content_hash         TEXT,
    last_checked_at      TEXT,
    next_check_at        TEXT,
    failure_count        INTEGER DEFAULT 0,
    last_http_status     INTEGER,
    last_error_code      TEXT,
    last_error_message   TEXT,
    last_run_id          INTEGER,
    updated_at           TEXT,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE CASCADE,
    FOREIGN KEY(last_run_id) REFERENCES update_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_source_monitor_due
ON source_monitor_state(next_check_at);

CREATE TABLE IF NOT EXISTS source_checks (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER,
    link_id               INTEGER,
    requested_url         TEXT,
    final_url             TEXT,
    outcome               TEXT,
    http_status           INTEGER,
    attempt_count         INTEGER,
    previous_raw_hash     TEXT,
    raw_hash              TEXT,
    previous_content_hash TEXT,
    content_hash          TEXT,
    etag                  TEXT,
    last_modified         TEXT,
    content_type          TEXT,
    response_bytes        INTEGER,
    snapshot_path         TEXT,
    error_code            TEXT,
    error_message         TEXT,
    checked_at            TEXT,
    duration_ms           INTEGER,
    FOREIGN KEY(run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE CASCADE,
    UNIQUE(run_id, link_id)
);

CREATE INDEX IF NOT EXISTS idx_source_checks_link_time
ON source_checks(link_id, checked_at);

CREATE INDEX IF NOT EXISTS idx_source_checks_outcome
ON source_checks(outcome);

-- 可读正文差异与候选字段供 Agent 校验和人类追溯，不属于正式业务数据。
CREATE TABLE IF NOT EXISTS source_diffs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_check_id       INTEGER,
    run_id                INTEGER,
    link_id               INTEGER,
    previous_content_hash TEXT,
    content_hash          TEXT,
    added_lines           INTEGER DEFAULT 0,
    removed_lines         INTEGER DEFAULT 0,
    truncated             INTEGER DEFAULT 0,
    diff_summary          TEXT,
    diff_path             TEXT,
    candidate_fields_json TEXT,
    created_at            TEXT,
    FOREIGN KEY(source_check_id) REFERENCES source_checks(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE CASCADE,
    UNIQUE(run_id, link_id)
);

CREATE INDEX IF NOT EXISTS idx_source_diffs_link_time
ON source_diffs(link_id, created_at);

-- Hermes 对来源变化完成机器校验后直接发布的审计记录。每条变化对同一芯片
-- 最多应用一次；真正的字段历史仍由 field_provenance 逐字段记录。
CREATE TABLE IF NOT EXISTS source_auto_applies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_diff_id      INTEGER NOT NULL,
    chip_id             INTEGER NOT NULL,
    chip_model          TEXT NOT NULL,
    status              TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    fields_json         TEXT NOT NULL,
    old_values_json     TEXT,
    evidence_json       TEXT NOT NULL,
    validation_json     TEXT NOT NULL,
    backup_path         TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    error_summary       TEXT,
    FOREIGN KEY(source_diff_id) REFERENCES source_diffs(id),
    FOREIGN KEY(chip_id) REFERENCES chips(id),
    UNIQUE(source_diff_id, chip_id)
);

CREATE INDEX IF NOT EXISTS idx_source_auto_applies_time
ON source_auto_applies(started_at);

-- ============================================================
-- 9. 数据抓取智能体控制面
-- ============================================================
-- 一个 cycle 可以生成多个 Skill 任务。采集/解析任务可并行领取，正式发布仍由
-- 单一 Publisher 串行执行。
CREATE TABLE IF NOT EXISTS data_agent_jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_run_id   INTEGER NOT NULL,
    dedupe_key     TEXT NOT NULL UNIQUE,
    job_type       TEXT NOT NULL,
    skill_name     TEXT NOT NULL,
    entity_type    TEXT,
    entity_id      TEXT,
    link_id        INTEGER,
    field_group    TEXT,
    priority       INTEGER DEFAULT 50,
    status         TEXT NOT NULL,
    scheduled_for  TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    attempt_count  INTEGER DEFAULT 0,
    worker_id      TEXT,
    lease_until    TEXT,
    input_json     TEXT,
    output_json    TEXT,
    error_summary  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY(cycle_run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_data_agent_jobs_queue
ON data_agent_jobs(status, scheduled_for, priority DESC, id);

CREATE INDEX IF NOT EXISTS idx_data_agent_jobs_cycle
ON data_agent_jobs(cycle_run_id, status, id);

-- 同一份网页快照被哪些 Skill 解析过；parser_version 变化时可以安全重放。
CREATE TABLE IF NOT EXISTS parse_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_check_id INTEGER,
    run_id          INTEGER,
    link_id         INTEGER,
    skill_name      TEXT NOT NULL,
    content_hash    TEXT,
    parser_version  TEXT NOT NULL,
    status          TEXT NOT NULL,
    extracted_count INTEGER DEFAULT 0,
    error_summary   TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    details_json    TEXT,
    FOREIGN KEY(source_check_id) REFERENCES source_checks(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE SET NULL,
    UNIQUE(link_id, skill_name, content_hash, parser_version)
);

CREATE INDEX IF NOT EXISTS idx_parse_ledger_run
ON parse_ledger(run_id, skill_name, status);

-- 解析器或 Agent 产出的候选事实。没有完成证据与实体校验前不得写正式业务表。
CREATE TABLE IF NOT EXISTS extraction_candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_run_id        INTEGER NOT NULL,
    source_check_id     INTEGER,
    source_diff_id      INTEGER,
    link_id             INTEGER,
    owner_skill         TEXT NOT NULL,
    target_table        TEXT NOT NULL,
    entity_type         TEXT,
    entity_key_json     TEXT,
    field_name          TEXT NOT NULL,
    proposed_value      TEXT,
    unit                TEXT,
    evidence_text       TEXT,
    evidence_location   TEXT,
    source_url          TEXT,
    source_type         TEXT,
    confidence          TEXT,
    status              TEXT NOT NULL,
    rejection_reason    TEXT,
    extractor_version   TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    validated_at        TEXT,
    published_at        TEXT,
    FOREIGN KEY(cycle_run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(source_check_id) REFERENCES source_checks(id) ON DELETE SET NULL,
    FOREIGN KEY(source_diff_id) REFERENCES source_diffs(id) ON DELETE SET NULL,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS candidate_baselines (
    candidate_id INTEGER PRIMARY KEY REFERENCES extraction_candidates(id) ON DELETE CASCADE,
    baseline_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_candidates_entity_dedupe
ON extraction_candidates(
    cycle_run_id, COALESCE(source_diff_id, 0), owner_skill,
    target_table, COALESCE(entity_key_json, ''), COALESCE(source_url, ''), field_name,
    COALESCE(proposed_value, '')
);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_status
ON extraction_candidates(status, owner_skill, created_at);

-- 一个 Skill 在页面里发现另一个数据域的信息时写入 inbox，目标 Skill 复用已有
-- 快照，不再重复访问相同 URL。
CREATE TABLE IF NOT EXISTS skill_inbox (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_run_id     INTEGER NOT NULL,
    from_skill       TEXT NOT NULL,
    to_skill         TEXT NOT NULL,
    link_id          INTEGER,
    source_check_id  INTEGER,
    content_hash     TEXT,
    topic            TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL,
    claimed_at       TEXT,
    completed_at     TEXT,
    FOREIGN KEY(cycle_run_id) REFERENCES update_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(link_id) REFERENCES link_library(id) ON DELETE SET NULL,
    FOREIGN KEY(source_check_id) REFERENCES source_checks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_inbox_target
ON skill_inbox(to_skill, status, created_at);

-- 实体消歧共用别名，防止不同 Skill 各自维护一套名称映射。
CREATE TABLE IF NOT EXISTS entity_aliases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    alias          TEXT NOT NULL,
    normalized     TEXT NOT NULL,
    source_url     TEXT,
    confidence     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(entity_type, normalized)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity
ON entity_aliases(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS candidate_publications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_run_id       INTEGER NOT NULL,
    target_table       TEXT NOT NULL,
    target_row_id      TEXT,
    candidate_ids_json TEXT NOT NULL,
    status             TEXT NOT NULL,
    old_values_json    TEXT,
    new_values_json    TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    backup_path        TEXT,
    validation_json    TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    error_summary      TEXT,
    FOREIGN KEY(cycle_run_id) REFERENCES update_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidate_publications_cycle
ON candidate_publications(cycle_run_id, status, id);
