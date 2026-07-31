# AI 芯片推荐引擎 v2.0 — 设计与评分方案

> 最后更新: 2026-07-30 | 版本: v2.0-draft

---

## 目录

1. [概述与设计目标](#1-概述与设计目标)
2. [推荐引擎流水线](#2-推荐引擎流水线)
3. [需求建模与卡数估算](#3-需求建模与卡数估算)
4. [评分体系 (10维)](#4-评分体系-10维)
5. [权重配置与总分计算](#5-权重配置与总分计算)
6. [实测 Benchmark 增强](#6-实测-benchmark-增强)
7. [推理场景 SLA 评估](#7-推理场景-sla-评估)
8. [API 响应格式](#8-api-响应格式)
9. [前端展示方案](#9-前端展示方案)
10. [文档与透明度](#10-文档与透明度)

---

## 1. 概述与设计目标

### 1.1 当前问题

| 问题 | 说明 |
|---|---|
| 评分不透明 | 用户只看到总分，不知道每个维度贡献了多少 |
| 维度固定 | 权重硬编码，不可调整 |
| 缺少训练数据量 | 只有 `training_days`，没有 `training_tokens`，无法准确估算算力需求 |
| 卡效率反直觉 | "卡越少分越高" 不合理 |
| 推理场景薄弱 | 没有吞吐 SLA，只有 VRAM 约束 |
| 未利用实测数据 | chip_model_benchmarks 有 1287 条推理 + 16 条训练实测，完全没用到 |
| 没有归一化 | 得分可以是负数或 50+，无法直观理解 |

### 1.2 v2.0 目标

1. **10 维可量化评分**：每个维度 0-10 分，有明确的公式、单位、常数
2. **训练数据量参数**：新增 `training_tokens` (T tokens)，范围 0.01-1000
3. **分维度明细**：每颗候选芯片返回 10 个维度的具体得分和计算过程
4. **实测数据加权**：有 benchmark 实测时可信度加权提升
5. **推理 SLA**：基于实测 tok/s 反推所需卡数
6. **文档化**：前端可查看的算法说明

---

## 2. 推荐引擎流水线

```
用户输入: 模型 + 场景 + 约束
    │
    ▼
┌─────────────────────────────┐
│ Phase 1: 需求建模           │
│  总参数量、architecture     │
│  训练数据量(T tokens)        │
│  推算 VRAM / FLOPs 总需求   │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 2: 候选筛选 (硬约束)   │
│  VRAM >= vram_per_card       │
│  chip_type ∈ {GPU,NPU,...}   │
│  tier = datacenter (默认)    │
│  usage 匹配场景               │
│  排除未发布(is_released=0)   │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 3: 卡数估算           │
│  vram_cards (显存约束)       │
│  compute_cards (算力约束)    │
│  sla_cards (SLA约束)         │
│  recommended = max(三者)     │
│  全部取 2 的幂次方           │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 4: 硬约束排除          │
│  min_cards <= cards <= max   │
│  price <= max_price           │
│  maturity >= min_maturity     │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 5: Benchmark 增强      │
│  匹配实测数据 → MFU修正      │
│  推理吞吐校准                 │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 6: 10维评分            │
│  每维 0-10，加权求和 = 总分   │
│  返回明细 + 总分              │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│ Phase 7: 排序输出            │
│  按总分降序，Top-N            │
│  含分维度得分、推算过程       │
└─────────────────────────────┘
```

---

## 3. 需求建模与卡数估算

### 3.1 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `training_tokens` | float | 1.0 | 训练数据量 (T tokens)，范围 0.01-1000 |
| `sla_tps` | float | None | 推理吞吐 SLA (tokens/s)，如 100 |
| `min_cards` | int | None | 最小卡数（硬下限，自动取 2 幂次方） |
| `max_cards` | int | None | 最大卡数（硬上限） |

### 3.2 训练场景 FLOPs 估算

```
已知:
  P = 模型总参数量 (B)
  T = 训练数据量 (T tokens)
  MFU_target = 0.30  (默认 MFU 目标，有实测时会修正)

总 FLOPs 需求:
  total_flops = 6 × P × 10^9 × T × 10^12
              = 6 × P × T × 10^21 FLOPs

有效单卡日算力:
  fp16_tflops = parse_fp16(chip.precision_perf)  (TFLOPS)
  effective_per_card_day = fp16_tflops × 10^12 × MFU_target × 86400  FLOPs/天

算力约束卡数:
  compute_cards = total_flops / (effective_per_card_day × training_days)
  compute_cards = ceil(compute_cards)
  compute_cards = _round_up_pow2(compute_cards)
```

### 3.3 显存约束卡数（训练）

```
min_vram_total = P × 12 × 1.3  (12 bytes/param: 参数+梯度+优化器, 1.3安全系数)
vram_cards = max(1, ceil(min_vram_total / chip_vram_gb) + 1)  (+1 留余量)
vram_cards = _round_up_pow2(vram_cards)
```

### 3.4 推理场景卡数

```
# 显存约束
min_vram_total = P × 2 × 1.25  (2 bytes/param BF16, 1.25 安全系数)
vram_cards = _round_up_pow2(max(1, ceil(min_vram_total / chip_vram_gb)))

# SLA 约束 (如果有 sla_tps 且有实测数据)
sla_cards = _round_up_pow2(ceil(sla_tps / chip.benchmark_tps_per_card))

recommended_cards = max(vram_cards, sla_cards)
```

### 3.5 训练所需天数反推

```
estimated_days = total_flops / (effective_per_card_day × recommended_cards)
```

### 3.6 训练数据量典型值参考

| 模型规模 | 典型训练数据量 |
|---|---|
| 7B-13B (小模型) | 1-3 T tokens |
| 34B-70B (中模型) | 3-15 T tokens |
| 100B-400B (大模型) | 10-50 T tokens |
| MoE 大模型 | 15-30 T tokens |
| GPT-4 级别 | 20-50 T tokens |
| DeepSeek-V3 级别 | 14.8 T tokens |

---

## 4. 评分体系 (10维)

### 4.0 总原则

- **每维 0-10 分**，加权求和 = 加权总分 (满分 10)
- 权重可配置（默认值基于行业经验）
- 每维有独立公式、上限钳制、下限钳制
- **缺数据降分**：数据缺失时给 0 分而非排除

### 4.1 算力密度 (Compute Density) — 权重 15%

衡量单卡计算能力。以 FP16/BF16 TFLOPS 为核心指标。

```
fp16 = parse_fp16(chip.precision_perf)  # TFLOPS
if fp16 > 0:
    score = min(10.0, fp16 / 200.0 * 10)  # 2000 TFLOPS 为满分参考 (B200级别)
elif INT8 available:
    score = min(10.0, int8_tops / 400.0 * 10 * 0.7)  # INT8 估算 FP16 折扣 30%
else:
    score = 0  # 缺数据
```

参考基准:
| TFLOPS | 芯片示例 | 得分 |
|---|---|---|
| ≥2000 | B200 Ultra (FP8=9000) | 10.0 |
| 1000-2000 | B200, GB300 | 5.0-10.0 |
| 500-1000 | H100, 昇腾910C | 2.5-5.0 |
| 200-500 | A100, MI250X | 1.0-2.5 |
| <200 | 边缘芯片 | <1.0 |

### 4.2 显存充裕度 (Memory Adequacy) — 权重 12%

衡量单卡显存对模型的适配程度。

```
overhead = chip_vram_gb / min_vram_per_model_gb  (min_vram_per_model_gb = 场景所需单卡最低显存)
if overhead >= 4:    score = 10.0  # 极其充裕，可开大 batch
elif overhead >= 2:  score = 8.0
elif overhead >= 1.5: score = 6.0
elif overhead >= 1.0: score = 4.0  # 刚好够
elif overhead >= 0.5: score = 2.0  # 需要 TP/PP 切分
else:                 score = 0     # 完全不够
```

### 4.3 显存带宽效率 (Memory Bandwidth) — 权重 10%

对推理场景至关重要（decode 阶段 memory-bound）。

```
bw = float(chip.vram_bw_gb_s)  # GB/s
if bw > 0:
    # HBM3e 峰值 ~8000 GB/s 为满分参考
    score = min(10.0, bw / 800.0 * 10)
else:
    score = 0
```

参考基准:
| BW (GB/s) | 芯片示例 | 得分 |
|---|---|---|
| ≥8000 | MI355X (8000), MI400X (10300) | 10.0 |
| 4000-8000 | H200 SXM (4800), H100 SXM (3350) | 5.0-10.0 |
| 2000-4000 | A100 80GB (2039), 昇腾910C (3200) | 2.5-5.0 |
| <2000 | 消费级 GPU | <2.5 |

### 4.4 互联扩展性 (Interconnect scalability) — 权重 8%

衡量多卡扩展能力。只有互联带宽是不够的——还需要互联技术本身存在。

```
bw = float(chip.interconnect_bw_gb_s or 0)
has_tech = 1 if chip.interconnect_tech else 0
if bw > 0 and has_tech:
    score = min(10.0, bw / 180.0 * 10 * 0.6 + 4.0)  # 1800 GB/s (NVLink 5.0) 为满分
elif has_tech:
    score = 3.0  # 有互联技术但带宽未知
else:
    score = 0
```

参考基准:
| 互联 BW (GB/s) | 芯片示例 | 得分 |
|---|---|---|
| ≥1800 | B200 (NVLink 5.0, 1800) | 10.0 |
| 900-1800 | H100 (NVLink 4.0, 900) | 7.0-10.0 |
| 400-900 | MI300X (Infinity Fabric, 600) | 5.5-7.0 |
| 100-400 | 昇腾910C (HCCS, 392) | 5.0-5.5 |
| <100 | 部分国产芯片 | <5.0 |

### 4.5 能效比 (Power Efficiency) — 权重 8%

```
fp16 = parse_fp16(chip.precision_perf)
tdp = float(chip.tdp_w or 300)

if fp16 > 0 and tdp > 0:
    eff = fp16 / tdp  # TFLOPS/W → GFLOPS/W
    # H100: 989 TFLOPS / 700W = 1.41 TFLOPS/W
    # B200: 2250 TFLOPS / 1000W = 2.25 TFLOPS/W
    # 3.0 TFLOPS/W 为满分
    score = min(10.0, eff / 0.3 * 10)
elif fp16 > 0:
    score = 3.0  # 有算力无功耗
else:
    score = 0
```

### 4.6 性价比 (Cost Efficiency) — 权重 10%

```
fp16 = parse_fp16(chip.precision_perf)
price_wan = float(chip.price_cny_wan or 0)

if price_wan > 0 and fp16 > 0:
    ratio = fp16 / price_wan  # TFLOPS/万元
    # H100: 989/25 ≈ 39.6  TFLOPS/万元
    # A100: 312/15 ≈ 20.8
    # 100 TFLOPS/万元为满分
    score = min(10.0, ratio / 10.0 * 10)
elif price_wan > 0 and fp16 == 0:
    score = 2.0  # 有价格无算力
else:
    score = 3.0  # 价格未知时给默认中性分

# 总成本加分调整
total_cost = price_wan * recommended_cards
if total_cost <= 50:   score = min(10.0, score + 1.0)
elif total_cost <= 200: score = min(10.0, score + 0.5)
elif total_cost > 1000: score = max(0, score - 1.0)  # 太贵惩罚
```

### 4.7 生态成熟度 (Ecosystem Maturity) — 权重 12%

```
maturity = int(float(chip.maturity_level or 0))
cloud = int(float(chip.cloud_available or 0))
has_frameworks = 1 if chip.software_stack or chip.compatible_frameworks else 0

score = maturity * 2.0  # 0-5 → 0-10
if cloud:  score = min(10.0, score + 1.0)
if has_frameworks: score = min(10.0, score + 0.5)
```

### 4.8 量产就绪度 (Production Readiness) — 权重 7%

```
status = str(chip.production_status or "")
is_released = int(float(chip.is_released or 0))

if "量产" in status:     score = 10.0
elif "已发布" in status:  score = 7.0
elif "待发布" in status:  score = 4.0
elif is_released:         score = 6.0  # 有 is_released=1 但无明确状态
else:                     score = 2.0  # 未公开发布/传闻
```

### 4.9 软件栈兼容性 (Software Compatibility) — 权重 8%

```
frameworks = str(chip.compatible_frameworks or "") + " " + str(chip.software_stack or "")
fw_lower = frameworks.lower()

# 框架支持记分
major_fws = ["pytorch", "tensorflow", "jax", "mindspore", "paddlepaddle", "vllm", "onnx"]
minor_fws = ["deepspeed", "megatron", "fsdp", "tensorrt", "openvino", "triton", "llama.cpp"]

major_hits = sum(1 for fw in major_fws if fw in fw_lower)
minor_hits = sum(1 for fw in minor_fws if fw in fw_lower)

score = min(10.0, major_hits * 2.5 + minor_hits * 1.0)
# 7 major × 2.5 = 17.5，上限钳制到 10
```

### 4.10 国产化优先 (Domestic Priority) — 权重 10%

可配置的偏好维度。

```
vendor_region = chip.vendor_region
prefer_domestic = user_input  # bool

if prefer_domestic:
    if vendor_region == "domestic":   score = 10.0
    else:                              score = 0
elif prefer_vendor:
    if prefer_vendor.lower() in chip.vendor.lower(): score = 10.0
    else:                                            score = 5.0
else:
    score = 5.0  # 无偏好时中性分
```

### 4.11 维度汇总

| # | 维度 | 权重 | 核心指标 | 满分基准 |
|---|---|---|---|---|
| 1 | 算力密度 | 15% | FP16 TFLOPS | 2000 TFLOPS |
| 2 | 显存充裕度 | 12% | VRAM / 模型需求 | 4× 余量 |
| 3 | 显存带宽 | 10% | VRAM BW (GB/s) | 8000 GB/s |
| 4 | 互联扩展 | 8% | 互联 BW + 互联技术 | 1800 GB/s |
| 5 | 能效比 | 8% | TFLOPS / Watt | 3.0 TFLOPS/W |
| 6 | 性价比 | 10% | TFLOPS / 万元 | 100 TFLOPS/万元 |
| 7 | 生态成熟度 | 12% | maturity_level 0-5 | 5 + cloud |
| 8 | 产量就绪度 | 7% | production_status | 已量产 |
| 9 | 软件栈兼容 | 8% | 框架支持数 | 7 主流框架 |
| 10 | 国产化优先 | 10% | vendor_region | 国产/厂商匹配 |

权重总计 = 100%

---

## 5. 权重配置与总分计算

### 5.1 默认权重

```python
DEFAULT_WEIGHTS = {
    "compute_density":       0.15,   # 算力密度
    "memory_adequacy":       0.12,   # 显存充裕度
    "memory_bandwidth":      0.10,   # 显存带宽
    "interconnect":          0.08,   # 互联扩展
    "power_efficiency":      0.08,   # 能效比
    "cost_efficiency":       0.10,   # 性价比
    "ecosystem_maturity":    0.12,   # 生态成熟度
    "production_readiness":  0.07,   # 量产就绪度
    "software_compat":       0.08,   # 软件栈兼容
    "domestic_priority":     0.10,   # 国产化优先
}
```

### 5.2 场景自适应权重

| 场景 | 调整 |
|---|---|
| **训练** | 互联扩展 +2%, 显存带宽 -2% |
| **推理 (无 SLA)** | 显存带宽 +3%, 算力密度 -3% |
| **推理 (有 SLA)** | 显存带宽 +2%, 能效比 +1% |
| **边缘部署** | 能效比 +3%, 互联扩展 -3% |

### 5.3 总分计算

```
weighted_score = Σ (dimension_score_i × weight_i)
total_score = round(weighted_score × 10, 1)  # 0-100 分制
```

---

## 6. 实测 Benchmark 增强

### 6.1 数据可用性

| 指标 | 数据量 |
|---|---|
| 推理 benchmarks | 1287 条 (1 chip × model × precision) |
| 训练 benchmarks | 16 条 (含 MFU、GPU 数) |
| 兼容性记录 | 150 条 (10 verified, 134 vendor_claimed, 6 community) |

### 6.2 训练 MFU 修正

当 chip_model_benchmarks 中有该芯片的训练 MFU 数据时，修正 Phase 3 的 `MFU_target`：

```python
def get_mfu_for_chip(chip_model, model_id=None):
    """从实测数据获取该芯片的 MFU，无数据时返回默认值 0.30"""
    row = db.execute(
        "SELECT AVG(CAST(mfu_pct AS REAL)) FROM chip_model_benchmarks "
        "WHERE chip_model = ? AND workload_type = 'training' AND mfu_pct != ''",
        (chip_model,)
    ).fetchone()
    if row and row[0]:
        return row[0] / 100.0  # 38% → 0.38
    return 0.30  # 默认
```

### 6.3 推理吞吐 SLA

当指定了 `sla_tps` 时，从实测数据中查找最匹配的 benchmark：

```python
def get_tps_for_chip(chip_model, model_id):
    """查找该芯片运行该模型的实测推理吞吐"""
    row = db.execute(
        "SELECT CAST(throughput_tok_s AS REAL) FROM chip_model_benchmarks "
        "WHERE chip_model = ? AND model_id = ? AND workload_type = 'inference' "
        "AND throughput_tok_s != '' "
        "ORDER BY CAST(throughput_tok_s AS REAL) DESC LIMIT 1",
        (chip_model, model_id)
    ).fetchone()
    if row:
        return float(row[0])  # tok/s per card
    return None
```

如果匹配到实测数据，`sla_cards = ceil(sla_tps / tps_per_card)`。
如果没有匹配，使用 FP16 理论值估算 (`tps ≈ fp16 × 1e12 / (2 × P × 1e9)`)。

### 6.4 可信度标记

```python
estimated = not benchmark_exists   # 是否理论估算
confidence = "measured" if benchmark_exists else "theoretical"
```

- `measured`：有实测数据支持，卡数估算准确度 ±10%
- `theoretical`：纯理论推算，卡数估算准确度 ±50%

---

## 7. 推理场景 SLA 评估

### 7.1 推理吞吐估算（无实测数据时）

```
# 理论峰值 (prefill bound)
theoretical_tps_per_card = fp16_tflops × 10^12 / (2 × P × 10^9)
                         = fp16_tflops × 500 / P   tok/s

# 实际有效 (考虑 memory bound, decode 阶段)
effective_tps = theoretical_tps_per_card × 0.25  (内存带宽利用因子)

# 示例 Qwen2.5-7B on H100:
#   989 TFLOPS / (2×7.6×10^9) ≈ 65,000 tok/s (理论)
#   实际 25% ≈ 16,000 tok/s
```

### 7.2 推理卡数 SLA

```
if sla_tps:
    if benchmark_tps:
        sla_cards = ceil(sla_tps / benchmark_tps)
    else:
        sla_cards = ceil(sla_tps / effective_tps)
    sla_cards = _round_up_pow2(sla_cards)

    meets_sla = sla_cards <= max_cards (if max_cards)
else:
    meets_sla = None (不评估)
```

---

## 8. API 响应格式

### 8.1 新响应结构

```json
{
  "model": {
    "model_id": "Qwen/Qwen2.5-7B-Instruct",
    "architecture": "Dense",
    "total_params_b": 7.6,
    "total_params": 7600000000
  },
  "requirements": {
    "scenario": "train",
    "training_tokens_t": 3.0,
    "training_days": 7.0,
    "min_vram_gb": 118.6,
    "total_tflops_required": 8.208e22,
    "max_cards": 16,
    "min_cards": 2,
    "max_price_wan": 50,
    "min_maturity": 2
  },
  "candidates": [
    {
      "chip": { /* chip_summary 字段 */ },
      "estimation": {
        "vram_cards": 1,
        "compute_cards": 4,
        "sla_cards": null,
        "recommended_cards": 4,
        "mfu_target": 0.38,
        "mfu_source": "measured",
        "estimated_training_days": 2.7,
        "meets_sla": true,
        "confidence": "measured"
      },
      "scoring": {
        "total": 78.5,
        "dimensions": {
          "compute_density":     {"score": 4.0, "weight": 0.15, "weighted": 0.60, "detail": "FP16=800TF → 4.0/10"},
          "memory_adequacy":     {"score": 6.0, "weight": 0.12, "weighted": 0.72, "detail": "128GB / 119GB=1.08× → 6.0/10"},
          "memory_bandwidth":    {"score": 4.0, "weight": 0.10, "weighted": 0.40, "detail": "3200GB/s → 4.0/10"},
          "interconnect":        {"score": 6.2, "weight": 0.08, "weighted": 0.50, "detail": "392GB/s HCCS → 6.2/10"},
          "power_efficiency":    {"score": 4.4, "weight": 0.08, "weighted": 0.35, "detail": "800TF/600W=1.33 TF/W → 4.4/10"},
          "cost_efficiency":     {"score": 3.0, "weight": 0.10, "weighted": 0.30, "detail": "价格未知 → 默认 3.0/10"},
          "ecosystem_maturity":  {"score": 8.0, "weight": 0.12, "weighted": 0.96, "detail": "成熟度4/5 + MindSpore/PyTorch/TF → 8.0/10"},
          "production_readiness":{"score": 10.0, "weight": 0.07, "weighted": 0.70, "detail": "已量产 → 10.0/10"},
          "software_compat":     {"score": 7.5, "weight": 0.08, "weighted": 0.60, "detail": "PyTorch+TensorFlow+MindSpore+vLLM → 7.5/10"},
          "domestic_priority":   {"score": 10.0, "weight": 0.10, "weighted": 1.00, "detail": "国产(华为) + 优先国产=ON → 10.0/10"}
        }
      },
      "cost": {
        "unit_price_wan": null,
        "total_price_wan": null,
        "price_confidence": "unknown"
      }
    }
  ],
  "meta": {
    "engine_version": "2.0",
    "total_candidates": 25,
    "rejected_by_hard_constraints": 18,
    "scoring_timestamp": "2026-07-30T20:00:00Z"
  }
}
```

### 8.2 新增 Query 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `training_tokens` | float | 1.0 | 训练数据量 (T tokens) |
| `min_cards` | int | None | 最小卡数硬下限 |

---

## 9. 前端展示方案

### 9.1 推荐结果卡片

每张推荐卡片包含：

```
┌─────────────────────────────────────────────────┐
│ #1  综合评分 78.5/100             [████████░░]   │
│ 昇腾910C OAM 128GB | 华为(昇腾)  国产 NPU ★4    │
│                                                   │
│ 配置: 4卡 · 预计2.7天 · 满足SLA ✓                  │
│ 总价: 未知 · 单卡128GB HBM2e · FP16=800TF          │
│ 置信度: 🟢 有实测MFU(38%)                          │
│                                                   │
│ 评分明细 ▼                                      │
│ ┌─────────────────────────────────────────────┐ │
│ │ 算力密度    ████░░░░░░  4.0/10 ×15% = 0.60 │ │
│ │ 显存充裕    ██████░░░░  6.0/10 ×12% = 0.72 │ │
│ │ 显存带宽    ████░░░░░░  4.0/10 ×10% = 0.40 │ │
│ │ 互联扩展    ██████░░░░  6.2/10 × 8% = 0.50 │ │
│ │ 能效比      ████░░░░░░  4.4/10 × 8% = 0.35 │ │
│ │ 性价比      ███░░░░░░░  3.0/10 ×10% = 0.30 │ │
│ │ 生态成熟    ████████░░  8.0/10 ×12% = 0.96 │ │
│ │ 量产就绪    ██████████ 10.0/10 × 7% = 0.70 │ │
│ │ 软件栈      ███████░░░  7.5/10 × 8% = 0.60 │ │
│ │ 国产优先    ██████████ 10.0/10 ×10% = 1.00 │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ 推算: 3T tokens × 7天 → 需 4×800TF×30%MFU        │
│       显存119GB → 128GB 单卡够 → 2^0=1卡(VRAM)     │
│       算力→ 需4卡 → recommend=max(1,4)=4卡        │
└─────────────────────────────────────────────────┘
```

### 9.2 算法说明按钮

在推荐结果上方增加一个 `📖 算法说明` 按钮，点击弹出 Modal 展示本文档的关键部分（评分维度表 + 卡数推算公式）。

### 9.3 评分条形图

每个维度用横向条形图展示 0-10 得分，颜色从红(0-3) 黄(3-6) 绿(6-8) 蓝(8-10)。

---

## 10. 文档与透明度

### 10.1 嵌入文档

将 `docs/recommend_algo.md` 作为算法说明文档，前端通过 API 获取或通过静态 Markdown 渲染展示。

### 10.2 API 端点

```
GET /docs/recommend-algorithm  → 返回本文档的 markdown 内容
```

或直接在 `GET /api/v1/db/status` 中增加 `recommend_engine_version` 字段。

### 10.3 TODO 清单

- [ ] 实现 10 维评分函数 `_score_chip_v2()`
- [ ] 重构 `api_chip_recommend()` 适配新架构
- [ ] CLI `chip recommend` 同步更新
- [ ] 新增 `training_tokens` 参数
- [ ] Benchmark 实测数据集成
- [ ] API 响应格式升级到 v2
- [ ] 前端卡片：评分明细折叠面板 + 条形图
- [ ] 前端表单：新增 training_tokens 和 min_cards
- [ ] 新增算法说明 Modal
- [ ] 测试 + 验证
