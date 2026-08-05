"""
AISHPerf Chip Recommendation Scoring Engine v3.0

10-dimension scoring with each dimension outputting 0.0-10.0,
weighted sum yields total 0-100.

v3.0 changes:
  - Removed maturity_level dimension (too abstract for chip selection)
  - Added fine-grained scenarios: train stage (CPT/SFT/RL) × method (full_param/LoRA/QLoRA)
  - Added quantization-aware inference (INT8/INT4/GPTQ/AWQ/GGUF)
  - VRAM formulas per scenario with different bytes-per-param coefficients
  - 7 scenario-specific weight presets replacing old TRAIN/INFERENCE dichotomy

All formulas have clear physical meaning, documented constants,
and return traces for transparency.
"""

from __future__ import annotations

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

def estimate_vram_total(
    params_B: float,
    scenario: str,                    # "train" | "quantize" | "inference"
    stage: TrainStage = "sft",
    method: str = "full_param",       # train: full_param/lora  |  quantize: gptq/awq/bitsandbytes/gguf
    quant: str = "fp16",              # inference: fp16/int8/int4_gptq/int4_awq/gguf_q4/gguf_q8
    quantize_bits: str = "int4",      # quantize: int8/int4/fp8
    moe_activated_B: float | None = None,
) -> tuple[float, str]:
    """Estimate total VRAM needed (GB) for a model under a scenario.

    Returns (vram_gb, formula_description).
    """
    P = params_B

    if scenario == "train":
        safety = 1.25
        if stage == "cpt":
            # CPT: weights(2) + gradients(2) + Adam m+v(8) + activations(6-8) ≈ 20 bytes/param
            bytes_per = 20.0
            label = "CPT"
        elif stage == "sft":
            if method == "full_param":
                # SFT full-param: same as CPT — optimizer states + gradients
                bytes_per = 20.0
                label = "SFT(全参)"
            elif method == "lora":
                # LoRA: load full frozen weights(2) + tiny trainable adapter + no optimizer for base
                # ~2.5 bytes/param for the frozen base + LoRA overhead
                bytes_per = 2.5
                label = "SFT(LoRA)"
            else:
                bytes_per = 20.0
                label = "SFT(full_param)"
        elif stage == "rl":
            # RL (PPO/GRPO): Actor(2) + Critic(2) + Ref model(2) + optimizer states(8-12)
            # ≈ 22-30 bytes/param for 2-3 model copies + optimizer
            bytes_per = 25.0
            label = "RL(PPO/GRPO)"
        else:
            bytes_per = 20.0
            label = "训练(full)"

        # MoE training: all experts need to be loaded
        effective_P = P
        if moe_activated_B and moe_activated_B < P:
            effective_P = min(P, moe_activated_B * 2.0)  # compromise for MoE training

        vram = effective_P * bytes_per * safety
        return round(vram, 1), f"{label}: {effective_P:.1f}B × {bytes_per} bytes/param × {safety} = {vram:.0f}GB"

    elif scenario == "quantize":
        # ── Quantization scenario (v3.1): needs training-capable chips ──
        # Must hold FP16 full model + calibration data structures in VRAM.
        # Different methods need different overhead:
        #   GPTQ: needs Hessian matrix (~1.5× per layer being processed)
        #   AWQ: needs activation statistics buffer (~1.0×)
        #   bitsandbytes: lightweight, mainly the model + quant buffers (~0.5×)
        #   GGUF: needs calibration dataset batches (~0.5×)
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

        # Quantization processes the full model (not just activated experts for MoE),
        # since every layer needs calibration passes.
        effective_P = P

        vram = effective_P * bytes_per * safety
        return round(vram, 1), f"{label}: {effective_P:.1f}B × {bytes_per} bytes/param × {safety} = {vram:.0f}GB"

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

        # MoE inference: only activated experts
        effective_P = moe_activated_B if moe_activated_B and moe_activated_B < P else P

        vram = effective_P * bytes_per * safety
        return round(vram, 1), f"推理({quant_label}): {effective_P:.1f}B × {bytes_per} bytes/param × {safety} = {vram:.0f}GB"


def estimate_training_flops(params_B: float, tokens_T: float) -> float:
    """Total FLOPs for training: 6 × P × tokens.

    Returns FLOPs (float).
    """
    return 6 * (params_B * 1e9) * (tokens_T * 1e12)


# ═══════════════════════════════════════════════════════════
# Configuration — scoring weights (v3.0: 7 scenario presets)
# ═══════════════════════════════════════════════════════════

@dataclass
class ScoringWeights:
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
        """Apply TCO weight and scale remaining to sum to 1.0."""
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


# ── v3.0 Scenario-specific weight presets ──
# Each preset adjusts which dimensions matter most for that use case.

# CPT: heavy compute + interconnect (distributed training)
WEIGHTS_CPT = ScoringWeights(
    compute_perf=0.22, vram_sufficiency=0.15, cost_efficiency=0.10,
    power_efficiency=0.07, interconnect_quality=0.15, ecosystem_maturity=0.08,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)

# SFT full-param: balanced training
WEIGHTS_SFT_FULL = ScoringWeights(
    compute_perf=0.18, vram_sufficiency=0.18, cost_efficiency=0.12,
    power_efficiency=0.08, interconnect_quality=0.10, ecosystem_maturity=0.10,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.09,
)

# SFT LoRA: VRAM-light, more weight on cost + ecosystem
WEIGHTS_SFT_LORA = ScoringWeights(
    compute_perf=0.12, vram_sufficiency=0.25, cost_efficiency=0.15,
    power_efficiency=0.08, interconnect_quality=0.05, ecosystem_maturity=0.12,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)

# SFT QLoRA: removed from train methods in v3.1 — quantization is now its own scenario
# (see WEIGHTS_QUANTIZE below)

# Quantize scenario: VRAM-dominant (must hold FP16 model + calibration structures),
# needs training-capable hardware for forward+backward calibration passes.
WEIGHTS_QUANTIZE = ScoringWeights(
    compute_perf=0.12,          # calibration computation needs some compute
    vram_sufficiency=0.28,      # most critical — FP16 model + calibration data in VRAM
    cost_efficiency=0.12,       # one-time cost, moderate importance
    power_efficiency=0.06,      # one-shot task, power less important
    interconnect_quality=0.08,  # only 70B+ models need multi-card quantization
    ecosystem_maturity=0.12,    # AutoGPTQ/AutoAWQ/bitsandbytes/llama.cpp toolchain support
    sla_satisfaction=0.08,      # one-shot task, SLA is soft
    production_readiness=0.06,
    benchmark_evidence=0.08,
)

# RL: multi-model copies → VRAM + interconnect critical
WEIGHTS_RL = ScoringWeights(
    compute_perf=0.17, vram_sufficiency=0.22, cost_efficiency=0.10,
    power_efficiency=0.08, interconnect_quality=0.12, ecosystem_maturity=0.08,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.08,
)

# Inference FP16: VRAM + ecosystem + cost
WEIGHTS_INFER_FP16 = ScoringWeights(
    compute_perf=0.15, vram_sufficiency=0.20, cost_efficiency=0.15,
    power_efficiency=0.08, interconnect_quality=0.08, ecosystem_maturity=0.12,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.07,
)

# Inference quantized (INT8/INT4): VRAM light, cost + ecosystem dominant
WEIGHTS_INFER_QUANT = ScoringWeights(
    compute_perf=0.10, vram_sufficiency=0.25, cost_efficiency=0.17,
    power_efficiency=0.08, interconnect_quality=0.05, ecosystem_maturity=0.13,
    sla_satisfaction=0.10, production_readiness=0.05, benchmark_evidence=0.07,
)

# Legacy backward-compat aliases
TRAIN_WEIGHTS = WEIGHTS_SFT_FULL    # default train = SFT full-param
INFERENCE_WEIGHTS = WEIGHTS_INFER_FP16

# ── Lookup table (v3.1: qlora removed from train, quantize scenario added) ──
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
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    version: str = "3.0.0"


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
    training_tokens_T: float = 0.0
    target_training_days: Optional[float] = None
    target_tps: Optional[float] = None
    estimated_training_days: Optional[float] = None
    benchmark_count: int = 0
    max_benchmark_mfu: Optional[float] = None
    max_benchmark_tps: Optional[float] = None
    compat_verified_count: int = 0


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
    """Extract process node in nm (e.g., '4nm' → 4.0, '5nm/6nm' → 5.0)."""
    if not process_str:
        return 0.0
    text = str(process_str).strip()
    m = re.search(r"(\d+\.?\d*)\s*nm", text)
    if m:
        return float(m.group(1))
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


# ═══════════════════════════════════════════════════════════
# Dimension #1: 算力性能 (Compute Performance) — weight 15-20%
# ═══════════════════════════════════════════════════════════

def score_compute_perf(fp16_tflops: float) -> DimensionResult:
    """Single-card FP16/BF16 compute throughput.

    1000 TFLOPS = 10/10 (reference: B200 ~2250 TFLOPS via FP8).
    """
    if fp16_tflops > 0:
        score = min(fp16_tflops / 100.0, 10.0)
        detail = f"FP16={fp16_tflops:.0f}TFLOPS → {score:.1f}/10 (满分1000T)"
    else:
        score = 0.0
        detail = "无FP16/BF16算力数据 → 0/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"fp16_tflops": fp16_tflops, "formula": "min(fp16/100, 10)"},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #2: 显存充裕度 (VRAM Sufficiency) — weight 12-20%
# ═══════════════════════════════════════════════════════════

def score_vram_sufficiency(vram_gb: float, model_vram_total: float, vram_cards: int) -> DimensionResult:
    """How much VRAM headroom per card vs what the model needs.

    Uses `vram_cards` (VRAM-only constraint) to compute per-card need,
    NOT recommended_cards (which may include compute constraint amplification).
    """
    per_card_need = model_vram_total / max(vram_cards, 1)
    ratio = vram_gb / max(per_card_need, 0.1)
    if ratio > 0:
        score = 10.0 * (1.0 - math.exp(-0.5 * ratio))
        detail = f"显存{vram_gb:.0f}GB / 需求{per_card_need:.0f}GB = {ratio:.1f}× → {score:.1f}/10"
    else:
        score = 0.0
        detail = "无法计算显存充裕度"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "vram_gb": vram_gb, "per_card_need_gb": round(per_card_need, 1),
            "vram_cards": vram_cards, "headroom_ratio": round(ratio, 2),
            "formula": "10*(1-exp(-0.5*headroom_ratio))",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #3: 价格经济性 (Cost Efficiency) — weight 12-15%
# ═══════════════════════════════════════════════════════════

def score_cost_efficiency(fp16_tflops: float, price_cny_wan: float) -> DimensionResult:
    """TFLOPS per 万 CNY. 5 TFLOPS/万 = 10/10.

    No price → neutral 5.0 (don't penalize unlisted chips).
    """
    price = float(price_cny_wan or 0)
    if price > 0 and fp16_tflops > 0:
        tflops_per_wan = fp16_tflops / price
        score = min(tflops_per_wan / 5.0, 10.0)
        detail = f"{fp16_tflops:.0f}T / {price:.1f}万元 = {tflops_per_wan:.1f} TFLOPS/万元 → {score:.1f}/10"
    elif fp16_tflops > 0:
        score = 5.0
        tflops_per_wan = None
        detail = "无价格数据 → 中性分 5.0/10"
    else:
        score = 0.0
        tflops_per_wan = None
        detail = "无算力+无价格 → 0/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "tflops_per_wan": round(tflops_per_wan, 2) if tflops_per_wan else None,
            "price_cny_wan": price, "formula": "min(tflops_per_wan/5, 10)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #4: 能效比 (Power Efficiency) — weight 8%
# ═══════════════════════════════════════════════════════════

def score_power_efficiency(fp16_tflops: float, tdp_w: float) -> DimensionResult:
    """GFLOPS per Watt. 700 GFLOPS/W = 10/10.

    Reference: B200 2250T/1000W = 2250 GFLOPS/W (capped), H100 989T/700W = 1413 GFLOPS/W.
    """
    tdp = float(tdp_w or 0)
    if fp16_tflops > 0 and tdp > 0:
        flops_per_watt = fp16_tflops * 1000 / tdp  # GFLOPS/W
        score = min(flops_per_watt / 700.0, 10.0)
        detail = f"{fp16_tflops:.0f}T / {tdp:.0f}W = {flops_per_watt:.0f} GFLOPS/W → {score:.1f}/10"
    elif fp16_tflops > 0:
        score = 3.0
        flops_per_watt = 0.0
        detail = "无功耗数据 → 保守 3.0/10"
    else:
        score = 0.0
        flops_per_watt = 0.0
        detail = "无算力+无功耗 → 0/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "gflops_per_watt": round(flops_per_watt, 1), "tdp_w": tdp,
            "formula": "min(fp16*1000/tdp/700, 10)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #5: 互联扩展性 (Interconnect Quality) — weight 8-12%
# ═══════════════════════════════════════════════════════════

def score_interconnect_quality(bw_gb_s: float, tech: str) -> DimensionResult:
    """Multi-card scalability via interconnect bandwidth + technology tier.

    BW: 200 GB/s → 1pt, 1400 GB/s → 7pt (capped).
    Tech: NVLink +3, HCCS +2.5, Infinity Fabric/ICI/MatrixLink/C2C +2, other +1.
    """
    bw = float(bw_gb_s or 0)
    tech = (tech or "").strip()
    bw_score = min(bw / 200.0, 7.0)

    tech_lower = tech.lower()
    if "nvlink" in tech_lower:
        tech_bonus = 3.0
        tech_label = "NVLink +3"
    elif "hccs" in tech_lower:
        tech_bonus = 2.5
        tech_label = "HCCS +2.5"
    elif any(t in tech_lower for t in ["infinity fabric", "ici", "matrixlink", "c2c"]):
        tech_bonus = 2.0
        tech_label = f"{tech[:30]} +2.0"
    elif tech:
        tech_bonus = 1.0
        tech_label = f"{tech[:30]} +1.0"
    else:
        tech_bonus = 0.0
        tech_label = "无互联技术"

    score = min(bw_score + tech_bonus, 10.0)
    detail = f"带宽{bw:.0f}GB/s({bw_score:.1f}) + {tech_label} = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "interconnect_bw_gb_s": bw, "bw_score": round(bw_score, 2),
            "tech": tech, "tech_bonus": tech_bonus,
            "formula": "min(bw/200 + tech_bonus, 10)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #6: 生态成熟度 (Ecosystem Maturity) — weight 8-13%
# ═══════════════════════════════════════════════════════════

def score_ecosystem_maturity(cloud: int, compat_verified: int,
                             has_frameworks: bool = False) -> DimensionResult:
    """Composite: cloud support (+3) + verified compat (+4 max, 0.8/ea) + frameworks (+3).

    Max: 3 + 4 + 3 = 10.0. (v3.0: removed maturity_level — too abstract)
    """
    cloud_val = int(float(cloud or 0))
    cloud_score = 3.0 if cloud_val >= 1 else 0.0
    compat_score = min(compat_verified * 0.8, 4.0)
    fw_score = 3.0 if has_frameworks else 0.0

    score = min(cloud_score + compat_score + fw_score, 10.0)
    parts = []
    if cloud_score: parts.append(f"云可用+{cloud_score:.0f}")
    if compat_score > 0: parts.append(f"兼容{compat_verified}条→+{compat_score:.1f}")
    if fw_score: parts.append(f"框架+{fw_score:.0f}")
    detail = " + ".join(parts) + f" = {score:.1f}/10" if parts else "无生态数据 → 0/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "cloud_available": cloud_val,
            "compat_verified_count": compat_verified,
            "has_frameworks": has_frameworks,
            "formula": "min(cloud*3 + min(compat*0.8,4) + fw*3, 10)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #7: SLA 满足度 (SLA Satisfaction) — weight 10%
# ═══════════════════════════════════════════════════════════

def score_sla_train(estimated_days: Optional[float], target_days: float) -> DimensionResult:
    """Training SLA: distance from deadline.
    Exactly on-target → 5, 50% ahead → 7.5, 100%+ ahead → 10.
    Miss → linear penalty to 0.
    """
    if target_days is None or target_days <= 0:
        return DimensionResult(score=5.0, detail="无训练天数SLA → 中性 5.0/10",
                               raw_values={"note": "no_target"})
    if estimated_days is None:
        return DimensionResult(score=0.0, detail=f"无法估算训练天数 → 0/10",
                               raw_values={"target_days": target_days})
    if estimated_days <= target_days:
        margin = (target_days - estimated_days) / target_days
        score = min(5.0 + margin * 5.0, 10.0)
        detail = f"预计{estimated_days:.1f}天 / 目标{target_days:.0f}天 → 提前{margin*100:.0f}% → {score:.1f}/10"
    else:
        shortfall = (estimated_days - target_days) / target_days
        score = max(0.0, 5.0 - shortfall * 5.0)
        detail = f"预计{estimated_days:.1f}天 / 目标{target_days:.0f}天 → 超出{shortfall*100:.0f}% → {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"estimated_days": estimated_days, "target_days": target_days,
                     "formula": "5 + margin*5 (on-time) or 5 - shortfall*5 (miss)"},
    )


def score_sla_inference(estimated_tps: Optional[float], target_tps: float) -> DimensionResult:
    """Inference SLA: throughput vs target. Same structure as training SLA."""
    if target_tps is None or target_tps <= 0:
        return DimensionResult(score=5.0, detail="无推理吞吐SLA → 中性 5.0/10",
                               raw_values={"note": "no_target"})
    if estimated_tps is None:
        return DimensionResult(score=0.0, detail=f"无法估算推理吞吐 → 0/10",
                               raw_values={"target_tps": target_tps})
    if estimated_tps >= target_tps:
        margin = (estimated_tps - target_tps) / target_tps
        score = min(5.0 + margin * 5.0, 10.0)
        detail = f"估算{estimated_tps:.0f}tok/s / 目标{target_tps:.0f}tok/s → 超出{margin*100:.0f}% → {score:.1f}/10"
    else:
        shortfall = (target_tps - estimated_tps) / target_tps
        score = max(0.0, 5.0 - shortfall * 5.0)
        detail = f"估算{estimated_tps:.0f}tok/s / 目标{target_tps:.0f}tok/s → 不足{shortfall*100:.0f}% → {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"estimated_tps": estimated_tps, "target_tps": target_tps,
                     "formula": "5 + margin*5 (satisfied) or 5 - shortfall*5 (miss)"},
    )


def estimate_inference_tps(fp16_tflops: float, vram_bw_gb_s: float, total_params_B: float) -> Optional[float]:
    """Theoretical single-card inference throughput (tok/s) for a model.

    Bound by min(compute, memory bandwidth), then apply 30% real-world efficiency.
    """
    if fp16_tflops <= 0 or total_params_B <= 0:
        return None
    # Compute bound: each token needs 2*params FLOPs
    compute_bound = fp16_tflops * 1e12 / (2 * total_params_B * 1e9)  # tok/s
    # Memory bound: each token reads 2*params bytes from VRAM
    bw = float(vram_bw_gb_s or 0)
    if bw > 0:
        memory_bound = bw * 1e9 / (2 * total_params_B * 1e9)
    else:
        memory_bound = float('inf')
    theoretical = min(compute_bound, memory_bound)
    return theoretical * 0.30  # 30% practical efficiency


# ═══════════════════════════════════════════════════════════
# Dimension #8: 生产就绪度 (Production Readiness) — weight 5%
# ═══════════════════════════════════════════════════════════

def score_production_readiness(status: str, is_released: str) -> DimensionResult:
    """How ready this chip is for production deployment."""
    s = str(status or "")
    r = str(is_released or "")
    if "量产" in s:
        score, detail = 10.0, "已量产 → 10/10"
    elif "已发布" in s:
        score, detail = 7.0, "已发布 → 7/10"
    elif r == "1":
        score, detail = 5.0, "已发布(无明确状态) → 5/10"
    elif "待发布" in s:
        score, detail = 4.0, "待发布 → 4/10"
    else:
        score, detail = 2.0, "未公开发布/传闻 → 2/10"
    return DimensionResult(
        score=score, detail=detail,
        raw_values={"production_status": s, "is_released": r},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #9: 软件栈兼容性 (Software Compatibility) — weight 8%
# ═══════════════════════════════════════════════════════════

# Major frameworks worth 2.5 each, minor worth 1.0 each
_MAJOR_FRAMEWORKS = ["pytorch", "tensorflow", "jax", "mindspore", "paddlepaddle", "vllm", "onnx"]
_MINOR_FRAMEWORKS = ["deepspeed", "megatron", "fsdp", "tensorrt", "openvino", "triton",
                      "llama.cpp", "sglang", "lmdeploy", "text-generation-inference"]


def score_software_compat(software_stack: str, compatible_frameworks: str) -> DimensionResult:
    """Framework support breadth. 7 major × 2.5 = 17.5, capped at 10."""
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    major_hits = sum(1 for fw in _MAJOR_FRAMEWORKS if fw in fw_text)
    minor_hits = sum(1 for fw in _MINOR_FRAMEWORKS if fw in fw_text)
    score = min(major_hits * 2.5 + minor_hits * 1.0, 10.0)
    if major_hits == 0 and minor_hits == 0:
        detail = "未检测到主流框架支持 → 0/10"
    else:
        detail = f"主流框架×{major_hits} (+{major_hits*2.5}) + 工具链×{minor_hits} (+{minor_hits}) = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "major_frameworks_found": major_hits, "minor_tools_found": minor_hits,
            "formula": "min(major*2.5 + minor*1.0, 10)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #10: 实测验证度 (Benchmark Evidence) — weight 7-8%
# ═══════════════════════════════════════════════════════════

def score_benchmark_evidence(benchmark_count: int, max_mfu: Optional[float],
                              max_tps: Optional[float], scenario: str) -> DimensionResult:
    """Reward chips with real benchmark data. No benchmarks → 0."""
    if benchmark_count == 0:
        return DimensionResult(score=0.0, detail="无实测benchmark数据 → 0/10",
                               raw_values={"benchmark_count": 0})

    # MFU sub-score (training-relevant)
    mfu = float(max_mfu or 0)
    if mfu > 0:
        mfu_score = min(mfu / 60.0, 5.0)  # 60% MFU → 5
    else:
        mfu_score = 0.0

    # TPS sub-score (inference-relevant)
    tps = float(max_tps or 0)
    if tps > 0:
        tps_score = min(tps / 5000.0, 5.0)  # 5000 tok/s → 5
    else:
        tps_score = 0.0

    # Count bonus
    count_score = min(benchmark_count / 3.0, 3.0)

    if scenario == "train":
        score = min(mfu_score * 0.6 + tps_score * 0.2 + count_score * 0.2, 10.0)
        detail = f"{benchmark_count}条实测, MFU={mfu:.0f}%({mfu_score:.1f}) → {score:.1f}/10"
    else:
        score = min(tps_score * 0.6 + mfu_score * 0.2 + count_score * 0.2, 10.0)
        detail = f"{benchmark_count}条实测, TPS={tps:.0f}({tps_score:.1f}) → {score:.1f}/10"

    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "benchmark_count": benchmark_count, "max_mfu_pct": mfu,
            "max_throughput_tok_s": tps, "mfu_score": round(mfu_score, 2),
            "tps_score": round(tps_score, 2), "count_score": round(count_score, 2),
            "formula": "mfu_score*0.6 + tps_score*0.2 + count_score*0.2",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #11 (optional): 国产化优先 (Domestic Priority)
# ═══════════════════════════════════════════════════════════

def score_domestic_priority(vendor_region: str, prefer_domestic: bool,
                             prefer_vendor: Optional[str], chip_vendor: str) -> DimensionResult:
    """Preference bonus. Not part of the 10-dim core but can be added as extra."""
    region = str(vendor_region or "")
    vendor = str(chip_vendor or "")
    if prefer_vendor and prefer_vendor.lower() in vendor.lower():
        score, detail = 10.0, f"厂商偏好匹配({prefer_vendor}) → 10/10"
    elif prefer_domestic and region == "domestic":
        score, detail = 10.0, "国产优先+国产芯片 → 10/10"
    elif prefer_domestic:
        score, detail = 0.0, "国产优先+非国产 → 0/10"
    else:
        score, detail = 5.0, "无偏好 → 中性 5/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"vendor_region": region, "prefer_domestic": prefer_domestic,
                     "prefer_vendor": prefer_vendor},
    )


# ═══════════════════════════════════════════════════════════
# Aggregator — compute all dimensions and weighted total
# ═══════════════════════════════════════════════════════════

def aggregate_score(
    ctx: RecommendContext,
    weights: ScoringWeights,
    prefer_domestic: bool = False,
    prefer_vendor: Optional[str] = None,
    tco_weight_override: float = 0.0,
) -> ScoringResult:
    """Compute all dimension scores and weighted total for one chip."""

    if tco_weight_override != weights.total_cost_ownership:
        weights = weights.scale_other_weights(tco_weight_override)

    chip = ctx.chip

    dims: dict[str, DimensionResult] = {}

    # D1: 算力性能
    dims["compute_perf"] = score_compute_perf(ctx.fp16_tflops)

    # D2: 显存充裕度 (use vram_cards for per-card need, not recommended_cards)
    dims["vram_sufficiency"] = score_vram_sufficiency(
        float(chip.get("vram_gb", 0) or 0), ctx.min_vram_total, ctx.vram_cards,
    )

    # D3: 价格经济性
    dims["cost_efficiency"] = score_cost_efficiency(
        ctx.fp16_tflops, float(chip.get("price_cny_wan", 0) or 0),
    )

    # D4: 能效比
    dims["power_efficiency"] = score_power_efficiency(
        ctx.fp16_tflops, float(chip.get("tdp_w", 0) or 0),
    )

    # D5: 互联扩展
    dims["interconnect_quality"] = score_interconnect_quality(
        float(chip.get("interconnect_bw_gb_s", 0) or 0),
        str(chip.get("interconnect_tech", "") or ""),
    )

    # D6: 生态成熟度 (v3.0: no maturity_level)
    has_fw = bool((chip.get("software_stack") or "").strip() or
                  (chip.get("compatible_frameworks") or "").strip())
    dims["ecosystem_maturity"] = score_ecosystem_maturity(
        int(float(chip.get("cloud_available", 0) or 0)),
        ctx.compat_verified_count,
        has_frameworks=has_fw,
    )

    # D7: SLA 满足度
    if ctx.scenario == "train" and ctx.target_training_days:
        dims["sla_satisfaction"] = score_sla_train(
            ctx.estimated_training_days, ctx.target_training_days,
        )
    elif ctx.scenario == "inference" and ctx.target_tps:
        est_tps = ctx.max_benchmark_tps or estimate_inference_tps(
            ctx.fp16_tflops, float(chip.get("vram_bw_gb_s", 0) or 0), ctx.model_params_B,
        )
        dims["sla_satisfaction"] = score_sla_inference(est_tps, ctx.target_tps)
    elif ctx.scenario == "quantize":
        # Quantization is a one-shot task — SLA is soft.
        # Score based on whether the chip has sufficient VRAM in recommended configuration.
        dims["sla_satisfaction"] = DimensionResult(
            score=5.0, detail="量化是一次性任务，无SLA目标 → 中性 5.0/10",
            raw_values={"note": "quantize_no_sla"},
        )
    else:
        dims["sla_satisfaction"] = DimensionResult(
            score=5.0, detail="无SLA目标 → 中性 5.0/10",
            raw_values={"note": "no_sla_target"},
        )

    # D8: 生产就绪度
    dims["production_readiness"] = score_production_readiness(
        str(chip.get("production_status", "") or ""),
        str(chip.get("is_released", "") or ""),
    )

    # D9: 软件栈兼容
    dims["software_compat"] = score_software_compat(
        str(chip.get("software_stack", "") or ""),
        str(chip.get("compatible_frameworks", "") or ""),
    )

    # D10: 实测验证度
    dims["benchmark_evidence"] = score_benchmark_evidence(
        ctx.benchmark_count, ctx.max_benchmark_mfu, ctx.max_benchmark_tps, ctx.scenario,
    )

    # Bonus: 国产化优先 (included in total but not a "scoring dimension")
    dims["domestic_priority"] = score_domestic_priority(
        str(chip.get("vendor_region", "") or ""),
        prefer_domestic, prefer_vendor,
        str(chip.get("vendor", "") or ""),
    )

    # Apply weights and compute total
    weight_map = {
        "compute_perf": weights.compute_perf,
        "vram_sufficiency": weights.vram_sufficiency,
        "cost_efficiency": weights.cost_efficiency,
        "power_efficiency": weights.power_efficiency,
        "interconnect_quality": weights.interconnect_quality,
        "ecosystem_maturity": weights.ecosystem_maturity,
        "sla_satisfaction": weights.sla_satisfaction,
        "production_readiness": weights.production_readiness,
        "software_compat": 0.0,  # not in weight budget, treated as bonus
        "benchmark_evidence": weights.benchmark_evidence,
        "domestic_priority": 0.0,  # bonus — not in weight budget
    }

    total = 0.0
    for dim_id, result in dims.items():
        w = weight_map.get(dim_id, 0.0)
        result.weight = w
        result.weighted = round(result.score * w, 4)
        total += result.weighted

    return ScoringResult(
        total_score=round(total * 10, 1),  # scale to 0-100
        dimensions=dims,
        weights=weights,
    )


# ═══════════════════════════════════════════════════════════
# Serialization helpers
# ═══════════════════════════════════════════════════════════

def scoring_result_to_dict(sr: ScoringResult) -> dict:
    """Convert ScoringResult to API-ready dict."""
    dims_out = {}
    for dim_id, dr in sr.dimensions.items():
        dims_out[dim_id] = {
            "score": dr.score,
            "weight": dr.weight,
            "weighted": dr.weighted,
            "detail": dr.detail,
            "raw_values": dr.raw_values,
        }
    return {
        "total": sr.total_score,
        "dimensions": dims_out,
        "weights": {
            "compute_perf": sr.weights.compute_perf,
            "vram_sufficiency": sr.weights.vram_sufficiency,
            "cost_efficiency": sr.weights.cost_efficiency,
            "power_efficiency": sr.weights.power_efficiency,
            "interconnect_quality": sr.weights.interconnect_quality,
            "ecosystem_maturity": sr.weights.ecosystem_maturity,
            "sla_satisfaction": sr.weights.sla_satisfaction,
            "production_readiness": sr.weights.production_readiness,
            "benchmark_evidence": sr.weights.benchmark_evidence,
            "total_cost_ownership": sr.weights.total_cost_ownership,
        },
        "version": sr.version,
    }


# ── Dimension metadata for documentation / UI ──

DIMENSION_META = [
    {"id": "compute_perf",          "name_cn": "算力性能",     "name_en": "Compute Performance",
     "desc": "单卡FP16/BF16理论算力(TFLOPS)", "unit": "TFLOPS", "train_weight": 0.20, "infer_weight": 0.15},
    {"id": "vram_sufficiency",      "name_cn": "显存充裕度",   "name_en": "VRAM Sufficiency",
     "desc": "单卡显存相比模型需求的余量倍数", "unit": "比率", "train_weight": 0.15, "infer_weight": 0.20},
    {"id": "cost_efficiency",       "name_cn": "价格经济性",   "name_en": "Cost Efficiency",
     "desc": "每万元能买到的FP16算力", "unit": "TFLOPS/万元", "train_weight": 0.12, "infer_weight": 0.15},
    {"id": "power_efficiency",      "name_cn": "能效比",      "name_en": "Power Efficiency",
     "desc": "每瓦功耗产出的算力", "unit": "GFLOPS/W", "train_weight": 0.08, "infer_weight": 0.08},
    {"id": "interconnect_quality",  "name_cn": "互联扩展性",   "name_en": "Interconnect Quality",
     "desc": "多卡互联带宽+技术等级", "unit": "GB/s + 技术分", "train_weight": 0.12, "infer_weight": 0.08},
    {"id": "ecosystem_maturity",    "name_cn": "生态成熟度",   "name_en": "Ecosystem Maturity",
     "desc": "云平台可用+已验证兼容模型数+框架支持 (v3.0移除成熟度主观评分)", "unit": "综合分", "train_weight": 0.10, "infer_weight": 0.12},
    {"id": "sla_satisfaction",      "name_cn": "SLA满足度",    "name_en": "SLA Satisfaction",
     "desc": "训练天数/推理吞吐是否满足目标", "unit": "SLA分", "train_weight": 0.10, "infer_weight": 0.10},
    {"id": "production_readiness",  "name_cn": "生产就绪度",   "name_en": "Production Readiness",
     "desc": "量产/已发布/未公开等状态", "unit": "等级", "train_weight": 0.05, "infer_weight": 0.05},
    {"id": "benchmark_evidence",    "name_cn": "实测验证度",   "name_en": "Benchmark Evidence",
     "desc": "是否有实测benchmark数据(MFU/吞吐)", "unit": "证据分", "train_weight": 0.08, "infer_weight": 0.07},
    {"id": "software_compat",       "name_cn": "软件栈兼容",   "name_en": "Software Compatibility",
     "desc": "支持的主流框架和工具链数量", "unit": "框架数", "train_weight": 0.00, "infer_weight": 0.00},
    {"id": "domestic_priority",     "name_cn": "国产化优先",   "name_en": "Domestic Priority",
     "desc": "国产/厂商偏好匹配加分", "unit": "偏好分", "train_weight": 0.00, "infer_weight": 0.00},
]
