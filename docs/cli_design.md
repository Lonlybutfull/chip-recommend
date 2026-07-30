# Parse1 CLI 设计文档 (V4 — 五表平等设计)

## 命令总览（12 个命令，5 组）

```
parse1
├── chip search             芯片搜索（模糊匹配 + 多条件筛选 + 模型驱动推算）
├── chip profile            芯片完整画像（规格 + 生态 + 评测 + 兼容 + 溯源）
├── chip recommend          芯片推荐（模型×场景×约束 → 9维评分排序）
├── model search            模型搜索（名称/架构/参数量 + 按芯片反查）
├── model profile           模型画像（HF 元数据 + 兼容芯片 + 溯源）
├── benchmark search        评测数据搜索（芯片×模型 推理/训练实测）
├── compat search           兼容性查询（芯片×模型 适配状态）
├── provenance show         来源追溯查询（按表/行/字段查溯源记录）
├── provenance stats        来源追溯统计（按来源类型/置信度/表聚合）
├── db status               数据库统计信息
├── config show             查看配置
└── config set              设置配置
```

全局选项：`--db-path` 指定数据库路径，`--version` 显示版本。

所有命令输出纯 JSON 到 stdout。错误和诊断信息到 stderr。

---

## 五张表与命令映射

| 表 | 字段数 | CLI 命令 | 说明 |
|----|--------|----------|------|
| `chips` | 78 | chip search / profile / recommend | 芯片全生命周期数据 |
| `models` | 18 | model search / profile | HF API 镜像数据 |
| `chip_model_benchmarks` | 32 | benchmark search | 芯片×模型 推理/训练实测 |
| `chip_model_compatibility` | 9 | compat search | 芯片×模型 兼容状态 |
| `field_provenance` | 14 | provenance show / stats | 字段级来源追溯（核心表） |

---

## 模块契约

### database.py 接口

所有函数返回 `list[dict]` 或 `dict`，每行是 `sqlite3.Row` 转 dict。

```
chips 表
────────────────────────────────────────────────────────────────────────────
search_chips(filters: ChipFilters, limit: int, offset: int)
  → {"count": int, "chips": list[dict]}   (每行经 chip_summary() 格式化)

get_chip_profile(name: str)
  → dict  (经 chip_profile() 格式化：分组 chip + benchmarks[] + compatibilities[] + provenance[])

get_chip_recommend_candidates(model_name: str, scenario: str, tier: str,
                              prefer_domestic: bool)
  → tuple[dict, list[dict]]  (model dict, candidates list — 原始 dict，由 cli 层评分)

models 表
────────────────────────────────────────────────────────────────────────────
search_models(filters: ModelFilters, limit: int, offset: int)
  → {"count": int, "models": list[dict]}   (每行经 model_summary() 格式化)

get_model_profile(name: str)
  → dict  (经 model_profile() 格式化：分组 model + compatible_chips[] + provenance[])

chip_model_benchmarks 表
────────────────────────────────────────────────────────────────────────────
search_benchmarks(filters: BenchmarkFilters, limit: int, offset: int)
  → {"count": int, "benchmarks": list[dict]}   (每行经 group_benchmark() 分组)

chip_model_compatibility 表
────────────────────────────────────────────────────────────────────────────
search_compat(filters: CompatFilters, limit: int, offset: int)
  → {"count": int, "compatibilities": list[dict]}   (每行经 group_compat() 分组)

field_provenance 表
────────────────────────────────────────────────────────────────────────────
search_provenance(filters: ProvenanceFilters, limit: int, offset: int)
  → {"count": int, "records": list[dict]}

get_provenance_stats(table: str | None)
  → dict  (按表/来源类型/置信度的聚合统计)

db / config
────────────────────────────────────────────────────────────────────────────
get_db_stats() → dict
load_config() → dict
set_config(key: str, value: str) → dict
```

### Filter 数据结构

```python
@dataclass
class ChipFilters:
    search: str | None          # 模糊匹配 vendor/chip_model/chip_series/architecture
    vendor: str | None
    region: str | None          # domestic | foreign
    usage: str | None           # train | inference | both
    vram_min: float | None
    vram_max: float | None
    tdp_max: float | None
    price_max: float | None
    interconnect_min: float | None
    tier: str | None            # datacenter | consumer | all
    min_maturity: int | None    # 0-5
    for_model: str | None       # 模型名，触发自动推算显存
    scenario: str | None        # train | inference（配合 for_model）

@dataclass
class ModelFilters:
    search: str | None
    architecture: str | None    # dense | moe
    params_min: float | None
    params_max: float | None
    for_chip: str | None        # 芯片名 → JOIN chip_model_compatibility

@dataclass
class BenchmarkFilters:
    chip: str | None            # chip_model 模糊匹配
    model: str | None           # model_id 模糊匹配
    workload: str | None        # inference | training
    suite: str | None           # MLPerf | vendor_doc | community

@dataclass
class CompatFilters:
    chip: str | None
    model: str | None
    status: str | None          # verified | vendor_claimed | community | unsupported

@dataclass
class ProvenanceFilters:
    table_name: str | None      # chips | models | chip_model_benchmarks | chip_model_compatibility
    row_id: str | None          # 对应表里的 id
    field_name: str | None      # 字段名，模糊匹配
    source_type: str | None     # official_datasheet | official_news | paper | community | vendor_claim | benchmark_suite
    confidence: str | None      # high | medium | low
    is_official: str | None     # 0 | 1
```

---

## 命令规范

### 1. chip search

**对应表**：`chips`

**功能**：模糊搜索 + 多条件筛选 + 模型驱动自动推算显存（`--for-model`）。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--search` / `-s` | string | 否 | 模糊匹配 vendor / chip_model / chip_series / architecture |
| `--vendor` / `-v` | string | 否 | 厂商过滤 |
| `--region` / `-r` | string | 否 | domestic（国产）/ foreign（国外） |
| `--usage` / `-u` | string | 否 | train / inference / both |
| `--vram-min` | number | 否 | 最小显存 GB |
| `--vram-max` | number | 否 | 最大显存 GB |
| `--tdp-max` | number | 否 | 最大 TDP W |
| `--price-max` | number | 否 | 最高单价 万元/片 |
| `--interconnect-min` | number | 否 | 最小互联带宽 GB/s |
| `--tier` | string | 否 | datacenter / consumer / all |
| `--min-maturity` | number | 否 | 最低生态成熟度 0-5 |
| `--for-model` / `-m` | string | 否 | 模型名，自动推算显存并追加为硬约束 |
| `--scenario` | string | 否 | train / inference（配合 --for-model，默认 inference） |
| `--limit` / `-n` | number | 否 | 返回上限，默认 50 |
| `--offset` | number | 否 | 分页偏移，默认 0 |

**--for-model 逻辑**：
1. 查 models 表获取 `total_params_b`
2. 推理：`vram ≥ params × 2 × 1.25`
3. 训练：`vram ≥ params × 12 × 1.3`，追加 `usage` 过滤训练/训推一体
4. 与显式 `--vram-min` 取 max

**输出 JSON**：

```json
{
  "count": 12,
  "chips": [
    {
      "vendor": "NVIDIA",
      "vendor_display": "NVIDIA",
      "vendor_region": "foreign",
      "chip_model": "H100 SXM5 80GB",
      "chip_series": "H100",
      "chip_type": "GPU",
      "usage": "训推一体",
      "tier": "datacenter",
      "architecture": "Hopper",
      "vram_gb": "80",
      "vram_type": "HBM3",
      "vram_bw_gb_s": "3350",
      "precision_support": "FP32,FP16,BF16,FP8,INT8,INT4",
      "precision_perf": "BF16=1980TF,FP8=3960TF,INT8=3960TOPS,INT4=7920TOPS",
      "tdp_w": "700",
      "interconnect_tech": "NVLink 4.0",
      "interconnect_bw_gb_s": "900",
      "price_cny_wan": "18",
      "maturity_level": "5",
      "production_status": "已量产"
    }
  ]
}
```

**契约**：`database.search_chips(filters, limit, offset)` → JSON 序列化

**错误**：
- 0 结果：`{"count": 0, "chips": []}`，stderr: `[INFO] 0 chips matched.`
- DB 不可用：退出码 1，stderr: `[ERROR] Database not found: {path}`

---

### 2. chip profile

**对应表**：`chips`（嵌入 benchmark + compatibility + provenance）

**功能**：一张芯片的完整画像 — 78 字段 + 关联评测 + 兼容模型 + 字段溯源记录。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | **是** | 芯片名称，模糊匹配，取第一条 |

**输出 JSON**：

```json
{
  "chip": {
    "identity": {
      "id": "3",
      "vendor": "NVIDIA",
      "vendor_display": "NVIDIA",
      "vendor_region": "foreign",
      "chip_series": "H100",
      "chip_model": "H100 SXM5 80GB",
      "chip_type": "GPU",
      "usage": "训推一体",
      "tier": "datacenter"
    },
    "architecture": {
      "architecture": "Hopper",
      "process_node_nm": "4",
      "foundry": "TSMC"
    },
    "memory": {
      "vram_gb": "80",
      "vram_type": "HBM3",
      "vram_bw_gb_s": "3350"
    },
    "precision": {
      "precision_support": "FP32,FP16,BF16,FP8,INT8,INT4",
      "precision_perf": "BF16=1980TF,FP8=3960TF,INT8=3960TOPS,INT4=7920TOPS"
    },
    "pricing": {
      "price_cny_wan": "18"
    },
    "...": "(78 fields, 13 groups — only non-empty groups shown)"
  },
  "benchmarks": [
    {
      "identity": {
        "id": "1",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "Qwen/Qwen2.5-7B-Instruct"
      },
      "test_metadata": {
        "suite_name": "MLPerf v5.0",
        "workload_type": "inference",
        "scenario": "serving",
        "chip_count": "1",
        "framework": "TensorRT-LLM",
        "precision": "FP8",
        "test_date": "2025-06-01"
      },
      "inference_metrics": {
        "throughput_tok_s": "12500",
        "time_to_first_token_ms": "45",
        "memory_peak_mb": "58000"
      }
    }
  ],
  "compatibilities": [
    {
      "identity": {
        "id": "5",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "Qwen/Qwen2.5-7B-Instruct"
      },
      "compat_details": {
        "compat_status": "verified",
        "framework": "TensorRT-LLM",
        "precision": "FP8",
        "verified_at": "2025-06-01",
        "notes": "MLPerf v5.0 验证"
      }
    }
  ],
  "provenance": [
    {
      "id": "42",
      "table_name": "chips",
      "row_id": "3",
      "field_name": "price_cny_wan",
      "field_label": "参考价格(万元)",
      "new_value": "18",
      "source_type": "community",
      "source_url": "https://reddit.com/...",
      "confidence": "low",
      "is_official": "0"
    }
  ]
}
```

**契约**：`database.get_chip_profile(name)` → 返回 dict 含四部分 → JSON 序列化

**错误**：
- 未找到：退出码 1，stderr: `[ERROR] Chip not found: {name}`

---

### 3. chip recommend

**对应表**：`chips` + `models`（评分排序）

**功能**：模型 + 场景 + 约束 → 多维度评分排序，输出推荐方案。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--model` / `-m` | string | **是** | 模型名称，模糊匹配 |
| `--scenario` / `-s` | string | 否 | train / inference，默认 train |
| `--training-days` / `-d` | number | 否 | 期望训练天数 |
| `--sla-tps` | number | 否 | 目标推理吞吐 tok/s |
| `--tier` | string | 否 | 芯片级别，默认 datacenter |
| `--max-cards` | number | 否 | 最大允许卡数（硬排除） |
| `--max-price` | number | 否 | 最高单价 万元/片（硬排除） |
| `--min-maturity` | number | 否 | 最低生态成熟度 0-5（硬排除） |
| `--domestic` | bool | 否 | 优先国产（vendor_region='domestic' 评分 +3） |
| `--prefer-vendor` | string | 否 | 优先厂商（评分 +8） |
| `--limit` / `-n` | number | 否 | 返回候选数，默认 5 |

**评分流程**（内部，不定义接口）：8 步流水线 → 查模型 → 估算显存 → 候选检索 → 9 维评分 → 硬排除 → 降序 → Top-N → 组装

**输出 JSON**：

```json
{
  "model": "Qwen/Qwen2.5-7B-Instruct | Dense | 7.0B params",
  "requirements": {
    "scenario": "train",
    "min_vram_gb": 109.2,
    "target_training_days": 3.0,
    "max_cards": 8,
    "max_price_wan": 30,
    "min_maturity": null
  },
  "candidates": [
    {
      "vendor_display": "NVIDIA",
      "vendor_region": "foreign",
      "chip_series": "B200",
      "chip_model": "B200 SXM 192GB",
      "chip_type": "GPU",
      "vram_gb": "192",
      "vram_type": "HBM3e",
      "vram_bw_gb_s": "8000",
      "precision_perf": "BF16=4500TF,FP8=9000TF,FP4=18000TF,INT8=9000TOPS",
      "tdp_w": "1000",
      "interconnect_tech": "NVLink 5.0",
      "price_cny_wan": "28",
      "maturity_level": "4",
      "production_status": "已发布",
      "recommend": {
        "vram_cards": 1,
        "recommended_cards": 1,
        "estimated_training_days": 1.8,
        "meets_sla": "true",
        "total_cost_wan": 28.0,
        "score": 83.5,
        "rationale": "1卡 B200 SXM 192GB | 预计 1.8 天 | 云可用 | FP4+FP8双引擎，显存带宽8TB/s"
      }
    }
  ],
  "rejected": 5,
  "scoring_dimensions": [
    "compute_power", "card_efficiency", "price_efficiency",
    "power_efficiency", "ecosystem_maturity", "interconnect_quality",
    "sla_satisfaction", "data_quality", "production_readiness"
  ]
}
```

**契约**：
1. `database.search_models({search: model_name})` → model dict
2. 计算 min_vram_gb
3. `database.get_chip_recommend_candidates(model, scenario, tier, domestic)` → candidates
4. 评分 + 硬排除 + 排序 + 截断在 cli 层

**错误**：
- 模型未找到：退出码 1，stderr: `[ERROR] Model not found: {name}`
- 无芯片满足显存：退出码 2，stderr: `[WARN] No chip meets VRAM >= {n}GB`
- 硬排除后 0 候选：退出码 2，stderr: `[WARN] 0 chips passed hard constraints`

---

### 4. model search

**对应表**：`models`（可选 JOIN `chip_model_compatibility`）

**功能**：搜索模型 + 按芯片反查兼容模型。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--search` / `-s` | string | 否 | 模糊匹配 model_id / author |
| `--architecture` | string | 否 | dense / moe |
| `--params-min` | number | 否 | 最小参数量 B |
| `--params-max` | number | 否 | 最大参数量 B |
| `--for-chip` | string | 否 | 芯片名 → JOIN chip_model_compatibility 反查 |
| `--limit` / `-n` | number | 否 | 返回上限，默认 50 |
| `--offset` | number | 否 | 分页偏移，默认 0 |

**输出 JSON**：

```json
{
  "count": 5,
  "models": [
    {
      "model_id": "Qwen/Qwen2.5-7B-Instruct",
      "author": "Qwen",
      "architecture_family": "Dense",
      "total_params_b": "7.0",
      "downloads": "12882000",
      "likes": "1310",
      "pipeline_tag": "text-generation",
      "last_modified": "2025-06-15",
      "library_name": "transformers"
    }
  ]
}
```

**契约**：`database.search_models(filters, limit, offset)` → JSON 序列化

**错误**：
- 0 结果：`{"count": 0, "models": []}`
- --for-chip 无兼容模型：stderr: `[INFO] No compatible models for chip: {name}`

---

### 5. model profile

**对应表**：`models`（嵌入 compatible_chips + provenance）

**功能**：模型 HF 元数据 + 兼容芯片列表 + 字段溯源记录。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | **是** | 模型名称，模糊匹配，取第一条 |

**输出 JSON**：

```json
{
  "model": {
    "identity": {
      "id": "2",
      "model_id": "Qwen/Qwen2.5-7B-Instruct",
      "author": "Qwen",
      "pipeline_tag": "text-generation",
      "library_name": "transformers",
      "tags": "llm,chat,qwen"
    },
    "stats": {
      "downloads": "12882000",
      "likes": "1310",
      "last_modified": "2025-06-15"
    },
    "access": {
      "private": "false",
      "gated": "false"
    },
    "architecture": {
      "architecture_family": "Dense",
      "total_params_b": "7.0"
    },
    "meta": {
      "created_at": "2026-07-27T22:49:32",
      "updated_at": "2026-07-27T22:49:32"
    }
  },
  "compatible_chips": [
    {
      "id": "5",
      "chip_model": "H100 SXM5 80GB",
      "vendor": "NVIDIA",
      "vendor_display": "NVIDIA",
      "compat_status": "verified",
      "framework": "TensorRT-LLM",
      "precision": "FP8",
      "verified_at": "2025-03"
    }
  ],
  "provenance": [
    {
      "id": "15",
      "table_name": "models",
      "row_id": "2",
      "field_name": "total_params_b",
      "field_label": "总参数量(B)",
      "old_value": null,
      "new_value": "7.0",
      "source_type": "official_datasheet",
      "source_url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct",
      "source_detail": "config.json",
      "confidence": "high",
      "is_official": "1",
      "updated_at": "2026-06-01"
    }
  ]
}
```

**契约**：`database.get_model_profile(name)` → JSON 序列化

**错误**：
- 未找到：退出码 1，stderr: `[ERROR] Model not found: {name}`

---

### 6. benchmark search

**对应表**：`chip_model_benchmarks`

**功能**：查询芯片×模型实测数据。按芯片查 → "H100 跑过哪些模型评测"，按模型查 → "Qwen2.5-7B 在哪些芯片上测过"。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--chip` | string | 否 | chip_model 模糊匹配 |
| `--model` | string | 否 | model_id 模糊匹配 |
| `--workload` | string | 否 | inference / training |
| `--suite` | string | 否 | MLPerf / vendor_doc / community |
| `--limit` / `-n` | number | 否 | 返回上限，默认 50 |
| `--offset` | number | 否 | 分页偏移，默认 0 |

**输出 JSON**：

```json
{
  "count": 3,
  "benchmarks": [
    {
      "identity": {
        "id": "1",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "Qwen/Qwen2.5-7B-Instruct"
      },
      "test_metadata": {
        "suite_name": "MLPerf v5.0",
        "workload_type": "inference",
        "scenario": "serving",
        "task": "LLM dialogue",
        "chip_count": "1",
        "framework": "TensorRT-LLM",
        "precision": "FP8",
        "batch_size": "32",
        "input_seq_length": "2048",
        "output_seq_length": "128",
        "test_date": "2025-06-01",
        "notes": "NVIDIA 官方测试数据"
      },
      "inference_metrics": {
        "throughput_tok_s": "12500",
        "time_to_first_token_ms": "15.2",
        "inter_token_latency_ms": "8.1",
        "memory_peak_mb": "68000"
      }
    },
    {
      "identity": {
        "id": "2",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "meta-llama/Llama-3.1-8B-Instruct"
      },
      "test_metadata": {
        "suite_name": "community",
        "workload_type": "training",
        "chip_count": "8",
        "framework": "DeepSpeed",
        "precision": "BF16",
        "test_date": "2024-10"
      },
      "training_metrics": {
        "training_workload_type": "SFT",
        "mfu_pct": "52",
        "gpu_hours": "120",
        "training_tokens_T": "0.5",
        "training_gpu_count": "8"
      }
    }
  ]
}
```

**契约**：`database.search_benchmarks(filters, limit, offset)` → JSON 序列化

**错误**：
- 0 结果：`{"count": 0, "benchmarks": []}`

---

### 7. compat search

**对应表**：`chip_model_compatibility`

**功能**：查询芯片×模型兼容状态。按芯片查 → "H100 兼容哪些模型"，按模型查 → "哪些芯片能跑 Qwen2.5-7B"。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--chip` | string | 否 | chip_model 模糊匹配 |
| `--model` | string | 否 | model_id 模糊匹配 |
| `--status` | string | 否 | verified / vendor_claimed / community / unsupported |
| `--limit` / `-n` | number | 否 | 返回上限，默认 50 |
| `--offset` | number | 否 | 分页偏移，默认 0 |

**输出 JSON**：

```json
{
  "count": 3,
  "compatibilities": [
    {
      "identity": {
        "id": "5",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "Qwen/Qwen2.5-7B-Instruct"
      },
      "compat_details": {
        "compat_status": "verified",
        "framework": "TensorRT-LLM",
        "precision": "FP8",
        "verified_at": "2025-03",
        "notes": "NVIDIA 官方文档确认"
      }
    },
    {
      "identity": {
        "id": "8",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "deepseek-ai/DeepSeek-V3"
      },
      "compat_details": {
        "compat_status": "vendor_claimed",
        "framework": "vLLM",
        "precision": "FP8",
        "verified_at": "2025-06",
        "notes": "厂商声明支持，待独立验证"
      }
    },
    {
      "identity": {
        "id": "12",
        "chip_model": "H100 SXM5 80GB",
        "model_id": "mistralai/Mixtral-8x22B-Instruct-v0.1"
      },
      "compat_details": {
        "compat_status": "community",
        "framework": "Transformers",
        "precision": "FP16",
        "notes": "社区用户报告可运行"
      }
    }
  ]
}
```

**契约**：`database.search_compat(filters, limit, offset)` → JSON 序列化

**错误**：
- 0 结果：`{"count": 0, "compatibilities": []}`

---

### 8. provenance show

**对应表**：`field_provenance`（一等公民，独立可查）

**功能**：查询字段级来源追溯记录。回答 "这个字段的值从哪来的 / 什么时候改的 / 可靠吗"。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--table` / `-t` | string | 否 | 目标表名：chips / models / chip_model_benchmarks / chip_model_compatibility |
| `--row-id` | string | 否 | 目标行 id |
| `--field` / `-f` | string | 否 | 字段名，模糊匹配 |
| `--source-type` | string | 否 | official_datasheet / official_news / paper / community / vendor_claim / benchmark_suite |
| `--confidence` | string | 否 | high / medium / low |
| `--is-official` | string | 否 | 0 / 1 |
| `--limit` / `-n` | number | 否 | 返回上限，默认 50 |
| `--offset` | number | 否 | 分页偏移，默认 0 |

**典型用法**：

```bash
# 查某芯片的所有字段来源
parse1 provenance show --table chips --row-id 3

# 查某字段的变更历史
parse1 provenance show --table chips --field price_cny_wan

# 查低置信度数据
parse1 provenance show --confidence low

# 交叉：某芯片 × 精度算力 ← 来源
parse1 provenance show --table chips --row-id 3 --field precision_perf
```

**输出 JSON**：

```json
{
  "count": 2,
  "records": [
    {
      "id": "42",
      "table_name": "chips",
      "row_id": "3",
      "field_name": "price_cny_wan",
      "field_label": "参考价格(万元)",
      "old_value": null,
      "new_value": "18",
      "source_type": "community",
      "source_url": "https://reddit.com/r/nvidia/comments/...",
      "source_detail": "回帖#42",
      "confidence": "low",
      "is_official": "0",
      "updated_at": "2026-07-15",
      "notes": "众包价格信息，待官方核实"
    },
    {
      "id": "25",
      "table_name": "chips",
      "row_id": "3",
      "field_name": "vram_gb",
      "field_label": "显存容量(GB)",
      "old_value": null,
      "new_value": "80",
      "source_type": "official_datasheet",
      "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
      "source_detail": "规格表 > Memory",
      "confidence": "high",
      "is_official": "1",
      "updated_at": "2026-06-01",
      "notes": null
    }
  ]
}
```

**契约**：`database.search_provenance(filters, limit, offset)` → JSON 序列化

**错误**：
- 0 结果：`{"count": 0, "records": []}`

---

### 9. provenance stats

**对应表**：`field_provenance`（聚合视图）

**功能**：从数据治理维度看来源分布 — 每个表有多少条溯源记录？来源类型占比？置信度分布？官方 vs 社区比例？

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--table` / `-t` | string | 否 | 限定某表，不传则返回全部汇总 |

**输出 JSON**：

```json
{
  "total": 42,
  "by_table": {
    "chips": 28,
    "models": 5,
    "chip_model_benchmarks": 6,
    "chip_model_compatibility": 3
  },
  "by_source_type": {
    "official_datasheet": 18,
    "official_news": 3,
    "vendor_claim": 8,
    "paper": 2,
    "community": 6,
    "benchmark_suite": 5
  },
  "by_confidence": {
    "high": 28,
    "medium": 8,
    "low": 6
  },
  "by_is_official": {
    "official": 29,
    "unofficial": 13
  }
}
```

如果传了 `--table chips`，`by_table` 只含 chips，其余字段不变。

**契约**：`database.get_provenance_stats(table)` → JSON 序列化

---

### 10. db status

**功能**：数据库各表行数统计。

**参数**：无

**输出 JSON**：

```json
{
  "database": "芯片+模型/data.db",
  "tables": {
    "chips": 12,
    "models": 10,
    "chip_model_benchmarks": 9,
    "chip_model_compatibility": 17,
    "field_provenance": 42
  }
}
```

**契约**：`database.get_db_stats()` → JSON 序列化

---

### 11. config show / config set

**功能**：查看/修改 CLI 配置。配置保存在 `~/.parse1/config.yaml`。

**config show 输出 JSON**：

```json
{
  "db": {
    "path": "芯片+模型/data.db"
  },
  "output": {
    "default_format": "json"
  }
}
```

**config set 参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | string | **是** | 配置项（如 `db.path`） |
| `value` | string | **是** | 配置值 |

**config set 输出 JSON**：

```json
{
  "key": "db.path",
  "value": "芯片+模型/data.db",
  "status": "ok"
}
```

---

## 退出码规范

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 一般错误（DB 不可用 / 配置错误 / 实体未找到） |
| 2 | 业务级无结果（无芯片匹配 / 硬排除后候选为空） |

---

## 附录 A：precision_perf 格式

逗号分隔键值对：`BF16=1980TF,FP8=3960TF,INT8=3960TOPS,INT4=7920TOPS`

评分算力提取偏好：BF16 > FP16 > INT8（INT8 TOPS × 0.5 折算 TFLOPS）。CLI 内部实现细节。

---

## 附录 B：兼容状态枚举

| compat_status | 含义 |
|---------------|------|
| `verified` | 实测验证（MLPerf / 社区实测 / 官方测试） |
| `vendor_claimed` | 厂商声明支持，未独立实测 |
| `community` | 社区报告可运行，缺乏系统验证 |
| `unsupported` | 确认不支持 |

---

## 附录 C：来源类型枚举（field_provenance.source_type）

| source_type | 含义 |
|-------------|------|
| `official_datasheet` | 官方规格书/产品页面 |
| `official_news` | 官方新闻/博客/公告 |
| `paper` | 学术论文 |
| `community` | 社区报告（Reddit/知乎/论坛/博客） |
| `vendor_claim` | 厂商声明（未独立验证） |
| `benchmark_suite` | 基准测试套件（MLPerf 等） |

---

## 附录 D：置信度枚举（field_provenance.confidence）

| confidence | 含义 |
|------------|------|
| `high` | 官方来源 / 多源交叉验证 / 独立实测 |
| `medium` | 单一非官方来源但可信 / 行业共识 |
| `low` | 单一社区来源 / 传闻 / 待核实 |

---
## 附录 E：输出分层规范 (V4.1)

profile 和 search 类命令的 78 个字段不再扁平堆叠，而是按信息类别分层嵌套。

### chips 表：78 字段 → 13 组

| 组名 | 包含字段 | 说明 |
|------|----------|------|
| `identity` | id, vendor, vendor_display, vendor_region, chip_series, chip_model, chip_type, usage, tier | 芯片身份标识 |
| `architecture` | architecture, arch_codename, generation, process_node_nm, foundry, die_size_mm2, transistors_b, package_type, is_chiplet | 架构与制程 |
| `memory` | vram_gb, vram_type, vram_bus_bit, vram_bw_gb_s, vram_clock_mhz | 显存规格 |
| `compute_units` | compute_units, tensor_cores, rt_cores, shading_units, sm_count | 计算单元 |
| `cache` | l1_cache_kb, l2_cache_mb, on_chip_sram_mb | 缓存 |
| `precision` | precision_support, precision_perf | 精度与算力 |
| `clock_power_physical` | base_clock_mhz, boost_clock_mhz, tdp_w, max_power_w, psu_w, power_connector, board_length_mm, board_width_mm, slot_width, form_factor, bus_interface | 频率/功耗/物理规格 |
| `interconnect` | interconnect_bw_gb_s, interconnect_tech, network_interface | 互联 |
| `software` | software_stack, compatible_frameworks | 软件栈 |
| `lifecycle` | release_date, production_status, eol_date, target_market, is_released, expected_release_date, known_specs, unconfirmed_items | 生命周期与发布状态 |
| `pricing` | price_usd, price_cny_wan, price_period, price_notes | 价格 |
| `description` | description, highlights, limitations, target_workloads, typical_deployment, competitor_comparison | 芯片描述与定位 |
| `ecosystem` | ecosystem_notes, maturity_level, framework_compat, sw_stack, cuda_compat, cloud_available, cluster_scale, key_strength, key_weakness | 生态评估 |
| `meta` | created_at, updated_at | 系统时间戳（始终输出） |

### models 表：18 字段 → 6 组

| 组名 | 包含字段 |
|------|----------|
| `identity` | id, model_id, author, pipeline_tag, library_name, tags |
| `stats` | downloads, likes, last_modified |
| `access` | private, gated |
| `architecture` | architecture_family, total_params_b |
| `raw` | config_json, card_data_json, api_response_json |
| `meta` | created_at, updated_at |

### benchmarks 表：32 字段 → 5 组

| 组名 | 包含字段 |
|------|----------|
| `identity` | id, chip_model, model_id |
| `test_metadata` | suite_name, workload_type, scenario, task, hardware_config, chip_count, framework, precision, batch_size, input_seq_length, output_seq_length, concurrency, test_date, notes |
| `inference_metrics` | prefill_throughput, decode_throughput, time_to_first_token_ms, inter_token_latency_ms, memory_peak_mb, throughput_tok_s, throughput_samples_s, tpot_ms |
| `training_metrics` | mfu_pct, gpu_hours, training_tokens_T, training_gpu_count, training_workload_type |
| `meta` | created_at |

### compatibility 表：9 字段 → 3 组

| 组名 | 包含字段 |
|------|----------|
| `identity` | id, chip_model, model_id |
| `compat_details` | compat_status, framework, precision, verified_at, notes |
| `meta` | created_at |

### 分组规则

- **空组不输出**：除 `meta` 始终输出外，某组所有字段均为 NULL 时整组不出现
- **chip search / model search 不分层**：列表浏览保持扁平，分层反而降低可读性
- **profile 命令全量分层**：chip profile / model profile / benchmark search / compat search 使用分组输出

---

## 附录 F：统一格式化接口 (V4.2)

搜索/列表和画像命令的返回值通过**四个统一函数**生成，避免 CLI 和 database 层各自手写输出结构。

### chip 侧

| 函数 | 用途 | 返回结构 |
|------|------|----------|
| `chip_summary(row)` | chip search / recommend candidates (chip portion) | 13 个核心字段（扁平 dict），无子数组 |
| `chip_profile(row, benchmarks, compatibilities, provenance)` | chip profile | 14 组嵌套 chip + grouped benchmarks[] + grouped compatibilities[] + provenance[] |
| `chip_recommend_candidate(chip, ...)` | chip recommend | chip_summary + recommend 子对象（评分/卡数/rationale） |

**chip_summary 字段**（13 个）：
`vendor_display, vendor_region, chip_series, chip_model, chip_type, vram_gb, vram_type, vram_bw_gb_s, precision_perf, tdp_w, interconnect_tech, price_cny_wan, maturity_level, production_status`

### model 侧

| 函数 | 用途 | 返回结构 |
|------|------|----------|
| `model_summary(row)` | model search | 7 个核心字段（扁平 dict），无子数组 |
| `model_profile(row, compatible_chips, provenance)` | model profile | 6 组嵌套 model + compatible_chips[] + provenance[] |

**model_summary 字段**（7 个）：
`model_id, author, architecture_family, total_params_b, pipeline_tag, library_name, downloads`

### 数据流

```
database.py
  search_chips()    → SELECT * FROM chips → chip_summary() 每行 → {count, chips: [...summaries]}
  get_chip_profile() → SELECT * + benchmarks + compatibilities + provenance
                      → chip_profile(row, benches, compats, provs) → {chip:{groups}, benchmarks:[], ...}
  get_chip_recommend_candidates() → 原始行（评分用）
  chip_recommend_candidate(chip, ...) → {**chip_summary(chip), recommend: {...}}

  search_models()   → SELECT * FROM models → model_summary() 每行 → {count, models: [...summaries]}
  get_model_profile() → SELECT * + compatible_chips + provenance
                       → model_profile(row, chips, provs) → {model:{groups}, compatible_chips:[], ...}

cli.py
  chip search   → search_chips() → _print_json()           # 不加工
  chip profile  → get_chip_profile() → _print_json()       # 不加工
  chip recommend → get_chip_recommend_candidates() + _score_chip()
                 → chip_recommend_candidate() → _print_json()
  model search  → search_models() → _print_json()          # 不加工
  model profile → get_model_profile() → _print_json()      # 不加工
```

### 修改一处，全部生效

- 要改 search 返回的字段 → 只改 `_CHIP_SUMMARY_FIELDS` / `_MODEL_SUMMARY_FIELDS`
- 要改 profile 分组 → 只改 `_CHIP_FIELD_GROUPS` / `_MODEL_FIELD_GROUPS`
- 要改 recommend candidate 形状 → 只改 `chip_recommend_candidate()`
- 要改分组逻辑 → 只改 `_group_fields()`
- CLI 层不参与任何输出格式化，只负责参数解析和 `_print_json()`
