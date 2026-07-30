# Parse1 CLI — API 测试文档

> 版本: v0.5.0 | 测试日期: 2026-07-29 | 数据库: 49芯片 / 206模型 / 22测试 / 23兼容 / 5,490溯源

---

## 0. 环境与约定

```bash
# 所有命令在 芯片+模型/ 目录执行
cd 芯片+模型

# 全局选项
--db-path data.db      # 数据库路径
--version                 # 显示版本
```

---

## 1. db status — 数据库统计

### 1.1 正常输入/输出

```bash
python cli.py --db-path data.db db status
```

<details open>
<summary>输出样例</summary>

```json
{
  "database": "E:\\BUPT_PS\\P_0\\芯片+模型\\parse1\\芯片+模型\\data.db",
  "tables": {
    "chips": 26,
    "models": 204,
    "chip_model_benchmarks": 9,
    "chip_model_compatibility": 23,
    "field_provenance": 4260
  }
}
```
</details>

### 1.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `db status` (数据库存在) | 5表行数统计 | ✅ PASS |
| 2 | `--db-path nonexist.db db status` | 退出码1 + 错误信息 | ✅ PASS |

---

## 2. chip search — 芯片搜索

### 2.1 全量搜索

```bash
python cli.py --db-path data.db chip search
```

<details>
<summary>输出样例 (前3条)</summary>

```json
{
  "count": 26,
  "chips": [
    {
      "vendor_display": "NVIDIA",
      "vendor_region": "foreign",
      "chip_series": "B300",
      "chip_model": "B300 NVL16 288GB",
      "chip_type": "GPU",
      "vram_gb": "288",
      "vram_type": "HBM3e",
      "vram_bw_gb_s": "9600",
      "precision_perf": "BF16=7200TF,FP8=14400TF,FP4=28800TF",
      "tdp_w": "1400",
      "interconnect_tech": "NVLink 5.0",
      "price_cny_wan": null,
      "maturity_level": "3",
      "production_status": "已发布"
    }
  ]
}
```

</details>

### 2.2 模糊搜索

```bash
python cli.py --db-path data.db chip search --search H100
```

| 输出 | count=2, H100 NVL 94GB + H100 SXM5 80GB |

### 2.3 国产芯片筛选

```bash
python cli.py --db-path data.db chip search --region domestic
```

| 输出 | count=14, 含华为昇腾/寒武纪/壁仞/海光/沐曦/昆仑芯/天数/摩尔线程/景嘉微 |

### 2.4 多条件组合筛选

```bash
python cli.py --db-path data.db chip search --vram-min 80 --vram-max 96 --tdp-max 400 --tier datacenter
```

| 输出 | count=3: H100 NVL 94GB (400W), A100 SXM4 80GB (400W), MLU590 80GB (250W) |

### 2.5 模型驱动VRAM推算 (训练)

```bash
python cli.py --db-path data.db chip search --for-model "Qwen2.5-7B" --scenario train
```

推算: 7B × 12 × 1.3 = 109.2GB VRAM下限
| 输出 | count=9 (≥109.2GB且支持训练的芯片) |

### 2.6 模型驱动 + 手动VRAM叠加

```bash
python cli.py --db-path data.db chip search --for-model "Qwen2.5-7B" --scenario train --vram-min 200
```

应取 max(auto=109.2, manual=200) = 200
| 输出 | count=2: B300 NVL16 288GB + Instinct MI350X 288GB | ✅ PASS |

### 2.7 按用途筛选

```bash
# 训练
python cli.py --db-path data.db chip search --usage train     # → 23 (训推一体 + 训练)
# 推理
python cli.py --db-path data.db chip search --usage inference  # → 26 (训推一体 + 推理)
# 训推一体
python cli.py --db-path data.db chip search --usage both       # → 23 (仅训推一体)
```

### 2.8 价格/生态/互联筛选

```bash
python cli.py --db-path data.db chip search --price-max 10          # ≤10万/片
python cli.py --db-path data.db chip search --min-maturity 3        # 成熟度≥3
python cli.py --db-path data.db chip search --interconnect-min 600  # 互联带宽≥600GB/s
```

### 2.9 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `chip search` (无参数) | 返回全部26芯片 | ✅ PASS |
| 2 | `--search H100` | 2条，H100 NVL + H100 SXM5 | ✅ PASS |
| 3 | `--search 昇腾` | 2条，910B + 910C | ✅ PASS |
| 4 | `--region domestic` | 14国产芯片 | ✅ PASS |
| 5 | `--region foreign` | 12国外芯片 | ✅ PASS |
| 6 | `--vram-min 80 --vram-max 96 --tdp-max 400 --tier datacenter` | 3条 | ✅ PASS |
| 7 | `--for-model "Qwen2.5-7B" --scenario train` | 9条，≥109.2GB VRAM | ✅ PASS |
| 8 | `--for-model "NONEXIST"` | count=0 + stderr info | ✅ PASS (已修复) |
| 9 | `--for-model "Qwen2.5-7B" --scenario train --vram-min 200` | VRAM取max，2条 | ✅ PASS |
| 10 | `--usage train` | 23条 (训推一体 + 训练) | ✅ PASS |
| 11 | `--usage inference` | 26条 (训推一体 + 推理) | ✅ PASS (已修复) |
| 12 | `--vram-min 9999` | count=0 + stderr info | ✅ PASS |
| 13 | `--price-max 10` | 19条 (含NULL值) | ✅ PASS |
| 14 | `--min-maturity 3` | 19条 | ✅ PASS |

---

## 3. chip profile — 芯片画像

### 3.1 正常输入/输出

```bash
python cli.py --db-path data.db chip profile "H100 SXM5"
```

<details>
<summary>输出样例</summary>

```json
{
  "chip": {
    "identity": {
      "id": 1,
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
      "arch_codename": "GH100",
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
    "pricing": { "price_cny_wan": "18" },
    "ecosystem": { "maturity_level": "5", "cloud_available": "1" }
  },
  "benchmarks": [
    {
      "identity": { "id": 1, "chip_model": "H100 SXM5 80GB", "model_id": "Qwen/Qwen2.5-7B-Instruct" },
      "test_metadata": { "suite_name": "MLPerf Inference v5.0", "workload_type": "inference", "scenario": "serving", "framework": "TensorRT-LLM", "precision": "FP8" },
      "inference_metrics": { "throughput_tok_s": "12500", "time_to_first_token_ms": "15.2", "memory_peak_mb": "68000" }
    }
  ],
  "compatibilities": [
    {
      "identity": { "id": 5, "chip_model": "H100 SXM5 80GB", "model_id": "Qwen/Qwen2.5-7B-Instruct" },
      "compat_details": { "compat_status": "verified", "framework": "TensorRT-LLM", "precision": "FP8", "verified_at": "2025-03" }
    }
  ],
  "field_provenance": {
    "price_cny_wan": {
      "field_label": "price_cny_wan",
      "current_value": "18",
      "update_count": 1,
      "history": [
        {
          "old_value": null,
          "new_value": "18",
          "source_type": "community",
          "source_url": "https://reddit.com/...",
          "source_detail": "",
          "confidence": "low",
          "is_official": "0",
          "updated_at": "2026-07-15",
          "notes": null
        }
      ]
    }
  }
}
```

</details>

**结构**: chip (13组78字段) + benchmarks[] + compatibilities[] + field_provenance (按字段分组的历史)

### 3.2 英文别名自动映射

```bash
python cli.py --db-path data.db chip profile "Ascend"    # → 昇腾910C (OAM 128GB)
```

### 3.3 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `chip profile "H100 SXM5"` | 13组 + 3benchmarks + 6compat + 62溯源 | ✅ PASS |
| 2 | `chip profile "不存在的芯片"` | 退出码1 + "[ERROR] Chip not found" | ✅ PASS |
| 3 | `chip profile "Ascend"` | English→Chinese alias fallback → 昇腾910C | ✅ PASS |
| 4 | `chip profile "昆仑"` | 昆仑芯100 | ✅ PASS |
| 5 | `chip profile "MLU590"` | 寒武纪MLU590 | ✅ PASS |

---

## 4. chip recommend — 芯片推荐

### 4.1 训练推荐 (基础)

```bash
python cli.py --db-path data.db chip recommend -m "Qwen2.5-7B" -s train -d 3 -n 5
```

<details>
<summary>输出样例</summary>

```json
{
  "model": "Qwen/Qwen2.5-7B-Instruct | Dense | 7.0B params",
  "requirements": {
    "scenario": "train",
    "min_vram_gb": 109.2,
    "target_training_days": 3.0,
    "target_tokens_per_sec": null,
    "max_cards": null,
    "max_price_wan": null,
    "min_maturity": null
  },
  "candidates": [
    {
      "vendor_display": "NVIDIA",
      "vendor_region": "foreign",
      "chip_series": "B300",
      "chip_model": "B300 NVL16 288GB",
      "chip_type": "GPU",
      "vram_gb": "288",
      "vram_type": "HBM3e",
      "vram_bw_gb_s": "9600",
      "precision_perf": "BF16=7200TF,FP8=14400TF,FP4=28800TF",
      "tdp_w": "1400",
      "interconnect_tech": "NVLink 5.0",
      "price_cny_wan": null,
      "maturity_level": "3",
      "production_status": "已发布",
      "recommend": {
        "vram_cards": 1,
        "recommended_cards": 1,
        "estimated_training_days": 1.8,
        "meets_sla": "true",
        "total_cost_wan": null,
        "score": 103.9,
        "rationale": "1卡 B300 NVL16 288GB | 预计 1.8 天 | 288GB显存+28.8PFLOPS FP4"
      }
    }
  ],
  "rejected": 18,
  "scoring_dimensions": [
    "compute_power", "card_efficiency", "price_efficiency",
    "power_efficiency", "ecosystem_maturity", "interconnect_quality",
    "sla_satisfaction", "data_quality", "production_readiness"
  ]
}
```

</details>

**评分排名** (Qwen2.5-7B 训练3天):
| 排名 | 芯片 | 分数 | 卡数 | 预估天数 |
|------|------|------|------|----------|
| 1 | B300 NVL16 288GB | 103.9 | 1 | 1.8 |
| 2 | B200 SXM 192GB | 83.5 | 1 | 1.8 |
| 3 | Instinct MI350X 288GB | 72.4 | 1 | 1.8 |
| 4 | Instinct MI300X 192GB | 64.74 | 2 | 2.1 |
| 5 | H100 SXM5 80GB | 59.42 | 2 | 0.9 |

### 4.2 推理推荐 (SLA)

```bash
python cli.py --db-path data.db chip recommend -m "Qwen2.5-7B" -s inference --sla-tps 5000
```

| 输出 | 5候选，B300(score=99), B200(79.3), MI350X(68.2), MI300X(57.9), H100(51.9) |

### 4.3 国产优先

```bash
python cli.py --db-path data.db chip recommend -m "Qwen2.5-7B" -s train -d 3 --domestic -n 5
```

| 排名 | 芯片 | 分数 | 说明 |
|------|------|------|------|
| 1 | 昇腾910C | 45.44 | +3分国产加成 |
| 2 | BR100 (壁砺100) | 40.95 | +3分国产加成 |
| 3 | 昇腾910B | 35.47 | +3分国产加成 |
| 4 | MLU590 | 32.40 | +3分国产加成 |
| 5 | 海光C500 | 25.14 | +3分国产加成 |

### 4.4 厂商偏好 (+8分)

```bash
python cli.py --db-path data.db chip recommend -m "Qwen2.5-7B" -s train -d 3 --prefer-vendor "NVIDIA" -n 5
```

| NVIDIA芯片占据前3: B300(111.9), B200(91.5), H100(67.4) |

### 4.5 硬约束排除

```bash
python cli.py --db-path data.db chip recommend -m "Qwen2.5-7B" -s train -d 3 --max-cards 1 --max-price 15 -n 5
```

| 输出 | 3候选 (仅单卡+≤15万的芯片) |

### 4.6 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `-m "Qwen2.5-7B" -s train -d 3` | 5候选，B300第一(>100分) | ✅ PASS |
| 2 | `-m "Qwen2.5-7B" -s inference --sla-tps 5000` | 5候选，B300第一(99分) | ✅ PASS |
| 3 | `-m "Qwen2.5-7B" -s train --domestic` | 昇腾910C第一，全部国产 | ✅ PASS |
| 4 | `-m "Qwen2.5-7B" -s train --prefer-vendor "NVIDIA"` | NVIDIA芯片+8分，前三全是NVIDIA | ✅ PASS |
| 5 | `-m "Qwen2.5-7B" -s train -d 3 --max-cards 1 --max-price 15` | 3候选，全部单卡且≤15万 | ✅ PASS |
| 6 | `-m "Qwen2.5-72B" -s train -d 7` | 大模型，更少候选 | ✅ PASS |
| 7 | `-m "不存在的模型" -s train` | 退出码1 + "[ERROR] Model not found" | ✅ PASS |

---

## 5. model search — 模型搜索

### 5.1 全量/模糊搜索

```bash
python cli.py --db-path data.db model search                          # 全量204
python cli.py --db-path data.db model search --search Qwen --limit 5  # Qwen系列43
```

<details>
<summary>输出样例</summary>

```json
{
  "count": 43,
  "models": [
    {
      "model_id": "Qwen/Qwen3-0.6B",
      "author": "Qwen",
      "architecture_family": "Dense",
      "total_params_b": "0.8",
      "pipeline_tag": "text-generation",
      "library_name": "transformers",
      "downloads": "12,345,678"
    }
  ]
}
```

</details>

### 5.2 按架构筛选

```bash
python cli.py --db-path data.db model search --architecture moe  # → 13个MoE模型
python cli.py --db-path data.db model search --architecture dense  # → 191个Dense模型
```

### 5.3 按参数量范围

```bash
python cli.py --db-path data.db model search --params-min 70    # ≥70B大模型
python cli.py --db-path data.db model search --params-min 30 --params-max 100  # 30-100B
```

### 5.4 按芯片反查兼容模型

```bash
python cli.py --db-path data.db model search --for-chip "H100"       # → 5个兼容模型
python cli.py --db-path data.db model search --for-chip "昇腾"        # → 3个
python cli.py --db-path data.db model search --for-chip "Ascend"     # → 3个 (English alias)
```

### 5.5 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `model search` | 全量204 | ✅ PASS |
| 2 | `--search Qwen` | 43条 | ✅ PASS |
| 3 | `--architecture moe` | 13条 | ✅ PASS |
| 4 | `--architecture dense` | 191条 | ✅ PASS |
| 5 | `--params-min 70` | 47条(≥70B) | ✅ PASS |
| 6 | `--for-chip H100` | 5条兼容 | ✅ PASS |
| 7 | `--for-chip Ascend` | 3条 (English alias → 昇腾) | ✅ PASS (已修复) |
| 8 | `--for-chip NONEXIST` | count=0 + stderr info | ✅ PASS |

---

## 6. model profile — 模型画像

### 6.1 正常输入/输出

```bash
python cli.py --db-path data.db model profile "Qwen2.5-7B"
```

<details>
<summary>输出样例</summary>

```json
{
  "model": {
    "identity": {
      "id": 2,
      "model_id": "Qwen/Qwen2.5-7B-Instruct",
      "author": "Qwen",
      "pipeline_tag": "text-generation",
      "library_name": "transformers",
      "tags": "transformers,safetensors,qwen2,text-generation,chat,..."
    },
    "stats": { "downloads": "12882000", "likes": "1310", "last_modified": "2025-06-15" },
    "access": { "private": "false", "gated": "false" },
    "architecture": { "architecture_family": "Dense", "total_params_b": "7.0" },
    "raw": {
      "config_json": "{\"architectures\":...}",
      "card_data_json": "{\"language\":\"en\",\"license\":\"apache-2.0\",...}",
      "api_response_json": "{...8079字节...}"
    }
  },
  "compatible_chips": [
    {
      "id": 5, "chip_model": "H100 SXM5 80GB", "vendor": "NVIDIA",
      "compat_status": "verified", "framework": "TensorRT-LLM", "precision": "FP8"
    }
  ],
  "field_provenance": {
    "total_params_b": {
      "field_label": "total_params_b",
      "current_value": "7.0",
      "update_count": 1,
      "history": [
        {
          "old_value": null,
          "new_value": "7.0",
          "source_type": "huggingface_api",
          "source_url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct",
          "source_detail": "",
          "confidence": "high",
          "is_official": "1",
          "updated_at": "2026-07-28T19:54:48",
          "notes": null
        }
      ]
    }
  }
}
```

</details>

### 6.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `model profile "Qwen2.5-7B"` | 6组 + 9兼容芯片 + 15溯源 | ✅ PASS |
| 2 | `model profile "DeepSeek-V3"` | MoE模型，671B参数 | ✅ PASS |
| 3 | `model profile "不存在的模型"` | 退出码1 + "[ERROR] Model not found" | ✅ PASS |

---

## 7. benchmark search — 评测数据搜索

### 7.1 正常输入/输出

```bash
# 按芯片查评测
python cli.py --db-path data.db benchmark search --chip H100
# 按模型查评测
python cli.py --db-path data.db benchmark search --model Qwen
# 按类型 + 套件
python cli.py --db-path data.db benchmark search --workload training
python cli.py --db-path data.db benchmark search --suite MLPerf
```

<details>
<summary>输出样例</summary>

```json
{
  "count": 3,
  "benchmarks": [
    {
      "identity": { "id": 1, "chip_model": "H100 SXM5 80GB", "model_id": "Qwen/Qwen2.5-7B-Instruct" },
      "test_metadata": {
        "suite_name": "MLPerf Inference v5.0",
        "workload_type": "inference",
        "scenario": "serving",
        "task": "LLM dialogue",
        "chip_count": "1",
        "framework": "TensorRT-LLM",
        "precision": "FP8",
        "batch_size": "32",
        "test_date": "2025-06-01"
      },
      "inference_metrics": {
        "throughput_tok_s": "12500",
        "time_to_first_token_ms": "15.2",
        "inter_token_latency_ms": "8.1",
        "memory_peak_mb": "68000"
      }
    }
  ]
}
```

</details>

### 7.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `--chip H100` | 3条 (MLPerf推理×2 + 训练×1) | ✅ PASS |
| 2 | `--model Qwen` | 6条 (含国产芯片实测) | ✅ PASS |
| 3 | `--workload training` | 2条训练 | ✅ PASS |
| 4 | `--workload inference` | 7条推理 | ✅ PASS |
| 5 | `--suite MLPerf` | 4条 | ✅ PASS |
| 6 | `--chip NONEXIST` | count=0 | ✅ PASS |

---

## 8. compat search — 兼容性查询

### 8.1 正常输入/输出

```bash
python cli.py --db-path data.db compat search --chip H100           # H100兼容所有模型
python cli.py --db-path data.db compat search --status verified     # 仅实测验证的
```

<details>
<summary>输出样例</summary>

```json
{
  "count": 15,
  "compatibilities": [
    {
      "identity": { "id": 5, "chip_model": "H100 SXM5 80GB", "model_id": "deepseek-ai/DeepSeek-V3" },
      "compat_details": {
        "compat_status": "verified",
        "framework": "vLLM",
        "precision": "FP8",
        "verified_at": "2025-06",
        "notes": "社区实测验证，吞吐达 1200 tok/s"
      }
    }
  ]
}
```

</details>

### 8.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `--chip H100` | 7条 | ✅ PASS |
| 2 | `--status verified` | 15条verified | ✅ PASS |
| 3 | `--status vendor_claimed` | vendor_claimed条目 | ✅ PASS |
| 4 | `--status unsupported` | unsupported条目 | ✅ PASS |
| 5 | `--model Qwen2.5-7B` | 9条兼容关系 | ✅ PASS |

---

## 9. provenance show — 来源追溯查询

### 9.1 正常输入/输出

```bash
# 查某芯片的所有字段来源
python cli.py --db-path data.db provenance show --table chips --row-id 1 --limit 10
# 查低置信度数据
python cli.py --db-path data.db provenance show --confidence low --limit 5
# 查价格字段来源
python cli.py --db-path data.db provenance show --table chips --row-id 1 --field price
```

<details>
<summary>输出样例</summary>

```json
{
  "count": 62,
  "records": [
    {
      "id": 1,
      "table_name": "chips",
      "row_id": "1",
      "field_name": "vendor",
      "field_label": "厂商",
      "old_value": null,
      "new_value": "NVIDIA",
      "source_type": "official_datasheet",
      "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
      "source_detail": "产品规格页",
      "confidence": "high",
      "is_official": "1",
      "updated_at": "2026-07-27T22:49:30",
      "notes": null
    }
  ]
}
```

</details>

### 9.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `--table chips --row-id 1` | 62条(H100所有字段) | ✅ PASS |
| 2 | `--confidence low` | 80条低置信度 | ✅ PASS |
| 3 | `--source-type official_datasheet` | 3,477条 | ✅ PASS |
| 4 | `--table chips --row-id 1 --field precision_perf` | 精度算力相关 | ✅ PASS |
| 5 | `--table NONEXIST` | count=0 | ✅ PASS |

---

## 10. provenance stats — 来源统计

### 10.1 正常输入/输出

```bash
python cli.py --db-path data.db provenance stats           # 全量统计
python cli.py --db-path data.db provenance stats --table chips  # 仅芯片统计
```

<details>
<summary>输出样例 (全量)</summary>

```json
{
  "total": 4260,
  "by_table": {
    "chips": 906,
    "models": 3060,
    "chip_model_benchmarks": 145,
    "chip_model_compatibility": 149
  },
  "by_source_type": {
    "official_datasheet": 3477,
    "community": 265,
    "vendor_claim": 261,
    "official_news": 137,
    "benchmark_suite": 120
  },
  "by_confidence": {
    "high": 3818,
    "medium": 362,
    "low": 80
  },
  "by_is_official": {
    "official": 4001,
    "unofficial": 259
  }
}
```

</details>

### 10.2 统计分析

| 维度 | 数据 | 解读 |
|------|------|------|
| 官方 vs 社区 | 4001 vs 259 (94%官方) | 数据库来源可信度较高 |
| 置信度 | high 90%, medium 8%, low 2% | 80条低置信度需关注 |
| 主要为 datasheet | 82% | 规格来自官方文档 |
| models表最大 | 3060条(72%) | 204个模型×15字段=3060，一致 |

### 10.3 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `provenance stats` (无参数) | 5表汇总，4维度聚合 | ✅ PASS |
| 2 | `--table chips` | 仅chips表，906条 | ✅ PASS |
| 3 | `--table models` | 仅models表，3060条 | ✅ PASS |

---

## 11. config show / config set

### 11.1 正常输入/输出

```bash
python cli.py --db-path data.db config show
```

```json
{
  "profiles": {
    "default_chip_format": "default",
    "default_model_format": "default"
  },
  "db": { "path": "" },
  "output": { "default_format": "yaml" }
}
```

```bash
python cli.py --db-path data.db config set db.path "data.db"
# → {"key": "db.path", "value": "data.db", "status": "ok"}
```

### 11.2 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `config show` | 3个section | ✅ PASS |
| 2 | `config set db.path "test.db"` | status=ok | ✅ PASS |

---

## 12. 错误码规范

| 退出码 | 含义 | 触发场景 |
|--------|------|----------|
| 0 | 成功 | 正常执行（包括0结果的搜索） |
| 1 | 一般错误 | DB不可用 / 实体未找到 (profile) / 模型未找到 (recommend) |
| 2 | 业务无结果 | 硬约束排除后0候选 (recommend) |

---

## 13. Bug 修复记录

### Bug #1: `--for-model` 对未知模型静默返回全部芯片

- **严重程度**: 中
- **现象**: 输入 `--for-model "不存在的模型"` 返回全部26芯片，无任何警告
- **根因**: `search_chips()` 中 model 查询失败时跳过VRAM限制，无条件回退
- **修复**: 模型未找到时插入 `1=0` 条件强制返回空结果
- **文件**: [database.py:121-125](芯片+模型/database.py#L121-125)

### Bug #2: `--usage inference` 不包含训推一体芯片

- **严重程度**: 中
- **现象**: `--usage inference` 仅匹配3个usage="推理"的芯片，遗漏23个usage="训推一体"
- **根因**: 单一 `LIKE "%推理%"` 不匹配 `"训推一体"`
- **修复**: `inference` 匹配 `"%训推%" OR "%推理%"`；`train` 匹配 `"%训推%" OR "%训练%"`
- **文件**: [database.py:128-140](芯片+模型/database.py#L128-140)

### Bug #3: `--for-chip` 不支持英文别名

- **严重程度**: 低
- **现象**: `model search --for-chip "Ascend"` 返回0结果，而 `--for-chip "昇腾"` 返回3个
- **根因**: `search_models()` 缺少英文→中文别名映射
- **修复**: 复用 `get_chip_profile()` 的 alias_map，生成多条 `LIKE` 条件
- **文件**: [database.py:428-440](芯片+模型/database.py#L428-440)

---

## 14. 数据状态总览

| 表 | 行数 | 说明 |
|---|---|---|
| chips | 26 | NVIDIA×6, AMD×2, Intel×1, Google×1, AWS×1, Microsoft×1, 华为×2, 寒武纪×3, 壁仞×2, 沐曦×2, 海光×2, 天数×1, 摩尔线程×1, 昆仑芯×1, 景嘉微×1 |
| models | 204 | LLM×51, VLM×31, Embedding×16, BERT×18, Audio×11, 其他×77 |
| chip_model_benchmarks | 9 | MLPerf + 社区 推理/训练实测 |
| chip_model_compatibility | 23 | verified×15, vendor_claimed×7, community×1 |
| field_provenance | 4,260 | 94%官方来源, 90%高置信度 |

---

## 15. 完整测试套件命令

```bash
# 一键运行所有测试 (在 芯片+模型/ 目录)
cd 芯片+模型
DB="--db-path data.db"

# === db ===
python cli.py $DB db status

# === chip ===
python cli.py $DB chip search
python cli.py $DB chip search --search H100
python cli.py $DB chip search --region domestic
python cli.py $DB chip search --vram-min 80 --vram-max 96 --tdp-max 400 --tier datacenter
python cli.py $DB chip search --for-model "Qwen2.5-7B" --scenario train
python cli.py $DB chip search --for-model "NONEXIST"              # Bug #1 fix verify
python cli.py $DB chip search --usage train
python cli.py $DB chip search --usage inference                    # Bug #2 fix verify
python cli.py $DB chip search --usage both
python cli.py $DB chip search --vram-min 9999                      # 0 results
python cli.py $DB chip profile "H100 SXM5"
python cli.py $DB chip profile "Ascend"                            # alias
python cli.py $DB chip recommend -m "Qwen2.5-7B" -s train -d 3 -n 5
python cli.py $DB chip recommend -m "Qwen2.5-7B" -s train --domestic -n 5
python cli.py $DB chip recommend -m "Qwen2.5-7B" -s inference --sla-tps 5000
python cli.py $DB chip recommend -m "Qwen2.5-7B" -s train -d 3 --prefer-vendor "NVIDIA" -n 5
python cli.py $DB chip recommend -m "Qwen2.5-7B" -s train -d 3 --max-cards 1 --max-price 15

# === model ===
python cli.py $DB model search
python cli.py $DB model search --search Qwen --limit 5
python cli.py $DB model search --architecture moe
python cli.py $DB model search --params-min 70
python cli.py $DB model search --for-chip H100
python cli.py $DB model search --for-chip Ascend                    # Bug #3 fix verify
python cli.py $DB model profile "Qwen2.5-7B"

# === benchmark ===
python cli.py $DB benchmark search --chip H100
python cli.py $DB benchmark search --model Qwen
python cli.py $DB benchmark search --workload training
python cli.py $DB benchmark search --suite MLPerf

# === compat ===
python cli.py $DB compat search --chip H100
python cli.py $DB compat search --status verified
python cli.py $DB compat search --model Qwen2.5-7B

# === provenance ===
python cli.py $DB provenance show --table chips --row-id 1 --limit 10
python cli.py $DB provenance show --confidence low --limit 5
python cli.py $DB provenance stats
python cli.py $DB provenance stats --table chips

# === config ===
python cli.py $DB config show
```

---

## 16. 接口字段溯源 — field_provenance

> 新增于 2026-07-29 | 覆盖全部 API 接口

### 16.1 设计说明

每个字段的来源不再是平铺列表，而是**按字段名分组的完整变更历史**：

- **Profile 接口**（`/chips/{id}`, `/models/{id}`）— `field_provenance` 自动返回，每个字段包含当前值 + 变更次数 + 按时间倒序的完整历史
- **搜索接口**（`/chips`, `/models`, `/benchmarks`, `/compat`）— 传 `?include_provenance=true` 返回 `_provenance` 紧凑摘要

### 16.2 Profile 接口 — 字段来源历史

```bash
# 芯片画像 — field_provenance 自动包含
curl -s http://localhost:8000/api/v1/chips/1 | python -m json.tool | head -80
```

<details>
<summary>field_provenance 结构样例 (H100 SXM5 80GB)</summary>

```json
{
  "chip": { "identity": {...}, "architecture": {...}, "memory": {...} },
  "benchmarks": [...],
  "compatibilities": [...],
  "field_provenance": {
    "vendor": {
      "field_label": "vendor",
      "current_value": "NVIDIA",
      "update_count": 1,
      "history": [
        {
          "old_value": null,
          "new_value": "NVIDIA",
          "source_type": "official_datasheet",
          "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
          "source_detail": "产品规格页",
          "confidence": "high",
          "is_official": "1",
          "updated_at": "2026-07-28T19:54:48",
          "notes": null
        }
      ]
    },
    "vram_gb": {
      "field_label": "vram_gb",
      "current_value": "80",
      "update_count": 1,
      "history": [
        {
          "old_value": null,
          "new_value": "80",
          "source_type": "official_datasheet",
          "source_url": "https://www.nvidia.com/en-us/data-center/h100/",
          "source_detail": "",
          "confidence": "high",
          "is_official": "1",
          "updated_at": "2026-07-28T19:54:48",
          "notes": null
        }
      ]
    }
  }
}
```
</details>

**多版本变更示例**（同一字段被多次修改时，history 包含全部记录）：

```json
{
  "vram_gb": {
    "field_label": "显存",
    "current_value": "24",
    "update_count": 2,
    "history": [
      {
        "old_value": "5",
        "new_value": "24",
        "source_type": "official_datasheet",
        "source_url": "https://example.com/specs",
        "source_detail": "spec page",
        "confidence": "high",
        "is_official": "1",
        "updated_at": "2026-07-29T12:00:00",
        "notes": "纠正数据错误: 5GB→24GB"
      },
      {
        "old_value": null,
        "new_value": "5",
        "source_type": "vendor_claim",
        "source_url": "https://example.com/old-press",
        "source_detail": "press release",
        "confidence": "medium",
        "is_official": "0",
        "updated_at": "2026-07-01T10:00:00",
        "notes": "初始录入"
      }
    ]
  }
}
```

### 16.3 搜索接口 — 紧凑溯源摘要

```bash
# 芯片搜索 + 来源摘要
curl -s "http://localhost:8000/api/v1/chips?limit=2&include_provenance=true"

# 模型搜索 + 来源摘要
curl -s "http://localhost:8000/api/v1/models?limit=2&include_provenance=true"

# 评测搜索 + 来源摘要
curl -s "http://localhost:8000/api/v1/benchmarks?limit=2&include_provenance=true"

# 兼容性搜索 + 来源摘要
curl -s "http://localhost:8000/api/v1/compat?limit=2&include_provenance=true"
```

<details>
<summary>_provenance 摘要结构样例</summary>

```json
{
  "count": 49,
  "chips": [
    {
      "chip_model": "B300 NVL16 288GB",
      "vram_gb": "288",
      "tdp_w": "1400",
      "_provenance": {
        "field_count": 37,
        "record_count": 37,
        "sources": [
          { "type": "official_datasheet", "count": 37 }
        ],
        "confidence": { "high": 37 },
        "last_updated": "2026-07-28T19:54:48"
      }
    }
  ]
}
```
</details>

### 16.4 模型 Profile — field_provenance

```bash
curl -s http://localhost:8000/api/v1/models/1 | python -m json.tool | head -60
```

<details>
<summary>输出样例</summary>

```json
{
  "model": { "identity": {...}, "stats": {...}, "architecture": {...} },
  "compatible_chips": [...],
  "field_provenance": {
    "model_id": {
      "field_label": "model_id",
      "current_value": "Qwen/Qwen3-8B",
      "update_count": 1,
      "history": [
        {
          "old_value": null,
          "new_value": "Qwen/Qwen3-8B",
          "source_type": "huggingface_api",
          "source_url": "https://huggingface.co/api/models/Qwen/Qwen3-8B",
          "source_detail": "HF API response",
          "confidence": "high",
          "is_official": "1",
          "updated_at": "2026-07-28T19:54:48",
          "notes": null
        }
      ]
    }
  }
}
```
</details>

### 16.5 测试用例

| # | 输入 | 预期 | 状态 |
|---|------|------|------|
| 1 | `GET /api/v1/chips/1` | 返回 `field_provenance`（非旧 `provenance`），含 62 个字段分组 | ✅ PASS |
| 2 | `GET /api/v1/chips/1` → `field_provenance.vendor` | 含 `current_value`、`update_count`、`history[]` 数组 | ✅ PASS |
| 3 | `GET /api/v1/chips/1` → `field_provenance.*.history[]` | 每条含 `old_value/new_value/source_type/source_url/confidence/is_official/updated_at` | ✅ PASS |
| 4 | `GET /api/v1/models/1` | 返回 `field_provenance`，含 15 个字段分组 | ✅ PASS |
| 5 | `GET /api/v1/chips?include_provenance=true` | 每个芯片带 `_provenance` 摘要 | ✅ PASS |
| 6 | `GET /api/v1/models?include_provenance=true` | 每个模型带 `_provenance` 摘要 | ✅ PASS |
| 7 | `GET /api/v1/benchmarks?include_provenance=true` | 每条评测带 `_provenance` 摘要 | ✅ PASS |
| 8 | `GET /api/v1/compat?include_provenance=true` | 每条兼容记录带 `_provenance` 摘要 | ✅ PASS |
| 9 | `GET /api/v1/chips`（不带参数） | 不含 `_provenance` 键（默认不加载，不影响性能） | ✅ PASS |
| 10 | Profile 接口旧 `provenance` 键 | 已替换为 `field_provenance` 分组格式 | ✅ PASS |

### 16.6 前端展示

在芯片/模型详情弹窗底部有「📋 字段来源追溯」区域（默认折叠），按字段展示：

- 字段中文名 + 字段英文名
- 变更次数标签（多次变更标橙色）
- 每条历史记录：`new_value ← old_value` + 来源 URL + 来源类型 + 置信度 + 是否官方 + 时间

### 16.7 与旧格式的差异

| 旧格式 (`provenance`) | 新格式 (`field_provenance`) |
|---|---|
| 平铺列表，62 条记录混在一起 | 按字段名分组，62 个独立字段 |
| 看不到字段修改次数 | `update_count` 直接显示 |
| 需要手动翻找同字段的历史 | `history[]` 数组内聚，按时间倒序 |
| 相同字段多次修改散落各处 | 集中在同一个 `history[]` 下 |
