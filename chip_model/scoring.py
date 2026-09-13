"""
AISHPerf Chip Recommendation Scoring Engine v4.4

8-dimension scoring with each dimension outputting 0.0-100.0,
weighted sum yields total 0-100.

v4.4 changes:
  - 大类权重调整为生态成熟度40%、实测验证度30%、算力性能20%、性价比10%

v4.3 changes:
  - 3大类 → 4大类 (5:2:2:1)：新增「实测验证度」独立大类 (10%)，生态成熟度 30%→20%
  - 删除「兼容性评分」维度（与框架/工具链兼容重复）
  - SUB_DIMS_ECOSYSTEM: framework 40% + toolchain 40% + source 20%

v4.2 changes:
  - All scores now 0-100 scale (was 0-10). Category scores, dimension scores, detail
    messages all in /100. total = direct weighted sum, no ×10.
  - Deleted: production_readiness, cost_efficiency (process_node), domestic_priority
  - New: server_count_efficiency, framework_compat, toolchain_compat, source_credibility
  - Modified: ecosystem_maturity → compatibility_score (renamed)
    benchmark_evidence: no-data default 50 (was 6.2)
    SUB_DIMS_ECOSYSTEM: compat 40% + framework 15% + toolchain 15% + benchmark 20% + source 10%

All formulas have clear physical meaning, documented constants,
and return traces for transparency.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Optional, Literal


# ═══════════════════════════════════════════════════════════
# Enums for fine-grained scenarios
# ═══════════════════════════════════════════════════════════

TrainStage = Literal["cpt", "sft", "rl"]
TrainMethod = Literal["full_param", "lora"]
QuantizeMethod = Literal["gptq", "awq", "bitsandbytes", "gguf"]
QuantizeBits = Literal["int8", "int4", "fp8"]
InferenceQuant = Literal["fp16", "int8", "int4_gptq", "int4_awq", "gguf_q4", "gguf_q8"]


# ═══════════════════════════════════════════════════════════
# VRAM estimation formulas per scenario
# ═══════════════════════════════════════════════════════════

# ── Model architecture params (KV cache / activation 需要) ──
# config_json 覆盖率仅 ~9%，缺时用「典型 7B 模型」通用默认（标记 estimated）。
ARCH_DEFAULTS = {
    "num_layers": 32,       # Llama-7B/8B 量级典型层数
    "num_kv_heads": 8,      # 典型 GQA (8 KV heads)
    "head_dim": 128,        # 绝大多数模型 head_dim=128
    "hidden_size": 4096,    # 7B 量级典型隐藏维度
}


def resolve_arch_params(config_json: str | None, total_params_b: float = 0.0) -> dict:
    """从 config_json 解析 KV/激活值所需架构参数，缺则用通用默认（estimated=True）。

    Returns {num_layers, num_kv_heads, head_dim, hidden_size, estimated}.
    """
    estimated = True
    d: dict = {}
    if config_json:
        try:
            parsed = json.loads(config_json) if isinstance(config_json, str) else config_json
            if isinstance(parsed, dict):
                d = parsed
        except Exception:
            d = {}

    # Multimodal models (for example Qwen3.5-VL/MoE) keep language-model
    # architecture fields under text_config.  Prefer that nested config so KV
    # cache and activation estimates do not silently fall back to a 7B model.
    text_d = d.get("text_config") if isinstance(d.get("text_config"), dict) else d

    num_layers = text_d.get("num_hidden_layers") or text_d.get("num_layers") or None
    num_heads = text_d.get("num_attention_heads") or None
    num_kv_heads = text_d.get("num_key_value_heads") or None
    head_dim = text_d.get("head_dim") or None
    hidden_size = text_d.get("hidden_size") or None
    layer_types = text_d.get("layer_types") or []
    num_kv_layers = sum(1 for layer in layer_types if layer == "full_attention")
    if not num_kv_layers:
        num_kv_layers = num_layers

    # head_dim 推导：head_dim = hidden_size / num_attention_heads
    if head_dim is None and hidden_size and num_heads:
        try:
            head_dim = int(hidden_size) // int(num_heads)
        except Exception:
            head_dim = None

    # 关键参数齐全 → 非估算
    if num_layers and (num_kv_heads or num_heads) and head_dim and hidden_size:
        estimated = False

    return {
        "num_layers": int(num_layers) if num_layers else ARCH_DEFAULTS["num_layers"],
        "num_kv_layers": int(num_kv_layers) if num_kv_layers else ARCH_DEFAULTS["num_layers"],
        "num_kv_heads": int(num_kv_heads) if num_kv_heads else (int(num_heads) if num_heads else ARCH_DEFAULTS["num_kv_heads"]),
        "head_dim": int(head_dim) if head_dim else ARCH_DEFAULTS["head_dim"],
        "hidden_size": int(hidden_size) if hidden_size else ARCH_DEFAULTS["hidden_size"],
        "num_experts": int(text_d.get("num_experts") or text_d.get("num_local_experts") or 0),
        "num_experts_per_tok": int(text_d.get("num_experts_per_tok") or 0),
        "estimated": estimated,
    }


def resolve_moe_metadata(
    model_id: str,
    architecture_family: str,
    total_params_b: float,
    config_json: str | dict | None,
) -> dict:
    """Resolve MoE metadata without using active parameters as weight memory.

    Configs expose expert counts but often not an exact activated-parameter
    total. Prefer an explicit value, then the common ``-A3B`` name convention.
    Do not derive it from top-k/expert-count because dense and shared layers
    make that ratio physically incorrect.
    """
    parsed: dict = {}
    if isinstance(config_json, dict):
        parsed = config_json
    elif config_json:
        try:
            candidate = json.loads(config_json)
            parsed = candidate if isinstance(candidate, dict) else {}
        except Exception:
            parsed = {}
    text_cfg = parsed.get("text_config") if isinstance(parsed.get("text_config"), dict) else parsed
    model_type = str(text_cfg.get("model_type") or parsed.get("model_type") or "")
    num_experts = int(text_cfg.get("num_experts") or text_cfg.get("num_local_experts") or 0)
    experts_per_token = int(text_cfg.get("num_experts_per_tok") or 0)
    is_moe = (
        "moe" in str(architecture_family or "").lower()
        or "moe" in model_type.lower()
        or num_experts > 0
    )

    active_params = None
    for key in ("active_params_b", "activated_params_b", "num_active_parameters_b"):
        try:
            value = float(text_cfg.get(key) or parsed.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if 0 < value < total_params_b:
            active_params = value
            break
    if active_params is None and is_moe:
        match = re.search(r"(?:^|[-_/])A(\d+(?:\.\d+)?)B(?:$|[-_/])", model_id or "", re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if 0 < value < total_params_b:
                active_params = value

    return {
        "is_moe": is_moe,
        "total_params_b": total_params_b,
        "active_params_b": active_params,
        "weight_params_b": total_params_b,
        "parameter_basis": "total",
        "num_experts": num_experts or None,
        "experts_per_token": experts_per_token or None,
        "note": (
            "激活参数量只表示每个 token 参与计算的专家规模；标准常驻权重部署的显存按总参数量计算。"
            if is_moe else None
        ),
    }


def estimate_kv_cache_gb(arch: dict, max_context: int, concurrency: int) -> float:
    """KV Cache 显存 (GB) = 2(K+V) × num_layers × num_kv_heads × head_dim × bytes × context × concurrency.

    KV cache 默认按 BF16/FP16 (2 bytes/element) 保存，即使权重 INT4
    量化也通常不会自动改变 KV 精度。FP8 KV Cache 属于需要后端显式支持的
    独立优化，不能由权重量化选项推断。
    """
    kv_layers = arch.get("num_kv_layers") or arch["num_layers"]
    per_token = 2 * kv_layers * arch["num_kv_heads"] * arch["head_dim"] * 2.0
    total = per_token * max(1, max_context) * max(1, concurrency)
    return total / 1e9


def estimate_activation_gb(arch: dict, batch_size: int, seq_len: int) -> float:
    """训练激活值显存 (GB) ≈ batch × seq × hidden × num_layers × 40 bytes（FP16 无梯度检查点，粗估）。"""
    total = batch_size * seq_len * arch["hidden_size"] * arch["num_layers"] * 40.0
    return total / 1e9


def estimate_vram_total(
    params_B: float,
    scenario: str,                    # "train" | "quantize" | "inference"
    stage: TrainStage = "sft",
    method: str = "full_param",       # train: full_param/lora  |  quantize: gptq/awq/bitsandbytes/gguf
    quant: str = "fp16",              # inference: fp16/int8/int4_gptq/int4_awq/gguf_q4/gguf_q8
    quantize_bits: str = "int4",      # quantize: int8/int4/fp8
    moe_activated_B: float | None = None,
    max_context: int = 4096,          # inference: input_len 缺省时的兼容回退值
    concurrency: int = 1,             # inference: 目标并发请求数（目标显存与卡数）
    input_len: int | None = None,     # inference: 单请求输入长度 (tokens)
    output_len: int | None = None,    # inference: 单请求最大输出长度 (tokens)
    batch_size: int = 1,              # train: batch size
    seq_len: int = 2048,              # train: 样本长度 (tokens)
    arch: dict | None = None,         # {num_layers, num_kv_heads, head_dim, hidden_size}
) -> dict:
    """Estimate VRAM (GB), including inference single-request and target tiers.

    Returns {min_vram, full_vram, min_formula, full_formula, kv_cache_gb,
             target_vram, weight_vram, ideal_kv_gb, total_context, calculation}.
      - 推理: min/full 均表示最小部署 = 权重 + 单请求峰值 KV；
              target = 权重 + 单请求峰值 KV × 目标并发。保留 min/full 双字段
              只为 API 兼容，界面仅展示最小与目标并发两档。
      - 训练:  min = 权重+优化器 + 激活值(batch×seq),  full = min（训练无独立全功能档）
      - 量化:  min = full = 量化显存
    """
    P = params_B
    arch = arch or {}

    if scenario == "train":
        safety = 1.25
        if stage == "cpt":
            # CPT: weights(2) + gradients(2) + Adam m+v(8) ≈ 12 bytes/param + activations(另算)
            bytes_per = 12.0
            label = "CPT"
        elif stage == "sft":
            if method == "full_param":
                # SFT full-param: weights(2) + gradients(2) + Adam m+v(8) = 12 + activations(另算)
                bytes_per = 12.0
                label = "SFT(全参)"
            elif method == "lora":
                # LoRA: load full frozen weights(2) + tiny trainable adapter + no optimizer for base
                bytes_per = 2.5
                label = "SFT(LoRA)"
            else:
                bytes_per = 12.0
                label = "SFT(full_param)"
        elif stage == "rl":
            # RL (PPO/GRPO): Actor(2) + Critic(2) + Ref model(2) + optimizer states(8-12)
            bytes_per = 25.0
            label = "RL(PPO/GRPO)"
        else:
            bytes_per = 12.0
            label = "训练(full)"

        # MoE model state still contains every expert.  Activated parameters
        # describe per-token compute, not the amount of resident model state.
        # Expert parallelism may shard those weights across cards, which is
        # already represented by dividing the total requirement by per-card
        # VRAM during card-count estimation.
        effective_P = P
        weight_vram = effective_P * bytes_per * safety
        act_gb = estimate_activation_gb(arch, batch_size, seq_len)
        min_vram = weight_vram + act_gb
        formula = (
            f"{label}: {effective_P:.1f}B × {bytes_per} bytes/param × {safety} = {weight_vram:.0f}GB "
            f"+ 激活值 {batch_size}×{seq_len}×{arch.get('hidden_size', 4096)}×{arch.get('num_layers', 32)}×40B = {act_gb:.1f}GB "
            f"= {min_vram:.0f}GB"
        )
        return {
            "min_vram": round(min_vram, 1),
            "full_vram": round(min_vram, 1),
            "min_formula": formula,
            "full_formula": formula,
            "kv_cache_gb": 0.0,
            "weight_vram": round(weight_vram, 1),
            "activation_vram": round(act_gb, 1),
            "calculation": {
                "parameter_basis": "total",
                "total_params_b": P,
                "active_params_b": moe_activated_B,
                "is_moe": bool(moe_activated_B and moe_activated_B < P),
                "safety_factor": safety,
                "components": [
                    {
                        "id": "model_states",
                        "label": "模型权重、梯度与优化器状态",
                        "result_gb": round(weight_vram, 1),
                        "formula": f"{P:.1f}B × {bytes_per:.1f} bytes/param × {safety:.2f} = {weight_vram:.1f} GB",
                        "inputs": {"params_b": P, "bytes_per_param": bytes_per, "safety_factor": safety},
                    },
                    {
                        "id": "activations",
                        "label": "训练激活值",
                        "result_gb": round(act_gb, 1),
                        "formula": (
                            f"{batch_size} batch × {seq_len} tokens × {arch.get('hidden_size', 4096)} hidden "
                            f"× {arch.get('num_layers', 32)} layers × 40 bytes = {act_gb:.1f} GB"
                        ),
                        "inputs": {
                            "batch_size": batch_size, "seq_len": seq_len,
                            "hidden_size": arch.get("hidden_size", 4096),
                            "num_layers": arch.get("num_layers", 32), "activation_bytes": 40,
                        },
                    },
                ],
                "minimum": {"total_gb": round(min_vram, 1), "component_ids": ["model_states", "activations"]},
                "full": {"total_gb": round(min_vram, 1), "component_ids": ["model_states", "activations"]},
            },
        }

    elif scenario == "quantize":
        # ── Quantization scenario (v3.1): needs training-capable chips ──
        # Must hold FP16 full model + calibration data structures in VRAM.
        safety = 1.25
        quantize_bytes = {
            "gptq": 3.5,           # 2.0 (FP16 model) + 1.5 (Hessian matrices)
            "awq": 3.0,            # 2.0 (FP16 model) + 1.0 (activation stats)
            "bitsandbytes": 2.5,   # 2.0 (FP16 model) + 0.5 (quant buffers)
            "gguf": 2.5,           # 2.0 (FP16 model) + 0.5 (calibration data)
        }
        bytes_per = quantize_bytes.get(method, 3.5)
        method_label = {"gptq": "GPTQ", "awq": "AWQ",
                        "bitsandbytes": "bitsandbytes", "gguf": "GGUF"}.get(method, method)
        bits_label = {"int8": "INT8", "int4": "INT4", "fp8": "FP8"}.get(quantize_bits, quantize_bits)
        label = f"量化({method_label}-{bits_label})"

        effective_P = P
        vram = effective_P * bytes_per * safety
        formula = f"{label}: {effective_P:.1f}B × {bytes_per} bytes/param × {safety} = {vram:.0f}GB"
        return {
            "min_vram": round(vram, 1),
            "full_vram": round(vram, 1),
            "min_formula": formula,
            "full_formula": formula,
            "kv_cache_gb": 0.0,
            "weight_vram": round(vram, 1),
            "calculation": {
                "parameter_basis": "total",
                "total_params_b": P,
                "active_params_b": moe_activated_B,
                "is_moe": bool(moe_activated_B and moe_activated_B < P),
                "safety_factor": safety,
                "components": [{
                    "id": "quantization_workspace",
                    "label": "全量模型与量化工作区",
                    "result_gb": round(vram, 1),
                    "formula": f"{P:.1f}B × {bytes_per:.1f} bytes/param × {safety:.2f} = {vram:.1f} GB",
                    "inputs": {"params_b": P, "bytes_per_param": bytes_per, "safety_factor": safety},
                }],
                "minimum": {"total_gb": round(vram, 1), "component_ids": ["quantization_workspace"]},
                "full": {"total_gb": round(vram, 1), "component_ids": ["quantization_workspace"]},
            },
        }

    else:  # inference
        safety = 1.25
        quant_bytes = {
            "fp16": 2.0, "int8": 1.0,
            "int4_gptq": 0.5, "int4_awq": 0.5,
            "gguf_q4": 0.5, "gguf_q8": 1.0,
        }
        bytes_per = quant_bytes.get(quant, 2.0)
        quant_label = {"fp16": "FP16", "int8": "INT8", "int4_gptq": "INT4-GPTQ",
                       "int4_awq": "INT4-AWQ", "gguf_q4": "GGUF Q4", "gguf_q8": "GGUF Q8"}.get(quant, quant)

        # MoE inference routes each token through only a subset of experts, but
        # the complete expert weight set must be resident across the deployment
        # unless an explicit CPU/NVMe expert-offload mode is selected.  This
        # estimator models the standard resident-weight deployment.
        effective_P = P
        weight_vram = effective_P * bytes_per * safety

        # 最小档（权重+单请求峰值KV）：峰值 KV 必须同时包含 prompt
        # 和已生成 token。目标并发档只增加 KV 槽位，不重复计算常驻权重。
        # max_context 仅作为未显式传 input_len 时的兼容回退值；它不再与
        # input/output 使用两套互相冲突的口径。
        _in = input_len if input_len and input_len > 0 else max_context
        _out = output_len if output_len and output_len > 0 else 512
        total_context = _in + _out
        kv_request = estimate_kv_cache_gb(arch, total_context, 1)
        target_concurrency = max(1, concurrency)
        target_kv = kv_request * target_concurrency

        # 推理不再展示“仅权重、无法承载完整请求”的最小档。兼容字段
        # min_vram/full_vram 均表示最小部署（权重 + 1份峰值KV）。
        full_vram = weight_vram + kv_request
        min_vram = full_vram
        target_vram = weight_vram + target_kv
        full_formula = (
            f"推理({quant_label})最小部署: 权重 {weight_vram:.1f}GB + "
            f"KV Cache(({_in}输入+{_out}输出) × 1请求) {kv_request:.1f}GB = {full_vram:.0f}GB"
        )
        min_formula = full_formula
        target_formula = (
            f"目标并发显存: 权重 {weight_vram:.1f}GB + 单请求峰值KV {kv_request:.3f}GB "
            f"× {target_concurrency}请求 = {target_vram:.1f}GB"
        )
        return {
            "min_vram": round(min_vram, 1),
            "full_vram": round(full_vram, 1),
            "min_vram_raw": min_vram,
            "full_vram_raw": full_vram,
            "min_formula": min_formula,
            "full_formula": full_formula,
            "kv_cache_gb": round(kv_request, 3),
            "kv_cache_gb_raw": kv_request,
            "target_kv_gb": round(target_kv, 3),
            "target_kv_gb_raw": target_kv,
            "target_vram": round(target_vram, 1),
            "target_vram_raw": target_vram,
            "target_formula": target_formula,
            "weight_vram": round(weight_vram, 1),
            "weight_vram_raw": weight_vram,
            "ideal_kv_gb": round(kv_request, 3),
            "ideal_kv_gb_raw": kv_request,
            "total_context": total_context,
            "calculation": {
                "parameter_basis": "total",
                "total_params_b": P,
                "active_params_b": moe_activated_B,
                "is_moe": bool(moe_activated_B and moe_activated_B < P),
                "safety_factor": safety,
                "components": [
                    {
                        "id": "weights",
                        "label": f"{quant_label} 模型权重",
                        "result_gb": round(weight_vram, 1),
                        "formula": f"{P:.1f}B × {bytes_per:.1f} bytes/param × {safety:.2f} = {weight_vram:.1f} GB",
                        "inputs": {"params_b": P, "bytes_per_param": bytes_per, "safety_factor": safety},
                    },
                    {
                        "id": "kv_cache_request",
                        "label": "单请求峰值 KV Cache",
                        "result_gb": round(kv_request, 3),
                        "formula": (
                            f"2(K/V) × {arch.get('num_kv_layers', arch.get('num_layers', 32))} layers "
                            f"× {arch.get('num_kv_heads', 8)} KV heads × {arch.get('head_dim', 128)} head_dim "
                            f"× 2 bytes/element（BF16/FP16 KV精度） × "
                            f"({_in}输入 + {_out}输出) tokens × 1请求 = {kv_request:.3f} GB"
                        ),
                        "inputs": {
                            "num_kv_layers": arch.get("num_kv_layers", arch.get("num_layers", 32)),
                            "num_kv_heads": arch.get("num_kv_heads", 8),
                            "head_dim": arch.get("head_dim", 128),
                            "kv_cache_dtype": "BF16/FP16",
                            "bytes_per_element": 2,
                            "input_tokens": _in, "output_tokens": _out,
                            "total_context": total_context, "requests": 1,
                        },
                    },
                ],
                "minimum": {"total_gb": round(full_vram, 1), "component_ids": ["weights", "kv_cache_request"]},
                "full": {"total_gb": round(full_vram, 1), "component_ids": ["weights", "kv_cache_request"]},
                "target_concurrency": {
                    "total_gb": round(target_vram, 1),
                    "weight_gb": round(weight_vram, 1),
                    "kv_total_gb": round(target_kv, 3),
                    "requests": target_concurrency,
                    "formula": target_formula,
                },
            },
        }


def estimate_training_flops(params_B: float, tokens_T: float) -> float:
    """Total FLOPs for training: 6 × P × tokens.

    Returns FLOPs (float).
    """
    return 6 * (params_B * 1e9) * (tokens_T * 1e12)


# ═══════════════════════════════════════════════════════════
# v4.0 Configuration — 3-category weights + sub-dimension ratios
# ═══════════════════════════════════════════════════════════
#
# Category weights (3 presets × 3 categories):
#   Train:     compute=55%, cost=15%, ecosystem=30%
#   Inference: compute=45%, cost=25%, ecosystem=30%
#   Quantize:  compute=40%, cost=20%, ecosystem=40%
#
# Sub-dimension ratios are FIXED (not scenario-dependent):
#   Compute:   compute_perf(90%) + bandwidth_adequacy(10%)
#   Cost:      power_efficiency(60%) + server_count_efficiency(40%)
#   Ecosystem: framework_compat(40%) + toolchain_compat(40%) + source_credibility(20%)
#   Benchmark: benchmark_evidence(100%)
# ═══════════════════════════════════════════════════════════


@dataclass
class CategoryWeights:
    """4-category weight preset. Sum must be 1.0."""
    compute_power: float = 0.20
    cost_effectiveness: float = 0.10
    ecosystem_maturity: float = 0.40
    benchmark_evidence: float = 0.30

    def validate(self) -> bool:
        return abs(self.compute_power + self.cost_effectiveness + self.ecosystem_maturity + self.benchmark_evidence - 1.0) < 0.001

    def to_dict(self) -> dict:
        return {
            "ecosystem_maturity": self.ecosystem_maturity,
            "benchmark_evidence": self.benchmark_evidence,
            "compute_power": self.compute_power,
            "cost_effectiveness": self.cost_effectiveness,
        }


@dataclass
class CategoryResult:
    """Aggregate score for one category, composed of sub-dimensions."""
    id: str = ""
    name_cn: str = ""
    name_en: str = ""
    score: float = 0.0          # 0-100 weighted avg of sub-dimensions
    weight: float = 0.0          # category-level weight
    weighted: float = 0.0        # score × weight
    sub_dimensions: dict = field(default_factory=dict)  # {dim_id: DimensionResult}
    formula: str = ""            # e.g. "D1×0.30 + D2×0.30 + ..."


# ── Sub-dimension composition (fixed ratios within each category) ──

SUB_DIMS_COMPUTE_POWER = {
    "compute_perf": 0.90,
    "bandwidth_adequacy": 0.10,
}

SUB_DIMS_COST_EFFECTIVENESS = {
    "power_efficiency": 0.60,
    "server_count_efficiency": 0.40,  # 8卡/节点 → 10, 多节点扣分
}

SUB_DIMS_ECOSYSTEM = {
    "framework_compat": 0.40,         # major frameworks only
    "toolchain_compat": 0.40,         # minor toolchains only
    "source_credibility": 0.20,       # official source ratio
}

SUB_DIMS_BENCHMARK = {
    "benchmark_evidence": 1.0,        # standalone category: 实测验证度
}

CATEGORY_DEFS = [
    {"id": "ecosystem_maturity", "name_cn": "生态成熟度", "name_en": "Ecosystem Maturity",
     "sub_dims": SUB_DIMS_ECOSYSTEM},
    {"id": "benchmark_evidence", "name_cn": "实测验证度", "name_en": "Benchmark Evidence",
     "sub_dims": SUB_DIMS_BENCHMARK},
    {"id": "compute_power", "name_cn": "算力性能", "name_en": "Compute Capability",
     "sub_dims": SUB_DIMS_COMPUTE_POWER},
    {"id": "cost_effectiveness", "name_cn": "性价比", "name_en": "Cost-Effectiveness",
     "sub_dims": SUB_DIMS_COST_EFFECTIVENESS},
]

# ── Category weight presets for each scenario ──

CAT_WEIGHTS_TRAIN = CategoryWeights(
    compute_power=0.20, cost_effectiveness=0.10, ecosystem_maturity=0.40, benchmark_evidence=0.30,
)
CAT_WEIGHTS_INFER = CategoryWeights(
    compute_power=0.20, cost_effectiveness=0.10, ecosystem_maturity=0.40, benchmark_evidence=0.30,
)
CAT_WEIGHTS_QUANTIZE = CategoryWeights(
    compute_power=0.20, cost_effectiveness=0.10, ecosystem_maturity=0.40, benchmark_evidence=0.30,
)

# ── Scenario → (CategoryWeights, label) lookup ──

_SCENARIO_CAT_MAP = {
    ("train", "cpt", "full_param"):    (CAT_WEIGHTS_TRAIN, "训练·CPT·全参"),
    ("train", "sft", "full_param"):    (CAT_WEIGHTS_TRAIN, "训练·SFT·全参"),
    ("train", "sft", "lora"):          (CAT_WEIGHTS_TRAIN, "训练·SFT·LoRA"),
    ("train", "rl", "full_param"):     (CAT_WEIGHTS_TRAIN, "训练·RL·全参"),
    ("quantize", "gptq", ""):          (CAT_WEIGHTS_QUANTIZE, "量化"),
    ("quantize", "awq", ""):           (CAT_WEIGHTS_QUANTIZE, "量化"),
    ("quantize", "bitsandbytes", ""):  (CAT_WEIGHTS_QUANTIZE, "量化"),
    ("quantize", "gguf", ""):          (CAT_WEIGHTS_QUANTIZE, "量化"),
    ("inference", "fp16", ""):         (CAT_WEIGHTS_INFER, "推理·FP16"),
    ("inference", "int8", ""):         (CAT_WEIGHTS_INFER, "推理·INT8"),
    ("inference", "int4_gptq", ""):    (CAT_WEIGHTS_INFER, "推理·INT4"),
    ("inference", "int4_awq", ""):     (CAT_WEIGHTS_INFER, "推理·INT4"),
    ("inference", "gguf_q4", ""):      (CAT_WEIGHTS_INFER, "推理·GGUF Q4"),
    ("inference", "gguf_q8", ""):      (CAT_WEIGHTS_INFER, "推理·GGUF Q8"),
}


def get_category_weights(
    scenario: str,
    stage: TrainStage = "sft",
    method: str = "full_param",
    quant: str = "fp16",
    quantize_bits: str = "int4",
) -> tuple[CategoryWeights, str]:
    """Return (category_weights, display_label) for a given scenario."""
    if scenario == "train":
        key = ("train", stage, method)
    elif scenario == "quantize":
        key = ("quantize", method, "")
    else:
        key = ("inference", quant, "")
    return _SCENARIO_CAT_MAP.get(
        key,
        (CAT_WEIGHTS_TRAIN if scenario == "train" else
         CAT_WEIGHTS_QUANTIZE if scenario == "quantize" else
         CAT_WEIGHTS_INFER,  scenario),
    )


# ── Backward compat: legacy ScoringWeights + scenario presets ──
# Kept for any existing callers; internally converted from CategoryWeights.

@dataclass
class ScoringWeights:
    """DEPRECATED — use CategoryWeights. Kept for backward compat."""
    compute_perf: float = 0.20
    vram_sufficiency: float = 0.15
    cost_efficiency: float = 0.12
    power_efficiency: float = 0.08
    interconnect_quality: float = 0.12
    ecosystem_maturity: float = 0.10
    sla_satisfaction: float = 0.10
    production_readiness: float = 0.05
    benchmark_evidence: float = 0.08
    total_cost_ownership: float = 0.00

    def scale_other_weights(self, tco_weight: float) -> "ScoringWeights":
        remaining = 1.0 - tco_weight
        scale = remaining / (1.0 - self.total_cost_ownership) if (1.0 - self.total_cost_ownership) > 0 else 1.0
        return ScoringWeights(
            compute_perf=round(self.compute_perf * scale, 4),
            vram_sufficiency=round(self.vram_sufficiency * scale, 4),
            cost_efficiency=round(self.cost_efficiency * scale, 4),
            power_efficiency=round(self.power_efficiency * scale, 4),
            interconnect_quality=round(self.interconnect_quality * scale, 4),
            ecosystem_maturity=round(self.ecosystem_maturity * scale, 4),
            sla_satisfaction=round(self.sla_satisfaction * scale, 4),
            production_readiness=round(self.production_readiness * scale, 4),
            benchmark_evidence=round(self.benchmark_evidence * scale, 4),
            total_cost_ownership=tco_weight,
        )


# Legacy scenario presets (7 presets kept for backward compat)
WEIGHTS_CPT = ScoringWeights(
    compute_perf=0.22, vram_sufficiency=0.15, cost_efficiency=0.10,
    power_efficiency=0.07, interconnect_quality=0.15, ecosystem_maturity=0.08,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)
WEIGHTS_SFT_FULL = ScoringWeights(
    compute_perf=0.18, vram_sufficiency=0.18, cost_efficiency=0.12,
    power_efficiency=0.08, interconnect_quality=0.10, ecosystem_maturity=0.10,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.09,
)
WEIGHTS_SFT_LORA = ScoringWeights(
    compute_perf=0.12, vram_sufficiency=0.25, cost_efficiency=0.15,
    power_efficiency=0.08, interconnect_quality=0.05, ecosystem_maturity=0.12,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)
WEIGHTS_QUANTIZE = ScoringWeights(
    compute_perf=0.12, vram_sufficiency=0.28, cost_efficiency=0.12,
    power_efficiency=0.06, interconnect_quality=0.08, ecosystem_maturity=0.12,
    sla_satisfaction=0.08, production_readiness=0.06, benchmark_evidence=0.08,
)
WEIGHTS_RL = ScoringWeights(
    compute_perf=0.17, vram_sufficiency=0.22, cost_efficiency=0.10,
    power_efficiency=0.08, interconnect_quality=0.12, ecosystem_maturity=0.08,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)
WEIGHTS_INFER_FP16 = ScoringWeights(
    compute_perf=0.15, vram_sufficiency=0.20, cost_efficiency=0.15,
    power_efficiency=0.08, interconnect_quality=0.08, ecosystem_maturity=0.12,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.07,
)
WEIGHTS_INFER_QUANT = ScoringWeights(
    compute_perf=0.10, vram_sufficiency=0.25, cost_efficiency=0.17,
    power_efficiency=0.08, interconnect_quality=0.05, ecosystem_maturity=0.13,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.07,
)
TRAIN_WEIGHTS = WEIGHTS_SFT_FULL
INFERENCE_WEIGHTS = WEIGHTS_INFER_FP16

# Legacy _SCENARIO_KEY_MAP (backward compat)
_SCENARIO_KEY_MAP = {
    ("train", "cpt", "full_param"): WEIGHTS_CPT,
    ("train", "sft", "full_param"): WEIGHTS_SFT_FULL,
    ("train", "sft", "lora"): WEIGHTS_SFT_LORA,
    ("train", "rl", "full_param"): WEIGHTS_RL,
    ("quantize", "gptq", "int8"): WEIGHTS_QUANTIZE,
    ("quantize", "gptq", "int4"): WEIGHTS_QUANTIZE,
    ("quantize", "gptq", "fp8"): WEIGHTS_QUANTIZE,
    ("quantize", "awq", "int8"): WEIGHTS_QUANTIZE,
    ("quantize", "awq", "int4"): WEIGHTS_QUANTIZE,
    ("quantize", "awq", "fp8"): WEIGHTS_QUANTIZE,
    ("quantize", "bitsandbytes", "int8"): WEIGHTS_QUANTIZE,
    ("quantize", "bitsandbytes", "int4"): WEIGHTS_QUANTIZE,
    ("quantize", "bitsandbytes", "fp8"): WEIGHTS_QUANTIZE,
    ("quantize", "gguf", "int8"): WEIGHTS_QUANTIZE,
    ("quantize", "gguf", "int4"): WEIGHTS_QUANTIZE,
    ("quantize", "gguf", "fp8"): WEIGHTS_QUANTIZE,
    ("inference", "fp16"): WEIGHTS_INFER_FP16,
    ("inference", "int8"): WEIGHTS_INFER_QUANT,
    ("inference", "int4_gptq"): WEIGHTS_INFER_QUANT,
    ("inference", "int4_awq"): WEIGHTS_INFER_QUANT,
    ("inference", "gguf_q4"): WEIGHTS_INFER_QUANT,
    ("inference", "gguf_q8"): WEIGHTS_INFER_QUANT,
}
_SCENARIO_LABEL_MAP = {
    ("train", "cpt", "full_param"): "训练·CPT·全参",
    ("train", "sft", "full_param"): "训练·SFT·全参",
    ("train", "sft", "lora"): "训练·SFT·LoRA",
    ("train", "rl", "full_param"): "训练·RL·全参",
    ("quantize", "gptq", "int8"): "量化·GPTQ·INT8",
    ("quantize", "gptq", "int4"): "量化·GPTQ·INT4",
    ("quantize", "gptq", "fp8"): "量化·GPTQ·FP8",
    ("quantize", "awq", "int8"): "量化·AWQ·INT8",
    ("quantize", "awq", "int4"): "量化·AWQ·INT4",
    ("quantize", "awq", "fp8"): "量化·AWQ·FP8",
    ("quantize", "bitsandbytes", "int8"): "量化·bitsandbytes·INT8",
    ("quantize", "bitsandbytes", "int4"): "量化·bitsandbytes·INT4",
    ("quantize", "bitsandbytes", "fp8"): "量化·bitsandbytes·FP8",
    ("quantize", "gguf", "int8"): "量化·GGUF·INT8",
    ("quantize", "gguf", "int4"): "量化·GGUF·INT4",
    ("quantize", "gguf", "fp8"): "量化·GGUF·FP8",
    ("inference", "fp16"): "推理·FP16",
    ("inference", "int8"): "推理·INT8",
    ("inference", "int4_gptq"): "推理·INT4-GPTQ",
    ("inference", "int4_awq"): "推理·INT4-AWQ",
    ("inference", "gguf_q4"): "推理·GGUF Q4",
    ("inference", "gguf_q8"): "推理·GGUF Q8",
}

def get_scenario_weights(
    scenario: str,
    stage: TrainStage = "sft",
    method: str = "full_param",
    quant: str = "fp16",
    quantize_bits: str = "int4",
) -> tuple[ScoringWeights, str]:
    """Return (weights, display_label) for a given scenario configuration."""
    if scenario == "train":
        key = ("train", stage, method)
    elif scenario == "quantize":
        key = ("quantize", method, quantize_bits)
    else:
        key = ("inference", quant)
    return (
        _SCENARIO_KEY_MAP.get(key, TRAIN_WEIGHTS if scenario == "train" else
                               WEIGHTS_QUANTIZE if scenario == "quantize" else INFERENCE_WEIGHTS),
        _SCENARIO_LABEL_MAP.get(key, scenario),
    )


# ═══════════════════════════════════════════════════════════
# Dimension result type
# ═══════════════════════════════════════════════════════════

@dataclass
class DimensionResult:
    score: float = 0.0           # 0.0–10.0 raw score
    weight: float = 0.0          # configured weight
    weighted: float = 0.0        # score × weight
    detail: str = ""             # human-readable explanation
    raw_values: dict = field(default_factory=dict)  # intermediate values


@dataclass
class ScoringResult:
    total_score: float = 0.0     # 0–100
    categories: dict[str, CategoryResult] = field(default_factory=dict)
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)  # flat backward-compat
    version: str = "4.4.0"


@dataclass
class RecommendContext:
    """All data needed for scoring one chip."""
    chip: dict
    model_params_B: float
    scenario: str                     # "train" | "quantize" | "inference"
    stage: str = "sft"                # "cpt" | "sft" | "rl"
    method: str = "full_param"        # train: full_param/lora  |  quantize: gptq/awq/bitsandbytes/gguf
    quant: str = "fp16"               # inference: fp16/int8/int4_gptq/int4_awq/gguf_q4/gguf_q8
    quantize_bits: str = "int4"       # quantize: int8/int4/fp8
    min_vram_total: float = 0.0
    vram_formula: str = ""            # human-readable formula
    vram_cards: int = 0
    recommended_cards: int = 0
    fp16_tflops: float = 0.0
    model_bandwidth_gb_s: float = 0.0  # model bandwidth requirement (GB/s)
    training_tokens_T: float = 0.0
    target_training_days: Optional[float] = None
    target_tps: Optional[float] = None
    estimated_training_days: Optional[float] = None
    benchmark_count: int = 0
    max_benchmark_mfu: Optional[float] = None
    max_benchmark_tps: Optional[float] = None
    compat_verified_count: int = 0
    official_ratio: float = -1.0       # source credibility: official / total (from field_provenance)


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def parse_fp16(perf_str: str) -> float:
    """Extract BF16/FP16 TFLOPS from precision_perf string."""
    if not perf_str:
        return 0.0
    text = str(perf_str).replace(",", "").replace("，", "")
    for tag in ("BF16", "FP16", "FP16/BF16", "BF16/FP16", "bf16", "fp16"):
        m = re.search(rf"{re.escape(tag)}\s*[:=]?\s*([\d.]+)\s*T", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    # Fallback: INT8 TOPS → FP16 estimate (50%)
    m = re.search(r"INT8\s*[:=]?\s*([\d.]+)\s*T", text, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 0.5
    # Last resort: any TFLOPS mention
    m = re.search(r"([\d.]+)\s*TFLOP", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return 0.0


def parse_process_node(process_str: str) -> float:
    """Extract process node in nm from all formats found in the database.

    Handles:
      - "Xnm" / "X nm" / "Xnm (...)":  "5nm", "3nm (TSMC N3)"
      - TSMC naming:  "4NP", "4N" → 4.0
      - Pure number:  "7", "14", "5.0", "28.0"
      - Dual-node:    "5nm/6nm" → 5.0 (takes smaller, more advanced node)
      - Old µm-scale: "220.0", "180.0" → 220, 180

    Coverage: 1067/1093 chips (97.6%).
    """
    if not process_str:
        return 0.0
    text = str(process_str).strip()
    # Pattern 1: "Xnm" or "X nm" or "Xnm (details)"
    m = re.search(r"(\d+\.?\d*)\s*nm", text)
    if m:
        return float(m.group(1))
    # Pattern 2: TSMC naming "XN" or "XNP" (e.g., "4NP" → 4)
    m = re.search(r"^(\d+\.?\d*)\s*N", text)
    if m:
        return float(m.group(1))
    # Pattern 3: pure number, check if reasonable nm range (≤220)
    m = re.search(r"^(\d+\.?\d*)$", text)
    if m:
        v = float(m.group(1))
        if v <= 220:
            return v
    # Pattern 4: first number as fallback
    m = re.search(r"(\d+\.?\d*)", text)
    if m:
        v = float(m.group(1))
        if v <= 220:
            return v
    return 0.0


def _next_pow2(n: int) -> int:
    """Smallest power of 2 >= n."""
    if n <= 1:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def round_up_pow2(n: int) -> int:
    """Round n up to nearest power of 2 (for GPU cluster sizing)."""
    return _next_pow2(n)


def estimate_card_count(required_vram_gb: float, per_card_vram_gb: float) -> dict:
    """Return transparent VRAM-based card sizing without an arbitrary cap.

    The raw count uses mathematical ceiling.  In particular, an exact division
    such as 512GB / 128GB stays at four cards instead of being over-counted.
    The deployment count is then rounded to a power of two for the topology
    convention used by this recommendation engine.
    """
    required = max(0.0, float(required_vram_gb or 0.0))
    per_card = float(per_card_vram_gb or 0.0)
    if per_card <= 0:
        raise ValueError("per_card_vram_gb must be greater than zero")
    raw_cards = max(1, math.ceil(required / per_card))
    rounded_cards = round_up_pow2(raw_cards)
    return {
        "required_vram_gb": round(required, 1),
        "per_card_vram_gb": round(per_card, 1),
        "raw_cards": raw_cards,
        "rounded_cards": rounded_cards,
        "formula": (
            f"ceil({required:.1f} GB ÷ {per_card:.1f} GB/卡) = {raw_cards} 卡"
            f" → 向上取2的幂 = {rounded_cards} 卡"
        ),
    }


def estimate_inference_concurrency_cards(
    weight_vram_gb: float,
    per_request_kv_gb: float,
    concurrency: int,
    per_card_vram_gb: float,
    *,
    per_card_tps: float | None = None,
    per_request_tps: float | None = None,
) -> dict:
    """Size a shared inference deployment pool for target concurrency.

    Resident weights are loaded once across the model-parallel pool.  Each
    concurrent request contributes one peak KV allocation.  A throughput floor
    is applied only when both measured per-card throughput and an explicit
    per-request throughput target are available; there is no hidden 60-second
    latency assumption.
    """
    requests = max(1, int(concurrency or 1))
    weight = max(0.0, float(weight_vram_gb or 0.0))
    per_request_kv = max(0.0, float(per_request_kv_gb or 0.0))
    target_kv = per_request_kv * requests
    target_vram = weight + target_kv
    capacity = estimate_card_count(target_vram, per_card_vram_gb)

    throughput_raw = None
    throughput_rounded = None
    if per_card_tps and per_card_tps > 0 and per_request_tps and per_request_tps > 0:
        aggregate_tps = requests * per_request_tps
        throughput_raw = max(1, math.ceil(aggregate_tps / per_card_tps))
        throughput_rounded = round_up_pow2(throughput_raw)

    rounded = max(capacity["rounded_cards"], throughput_rounded or 1)
    formula = (
        f"目标并发显存 = 权重 {weight:.1f}GB + 单请求KV {per_request_kv:.3f}GB × "
        f"{requests} = {target_vram:.1f}GB；ceil({target_vram:.1f}÷{float(per_card_vram_gb):.1f})"
        f" = {capacity['raw_cards']}卡 → 取2的幂 = {capacity['rounded_cards']}卡"
    )
    if throughput_raw is not None:
        formula += (
            f"；吞吐约束 ceil({requests}×{per_request_tps:.1f}÷{per_card_tps:.1f})"
            f" = {throughput_raw}卡 → 取2的幂 = {throughput_rounded}卡；最终取较大值 = {rounded}卡"
        )

    return {
        "basis": "shared_pool_capacity",
        "weight_vram_gb": round(weight, 1),
        "per_request_kv_gb": round(per_request_kv, 3),
        "target_kv_gb": round(target_kv, 3),
        "target_vram_gb": round(target_vram, 1),
        "target_concurrency": requests,
        "capacity_raw_cards": capacity["raw_cards"],
        "capacity_rounded_cards": capacity["rounded_cards"],
        "throughput_raw_cards": throughput_raw,
        "throughput_rounded_cards": throughput_rounded,
        "raw_cards": max(capacity["raw_cards"], throughput_raw or 1),
        "rounded_cards": rounded,
        "formula": formula,
        "assumption": "按可统一分片和调度的显存池估算，常驻权重仅加载一份；实际后端若限制最大并行度，需再按实例复制校核。",
    }


# ═══════════════════════════════════════════════════════════
# Dimension #1: 算力性能 (Compute Performance) — weight 15-20%
# ═══════════════════════════════════════════════════════════

def score_compute_perf(fp16_tflops: float) -> DimensionResult:
    """Single-card FP16/BF16 compute throughput.

    Based on 996-chip distribution (1093 total, 91.1% coverage):
      P50=6.5T, P75=24T, P90=105T, P95=383T, P99=2563T
    Piecewise linear anchored at data percentiles:
      ≤13T(P50 area)→0-25, 13-105T→25-50, 105-383T→50-75, >383T→75-100 (capped at 2563=P99)

    Missing data → statistical mean 19 (996-chip average; actual mean is low because
    most chips are consumer GPUs with modest FP16). Prevents inflating scores for
    chips without published perf data — if they can't benchmark, they likely aren't
    strong compute chips.
    """
    MEAN = 19.0  # statistical mean of 996 chips (91.1% coverage)
    if fp16_tflops > 0:
        if fp16_tflops <= 13:
            score = fp16_tflops / 13.0 * 25.0
            detail = f"FP16={fp16_tflops:.0f}T (≤P50=13T) → {score:.0f}/100"
        elif fp16_tflops <= 105:
            score = 25.0 + (fp16_tflops - 13) / (105 - 13) * 25.0
            detail = f"FP16={fp16_tflops:.0f}T (P50-P90) → {score:.0f}/100"
        elif fp16_tflops <= 383:
            score = 50.0 + (fp16_tflops - 105) / (383 - 105) * 25.0
            detail = f"FP16={fp16_tflops:.0f}T (P90-P95) → {score:.0f}/100"
        else:
            score = min(75.0 + (fp16_tflops - 383) / (2563 - 383) * 25.0, 100.0)
            detail = f"FP16={fp16_tflops:.0f}T (>P95=383T) → {score:.0f}/100"
    else:
        score = MEAN
        detail = f"无FP16/BF16算力数据 → 统计均值 {MEAN:.0f}/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"fp16_tflops": fp16_tflops,
                    "formula": "piecewise: P50(13T)=25, P90(105T)=50, P95(383T)=75, P99(2563T)=100",
                    "source": "chip.precision_perf (996/1093 chips, 91% coverage)", "missing": fp16_tflops <= 0},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #2: 带宽充裕度 (Bandwidth Adequacy) — 计算大类内权重 10%
# ═══════════════════════════════════════════════════════════

def score_bandwidth_adequacy(vram_bw_gb_s: float, cards: int,
                              model_bandwidth_gb_s: float) -> DimensionResult:
    """Is total VRAM bandwidth sufficient for the model?

    total_bw = vram_bw_gb_s × cards   (aggregate bandwidth across all cards)
    adequacy = model_need / total_bw  (lower is better)

    Scoring: model needs ≤ 50% of total bandwidth → 100 (comfortable headroom)
             above 50% → linear decay: score = 100 × (1 - 2×(ratio - 0.5))
             i.e. at 50%→100, at 75%→50, at 100%→0

    Missing data → statistical mean 41 (962-chip average, 88% coverage).
    """
    MEAN = 41.0  # statistical mean of 962 chips (88% coverage)
    cards = max(cards, 1)
    bw = float(vram_bw_gb_s or 0)
    if bw <= 0:
        return DimensionResult(
            score=MEAN, detail=f"无显存带宽数据 → 统计均值 {MEAN:.0f}/100",
            raw_values={"vram_bw_gb_s": 0, "cards": cards,
                        "source": "chip.vram_bw_gb_s (962/1093 chips, 88% coverage)",
                        "missing": True},
        )
    total_bw = bw * cards
    if model_bandwidth_gb_s <= 0:
        # No bandwidth target → assume adequate
        score = 80.0
        detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s （无模型带宽需求目标） → 80/100"
    else:
        ratio = model_bandwidth_gb_s / total_bw
        if ratio <= 0.5:
            score = 100.0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s, 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}% → 满分100"
        elif ratio <= 1.0:
            score = 100.0 - (ratio - 0.5) * 200.0  # 0.5→100, 1.0→0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s, 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}%（>50%） → {score:.0f}/100"
        else:
            score = 0.0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s < 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}% → 0/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "vram_bw_gb_s": bw, "cards": cards,
            "total_bw_gb_s": round(total_bw, 1),
            "model_bandwidth_gb_s": round(model_bandwidth_gb_s, 1),
            "ratio": round(ratio, 3) if model_bandwidth_gb_s > 0 else 0,
            "formula": "total_bw = vram_bw × cards; ratio = model_need / total_bw; ≤50%→100, >50%→linear 100→0",
            "source": "chip.vram_bw_gb_s (88% coverage)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #3: 能效比 (Power Efficiency) — combined with server count
# ═══════════════════════════════════════════════════════════

def score_power_efficiency(fp16_tflops: float, tdp_w: float) -> DimensionResult:
    """GFLOPS per Watt. Based on 959-chip FP16×TDP cross-section (87.7% coverage).

    Actual data: mean=167 GFLOPS/W, median=76, P25=10, P75=160, P90=470.
    Piecewise: ≤130(P50 area)→0-25, 130-470(P90)→25-50, 470-1500→50-80, >1500→80-100(cap at 4500=top).
    Missing data → statistical mean 20 (959-chip average).
    """
    MEAN = 20.0  # statistical mean of 959 chips (87.7% coverage)
    tdp = float(tdp_w or 0)
    if fp16_tflops > 0 and tdp > 0:
        gf = fp16_tflops * 1000 / tdp  # GFLOPS/W
        if gf <= 130:
            score = gf / 130.0 * 25.0
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (≤P50=130) → {score:.0f}/100"
        elif gf <= 470:
            score = 25.0 + (gf - 130) / (470 - 130) * 25.0
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (P50-P90) → {score:.0f}/100"
        elif gf <= 1500:
            score = 50.0 + (gf - 470) / (1500 - 470) * 30.0
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (P90-H100) → {score:.0f}/100"
        else:
            score = min(80.0 + (gf - 1500) / 3000 * 20.0, 100.0)
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (>H100=1413) → {score:.0f}/100"
    else:
        score = MEAN
        gf = 0.0
        detail = f"无功耗或算力数据 → 统计均值 {MEAN:.0f}/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "gflops_per_watt": round(gf, 1), "tdp_w": tdp,
            "formula": "piecewise: P50(130)=25, P90(470)=50, H100(1413)=80, B200(2250)=100",
            "source": "chip.tdp_w (92% coverage) + chip.precision_perf",
            "missing": tdp <= 0 or fp16_tflops <= 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #4: 服务器节点效率 (Server Count Efficiency) — weight 40%
# 8卡（1节点）=10分，多节点扣分
# ═══════════════════════════════════════════════════════════

def score_server_count_efficiency(recommended_cards: int) -> DimensionResult:
    """8 cards per node = 100 (optimal), more nodes → linear decay.

    Formula: nodes = ceil(cards / 8)
    1 node (≤8 cards) → 100
    2 nodes (9-16) → 80
    3 nodes (17-24) → 60
    4 nodes (25-32) → 40
    5+ nodes → max(0, 20 - 5 per extra node above 4)
    """
    cards = max(recommended_cards, 1)
    nodes = (cards + 7) // 8  # ceiling division
    if nodes <= 1:
        score = 100.0
        detail = f"{cards}卡 = 1节点 → 满分 100/100"
    elif nodes <= 4:
        score = 100.0 - (nodes - 1) * 20.0  # 1→100, 2→80, 3→60, 4→40
        detail = f"{cards}卡 = {nodes}节点 → {score:.0f}/100"
    else:
        score = max(0.0, 40.0 - (nodes - 4) * 5.0)  # 5→35, 6→30, ...
        detail = f"{cards}卡 = {nodes}节点（>4） → {score:.0f}/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "recommended_cards": cards, "nodes": nodes,
            "formula": "nodes=ceil(cards/8); 1→100, 2→80, 3→60, 4→40, >4→-5/node",
        },
    )


# ── Framework and toolchain lists ──

_MAJOR_FRAMEWORKS = ["pytorch", "tensorflow", "jax", "mindspore", "paddlepaddle", "vllm", "onnx"]
_MINOR_FRAMEWORKS = ["deepspeed", "megatron", "fsdp", "tensorrt", "openvino", "triton",
                      "llama.cpp", "sglang", "lmdeploy", "text-generation-inference"]


# ═══════════════════════════════════════════════════════════
# Dimension #6: 框架兼容 (Framework Compatibility) — weight 15%
# Major frameworks only
# ═══════════════════════════════════════════════════════════

def score_framework_compat(software_stack: str, compatible_frameworks: str) -> DimensionResult:
    """Major framework support. 7 frameworks × 15, capped at 100.
    No data → default 50.
    """
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    major_hits = sum(1 for fw in _MAJOR_FRAMEWORKS if fw in fw_text)
    if major_hits == 0:
        score = 50.0
        detail = "未检测到主流框架支持 → 默认 50/100"
    else:
        score = min(major_hits * 15.0, 100.0)
        detail = f"主流框架×{major_hits} (+{major_hits*15:.0f}) = {score:.0f}/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "major_frameworks_found": major_hits,
            "formula": "min(major*15, 100); no-data→50",
            "source": "chip.software_stack + chip.compatible_frameworks",
            "missing": major_hits == 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #7: 工具链兼容 (Toolchain Compatibility) — weight 15%
# Minor toolchains only
# ═══════════════════════════════════════════════════════════

def score_toolchain_compat(software_stack: str, compatible_frameworks: str) -> DimensionResult:
    """Minor toolchain support. 10 tools × 8, capped at 80.
    No data → default 50.
    """
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    minor_hits = sum(1 for fw in _MINOR_FRAMEWORKS if fw in fw_text)
    if minor_hits == 0:
        score = 50.0
        detail = "未检测到工具链支持 → 默认 50/100"
    else:
        score = min(minor_hits * 8.0, 80.0)
        detail = f"工具链×{minor_hits} (+{minor_hits*8:.0f}) = {score:.0f}/100"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "minor_tools_found": minor_hits,
            "formula": "min(minor*8, 80); no-data→50",
            "source": "chip.software_stack + chip.compatible_frameworks",
            "missing": minor_hits == 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #8: 实测验证度 (Benchmark Evidence) — weight 20%
# ═══════════════════════════════════════════════════════════

def score_benchmark_evidence(benchmark_count: int, max_mfu: Optional[float],
                              max_tps: Optional[float]) -> DimensionResult:
    """Reward chips with real benchmark data. No data → 50 (neutral default).

    Count-based: 0→50, 1-3→55-65, 4-13→65-85, 13+→85-95.
    Modest MFU/TPS bonus (±2.5 max) rather than multiplier.
    """
    if benchmark_count == 0:
        return DimensionResult(score=50.0, detail="无实测benchmark数据 → 默认 50/100",
                               raw_values={"benchmark_count": 0,
                                           "source": "chip_model_benchmarks (33 matched / 430 total, 3% coverage)",
                                           "missing": True})

    # Count-based base score (modest range)
    if benchmark_count <= 3:
        base = 50.0 + benchmark_count / 3.0 * 15.0  # 1→55, 2→60, 3→65
    elif benchmark_count <= 13:
        base = 65.0 + (benchmark_count - 3) / 10.0 * 20.0  # 4→67, 13→85
    else:
        base = min(85.0 + (benchmark_count - 13) / 87.0 * 10.0, 95.0)  # capped at 95

    # Modest MFU bonus (±2.5)
    mfu = float(max_mfu or 0)
    mfu_bonus = 0.0
    if mfu > 40:
        mfu_bonus = 2.5
    elif mfu > 0:
        mfu_bonus = -2.5

    # Modest TPS bonus (±2.5)
    tps = float(max_tps or 0)
    tps_bonus = 0.0
    if tps > 500:
        tps_bonus = 2.5
    elif tps > 0:
        tps_bonus = -2.5

    score = min(base + mfu_bonus + tps_bonus, 100.0)

    detail = f"{benchmark_count}条实测"
    if mfu > 0: detail += f" MFU={mfu:.0f}%"
    if tps > 0: detail += f" TPS={tps:.0f}"
    detail += f" → {score:.0f}/100"

    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "benchmark_count": benchmark_count, "max_mfu_pct": mfu,
            "max_throughput_tok_s": tps,
            "base_score": round(base, 2), "mfu_bonus": round(mfu_bonus, 2),
            "tps_bonus": round(tps_bonus, 2),
            "formula": "count-base(50-95) + MFU/TPS bonus(±2.5); no-data→50",
            "source": "chip_model_benchmarks",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #9: 来源真实度 (Source Credibility) — weight 10%
# ═══════════════════════════════════════════════════════════

def score_source_credibility(official_ratio: float) -> DimensionResult:
    """How much of the chip's data comes from official sources.

    official_ratio = official_fields / total_fields (from field_provenance)
    ≥80% → 100, ≥60% → 80, ≥40% → 70, ≥20% → 60, <20% → 50
    No provenance data → 50 (neutral, data comes from web_crawl mostly)
    """
    if official_ratio < 0:
        return DimensionResult(
            score=50.0, detail="无来源溯源数据 → 默认 50/100",
            raw_values={"official_ratio": 0, "missing": True},
        )
    if official_ratio >= 0.80:
        score = 100.0
        detail = f"官方来源 {official_ratio*100:.0f}%（≥80%）→ 100/100"
    elif official_ratio >= 0.60:
        score = 80.0
        detail = f"官方来源 {official_ratio*100:.0f}%（60-80%）→ 80/100"
    elif official_ratio >= 0.40:
        score = 70.0
        detail = f"官方来源 {official_ratio*100:.0f}%（40-60%）→ 70/100"
    elif official_ratio >= 0.20:
        score = 60.0
        detail = f"官方来源 {official_ratio*100:.0f}%（20-40%）→ 60/100"
    else:
        score = 50.0
        detail = f"官方来源 {official_ratio*100:.0f}%（<20%）→ 50/100"
    return DimensionResult(
        score=score, detail=detail,
        raw_values={
            "official_ratio": round(official_ratio, 3),
            "formula": "official_fields/total_fields; ≥80%=100, ≥60%=80, ≥40%=70, ≥20%=60, <20%=50",
            "source": "field_provenance table — source_type + is_official",
        },
    )


# ═══════════════════════════════════════════════════════════
# Aggregator — compute all dimensions and weighted total
# ═══════════════════════════════════════════════════════════

def aggregate_score(
    ctx: RecommendContext,
    cat_weights: CategoryWeights,
) -> ScoringResult:
    """v4.4: Compute all dimension scores, aggregate into 4 categories.

    Formula:
      total = Σ(Cat_score × Cat_weight)    (0-100 scale)
    where each category score = Σ(sub_dim_score × sub_weight) within category.
    """

    chip = ctx.chip

    # ── Step 1: Compute all 8 sub-dimension scores ──

    dims: dict[str, DimensionResult] = {}

    # D1: 算力性能
    dims["compute_perf"] = score_compute_perf(ctx.fp16_tflops)

    # D2: 带宽充裕度
    dims["bandwidth_adequacy"] = score_bandwidth_adequacy(
        float(chip.get("vram_bw_gb_s", 0) or 0),
        ctx.recommended_cards,
        ctx.model_bandwidth_gb_s,
    )

    # D3: 能效比
    dims["power_efficiency"] = score_power_efficiency(
        ctx.fp16_tflops, float(chip.get("tdp_w", 0) or 0),
    )

    # D4: 服务器节点效率
    dims["server_count_efficiency"] = score_server_count_efficiency(ctx.recommended_cards)

    # D5: 框架兼容
    dims["framework_compat"] = score_framework_compat(
        str(chip.get("software_stack", "") or ""),
        str(chip.get("compatible_frameworks", "") or ""),
    )

    # D6: 工具链兼容
    dims["toolchain_compat"] = score_toolchain_compat(
        str(chip.get("software_stack", "") or ""),
        str(chip.get("compatible_frameworks", "") or ""),
    )

    # D7: 实测验证度
    dims["benchmark_evidence"] = score_benchmark_evidence(
        ctx.benchmark_count, ctx.max_benchmark_mfu, ctx.max_benchmark_tps,
    )

    # D8: 来源真实度
    dims["source_credibility"] = score_source_credibility(ctx.official_ratio)

    # ── Step 2: Aggregate into 4 categories ──

    categories: dict[str, CategoryResult] = {}
    total = 0.0

    for cat_def in CATEGORY_DEFS:
        cat_id = cat_def["id"]
        sub_dims = cat_def["sub_dims"]

        # Compute category score = weighted average of sub-dimensions (0-10)
        cat_score = 0.0
        sub_results: dict[str, DimensionResult] = {}
        formula_parts = []
        for dim_id, sub_w in sub_dims.items():
            dr = dims[dim_id]
            # Sub-weight is the fraction within the category; record as weight for display
            dr.weight = sub_w
            dr.weighted = round(dr.score * sub_w, 4)
            cat_score += dr.score * sub_w
            sub_results[dim_id] = dr
            # Build formula label (short dim names)
            short_names = {
                "compute_perf": "D1算力", "bandwidth_adequacy": "D2带宽",
                "power_efficiency": "D3能效", "server_count_efficiency": "D4节点",
                "framework_compat": "D5框架", "toolchain_compat": "D6工具",
                "benchmark_evidence": "D7实测", "source_credibility": "D8来源",
            }
            sn = short_names.get(dim_id, dim_id)
            formula_parts.append(f"{sn}×{sub_w:.0%}")

        cat_score = round(cat_score, 2)
        cat_weight = getattr(cat_weights, cat_id)
        cat_weighted = round(cat_score * cat_weight, 4)

        cr = CategoryResult(
            id=cat_id,
            name_cn=cat_def["name_cn"],
            name_en=cat_def["name_en"],
            score=cat_score,
            weight=cat_weight,
            weighted=cat_weighted,
            sub_dimensions=sub_results,
            formula=" + ".join(formula_parts),
        )
        categories[cat_id] = cr
        total += cat_weighted

    return ScoringResult(
        total_score=round(total, 1),  # direct weighted sum (0-100)
        categories=categories,
        dimensions=dims,         # flat backward-compat
        version="4.4.0",
    )


# ═══════════════════════════════════════════════════════════
# Serialization helpers
# ═══════════════════════════════════════════════════════════

def scoring_result_to_dict(sr: ScoringResult) -> dict:
    """v4.0: Convert ScoringResult to API-ready dict with nested categories."""
    # Build categories output
    cats_out = {}
    for cat_id, cr in sr.categories.items():
        sub_out = {}
        for dim_id, dr in cr.sub_dimensions.items():
            sub_out[dim_id] = {
                "score": dr.score,
                "weight": dr.weight,      # sub-weight within category
                "weighted": dr.weighted,
                "detail": dr.detail,
                "raw_values": dr.raw_values,
            }
        cats_out[cat_id] = {
            "score": cr.score,
            "weight": cr.weight,          # category-level weight
            "weighted": cr.weighted,
            "name_cn": cr.name_cn,
            "name_en": cr.name_en,
            "sub_dimensions": sub_out,
            "formula": cr.formula,
        }

    # Build flat dimensions (backward compat)
    dims_out = {}
    for dim_id, dr in sr.dimensions.items():
        dims_out[dim_id] = {
            "score": dr.score,
            "weight": dr.weight,          # sub-weight (within category) or 0
            "weighted": dr.weighted,
            "detail": dr.detail,
            "raw_values": dr.raw_values,
        }

    # Build category weights dict for display
    cw = {}
    for cat_id, cr in sr.categories.items():
        cw[cat_id] = cr.weight

    return {
        "total": sr.total_score,
        "categories": cats_out,
        "dimensions": dims_out,        # flat backward-compat
        "category_weights": cw,
        "version": sr.version,
    }


# ── Dimension metadata for documentation / UI ──

DIMENSION_META = [
    {"id": "compute_perf",          "name_cn": "算力性能",     "name_en": "Compute Performance",
     "desc": "单卡FP16/BF16理论算力(TFLOPS)", "unit": "TFLOPS",
     "explain_cn": "看单卡FP16/BF16理论算力；分数越高，单卡计算能力通常越强。",
     "category": "compute_power", "sub_weight": 0.90},
    {"id": "bandwidth_adequacy",    "name_cn": "带宽充裕度",   "name_en": "Bandwidth Adequacy",
     "desc": "总显存带宽(vram_bw×卡数)相对模型带宽需求的充裕程度", "unit": "比率",
     "explain_cn": "看推荐卡的总显存带宽能否覆盖模型需求；余量越大，越不容易受访存瓶颈限制，也越能应对更多并发请求。",
     "category": "compute_power", "sub_weight": 0.10},
    {"id": "power_efficiency",      "name_cn": "能效比",      "name_en": "Power Efficiency",
     "desc": "每瓦功耗产出的算力", "unit": "GFLOPS/W",
     "explain_cn": "看每瓦功耗可提供多少算力；分数越高，同等计算量的电力和散热成本通常越低。",
     "category": "cost_effectiveness", "sub_weight": 0.60},
    {"id": "server_count_efficiency","name_cn": "节点效率",    "name_en": "Server Count Efficiency",
     "desc": "8卡=1节点满分，多节点扣分", "unit": "节点数",
     "explain_cn": "按每节点8卡估算部署规模；节点越少，互联、运维和部署复杂度通常越低。",
     "category": "cost_effectiveness", "sub_weight": 0.40},
    {"id": "framework_compat",      "name_cn": "框架兼容",     "name_en": "Framework Compatibility",
     "desc": "主流框架支持数(PyTorch/TF/JAX等)", "unit": "框架数",
     "explain_cn": "看PyTorch、TensorFlow、JAX等主流框架的支持情况；支持越多，模型迁移和部署越容易。",
     "category": "ecosystem_maturity", "sub_weight": 0.40},
    {"id": "toolchain_compat",      "name_cn": "工具链兼容",   "name_en": "Toolchain Compatibility",
     "desc": "辅助工具链支持数(DeepSpeed/TensorRT等)", "unit": "工具数",
     "explain_cn": "看DeepSpeed、TensorRT、Triton等辅助工具的支持情况；支持越完整，训练和推理优化越方便。",
     "category": "ecosystem_maturity", "sub_weight": 0.40},
    {"id": "source_credibility",    "name_cn": "来源真实度",   "name_en": "Source Credibility",
     "desc": "数据来源中官方资料占比", "unit": "比率",
     "explain_cn": "看评分数据中官方资料所占比例；分数越高，评分所依据的数据通常越可信。",
     "category": "ecosystem_maturity", "sub_weight": 0.20},
    {"id": "benchmark_evidence",    "name_cn": "实测验证度",   "name_en": "Benchmark Evidence",
     "desc": "是否有实测benchmark数据(MFU/吞吐)", "unit": "证据分",
     "explain_cn": "看真实benchmark的数量和质量；分数越高，越能用实测结果验证理论规格。",
     "category": "benchmark_evidence", "sub_weight": 1.0},
]
