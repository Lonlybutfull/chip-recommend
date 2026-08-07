"""
AISHPerf Chip Recommendation Scoring Engine v4.2

9-dimension scoring with each dimension outputting 0.0-10.0,
weighted sum yields total 0-100.

v4.2 changes:
  - Deleted: production_readiness, cost_efficiency (process_node), domestic_priority
  - New: server_count_efficiency (8卡/节点=10, 多节点扣分)
    framework_compat (major frameworks, split from software_compat)
    toolchain_compat (minor toolchains, split from software_compat)
    source_credibility (official source ratio)
  - Modified: ecosystem_maturity → compatibility_score (renamed)
    benchmark_evidence: no-data default 5.0 (was 6.2)
    SUB_DIMS_ECOSYSTEM: compat 40% + framework 15% + toolchain 15% + benchmark 20% + source 10%

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
# v4.0 Configuration — 3-category weights + sub-dimension ratios
# ═══════════════════════════════════════════════════════════
#
# Category weights (3 presets × 3 categories):
#   Train:     compute=55%, cost=15%, ecosystem=30%
#   Inference: compute=45%, cost=25%, ecosystem=30%
#   Quantize:  compute=40%, cost=20%, ecosystem=40%
#
# Sub-dimension ratios are FIXED (not scenario-dependent):
#   Compute:  D1(30%) + D2(30%) + D5(15%) + D7(10%) + D8(15%)
#   Cost:     D3(50%) + D4(50%)
#   Ecosystem: D6(40%) + D9(20%) + D10(30%) + D11(10%)
# ═══════════════════════════════════════════════════════════


@dataclass
class CategoryWeights:
    """3-category weight preset. Sum must be 1.0."""
    compute_power: float = 0.50
    cost_effectiveness: float = 0.25
    ecosystem_maturity: float = 0.25

    def validate(self) -> bool:
        return abs(self.compute_power + self.cost_effectiveness + self.ecosystem_maturity - 1.0) < 0.001

    def to_dict(self) -> dict:
        return {
            "compute_power": self.compute_power,
            "cost_effectiveness": self.cost_effectiveness,
            "ecosystem_maturity": self.ecosystem_maturity,
        }


@dataclass
class CategoryResult:
    """Aggregate score for one category, composed of sub-dimensions."""
    id: str = ""
    name_cn: str = ""
    name_en: str = ""
    score: float = 0.0          # 0-10 weighted avg of sub-dimensions
    weight: float = 0.0          # category-level weight
    weighted: float = 0.0        # score × weight
    sub_dimensions: dict = field(default_factory=dict)  # {dim_id: DimensionResult}
    formula: str = ""            # e.g. "D1×0.30 + D2×0.30 + ..."


# ── Sub-dimension composition (fixed ratios within each category) ──

SUB_DIMS_COMPUTE_POWER = {
    "compute_perf": 0.60,
    "bandwidth_adequacy": 0.40,
}

SUB_DIMS_COST_EFFECTIVENESS = {
    "power_efficiency": 0.40,
    "server_count_efficiency": 0.60,  # 8卡/节点 → 10, 多节点扣分
}

SUB_DIMS_ECOSYSTEM = {
    "compatibility_score": 0.40,      # renamed from ecosystem_maturity, cloud support + verified compat
    "framework_compat": 0.15,         # major frameworks only, split from software_compat
    "toolchain_compat": 0.15,         # minor toolchains only
    "benchmark_evidence": 0.20,       # no-data default 5.0
    "source_credibility": 0.10,       # official source ratio (low weight)
}

CATEGORY_DEFS = [
    {"id": "compute_power", "name_cn": "算力性能", "name_en": "Compute Capability",
     "sub_dims": SUB_DIMS_COMPUTE_POWER},
    {"id": "cost_effectiveness", "name_cn": "性价比", "name_en": "Cost-Effectiveness",
     "sub_dims": SUB_DIMS_COST_EFFECTIVENESS},
    {"id": "ecosystem_maturity", "name_cn": "生态成熟度", "name_en": "Ecosystem Maturity",
     "sub_dims": SUB_DIMS_ECOSYSTEM},
]

# ── Category weight presets for each scenario ──

CAT_WEIGHTS_TRAIN = CategoryWeights(
    compute_power=0.55, cost_effectiveness=0.15, ecosystem_maturity=0.30,
)
CAT_WEIGHTS_INFER = CategoryWeights(
    compute_power=0.45, cost_effectiveness=0.25, ecosystem_maturity=0.30,
)
CAT_WEIGHTS_QUANTIZE = CategoryWeights(
    compute_power=0.40, cost_effectiveness=0.20, ecosystem_maturity=0.40,
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
    version: str = "4.2.0"


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


# ═══════════════════════════════════════════════════════════
# Dimension #1: 算力性能 (Compute Performance) — weight 15-20%
# ═══════════════════════════════════════════════════════════

def score_compute_perf(fp16_tflops: float) -> DimensionResult:
    """Single-card FP16/BF16 compute throughput.

    Based on 996-chip distribution (1093 total, 91.1% coverage):
      P50=6.5T, P75=24T, P90=105T, P95=383T, P99=2563T
    Piecewise linear anchored at data percentiles:
      ≤13T(P50 area)→0-2.5, 13-105T→2.5-5.0, 105-383T→5.0-7.5, >383T→7.5-10(capped at 2563=P99)

    Missing data → statistical mean 1.9 (996-chip average; actual mean is low because
    most chips are consumer GPUs with modest FP16). Prevents inflating scores for
    chips without published perf data — if they can't benchmark, they likely aren't
    strong compute chips.
    """
    MEAN = 1.9  # statistical mean of 996 chips (91.1% coverage)
    if fp16_tflops > 0:
        if fp16_tflops <= 13:
            score = fp16_tflops / 13.0 * 2.5
            detail = f"FP16={fp16_tflops:.0f}T (≤P50=13T) → {score:.1f}/10"
        elif fp16_tflops <= 105:
            score = 2.5 + (fp16_tflops - 13) / (105 - 13) * 2.5
            detail = f"FP16={fp16_tflops:.0f}T (P50-P90) → {score:.1f}/10"
        elif fp16_tflops <= 383:
            score = 5.0 + (fp16_tflops - 105) / (383 - 105) * 2.5
            detail = f"FP16={fp16_tflops:.0f}T (P90-P95) → {score:.1f}/10"
        else:
            score = min(7.5 + (fp16_tflops - 383) / (2563 - 383) * 2.5, 10.0)
            detail = f"FP16={fp16_tflops:.0f}T (>P95=383T) → {score:.1f}/10"
    else:
        score = MEAN
        detail = f"无FP16/BF16算力数据 → 统计均值 {MEAN:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={"fp16_tflops": fp16_tflops,
                    "formula": "piecewise: P50(13T)=2.5, P90(105T)=5.0, P95(383T)=7.5, P99(2563T)=10",
                    "source": "chip.precision_perf (996/1093 chips, 91% coverage)", "missing": fp16_tflops <= 0},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #2: 带宽充裕度 (Bandwidth Adequacy) — weight 30%
# ═══════════════════════════════════════════════════════════

def score_bandwidth_adequacy(vram_bw_gb_s: float, cards: int,
                              model_bandwidth_gb_s: float) -> DimensionResult:
    """Is total VRAM bandwidth sufficient for the model?

    total_bw = vram_bw_gb_s × cards   (aggregate bandwidth across all cards)
    adequacy = model_need / total_bw  (lower is better)

    Scoring: model needs ≤ 50% of total bandwidth → 10 (comfortable headroom)
             above 50% → linear decay: score = 10 × (1 - 2×(ratio - 0.5))
             i.e. at 50%→10, at 75%→5, at 100%→0

    Missing data → statistical mean 4.1 (962-chip average, 88% coverage).
    """
    MEAN = 4.1  # statistical mean of 962 chips (88% coverage)
    cards = max(cards, 1)
    bw = float(vram_bw_gb_s or 0)
    if bw <= 0:
        return DimensionResult(
            score=MEAN, detail=f"无显存带宽数据 → 统计均值 {MEAN:.1f}/10",
            raw_values={"vram_bw_gb_s": 0, "cards": cards,
                        "source": "chip.vram_bw_gb_s (962/1093 chips, 88% coverage)",
                        "missing": True},
        )
    total_bw = bw * cards
    if model_bandwidth_gb_s <= 0:
        # No bandwidth target → assume adequate
        score = 8.0
        detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s （无模型带宽需求目标） → 8.0/10"
    else:
        ratio = model_bandwidth_gb_s / total_bw
        if ratio <= 0.5:
            score = 10.0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s, 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}% → 满分10.0"
        elif ratio <= 1.0:
            score = 10.0 - (ratio - 0.5) * 20.0  # 0.5→10, 1.0→0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s, 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}%（>50%） → {score:.1f}/10"
        else:
            score = 0.0
            detail = f"总带宽{bw:.0f}×{cards}卡={total_bw:.0f}GB/s < 需求{model_bandwidth_gb_s:.0f}GB/s, 占比{ratio*100:.0f}% → 0/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "vram_bw_gb_s": bw, "cards": cards,
            "total_bw_gb_s": round(total_bw, 1),
            "model_bandwidth_gb_s": round(model_bandwidth_gb_s, 1),
            "ratio": round(ratio, 3) if model_bandwidth_gb_s > 0 else 0,
            "formula": "total_bw = vram_bw × cards; ratio = model_need / total_bw; ≤50%→10, >50%→linear 10→0",
            "source": "chip.vram_bw_gb_s (88% coverage)",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #3: 能效比 (Power Efficiency) — combined with server count
# ═══════════════════════════════════════════════════════════

def score_power_efficiency(fp16_tflops: float, tdp_w: float) -> DimensionResult:
    """GFLOPS per Watt. Based on 959-chip FP16×TDP cross-section (87.7% coverage).

    Actual data: mean=167 GFLOPS/W, median=76, P25=10, P75=160, P90=470.
    Piecewise: ≤130(P50 area)→0-2.5, 130-470(P90)→2.5-5.0, 470-1500→5.0-8.0, >1500→8.0-10.0(cap at 4500=top).
    Missing data → statistical mean 2.0 (959-chip average).
    """
    MEAN = 2.0  # statistical mean of 959 chips (87.7% coverage)
    tdp = float(tdp_w or 0)
    if fp16_tflops > 0 and tdp > 0:
        gf = fp16_tflops * 1000 / tdp  # GFLOPS/W
        if gf <= 130:
            score = gf / 130.0 * 2.5
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (≤P50=130) → {score:.1f}/10"
        elif gf <= 470:
            score = 2.5 + (gf - 130) / (470 - 130) * 2.5
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (P50-P90) → {score:.1f}/10"
        elif gf <= 1500:
            score = 5.0 + (gf - 470) / (1500 - 470) * 3.0
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (P90-H100) → {score:.1f}/10"
        else:
            score = min(8.0 + (gf - 1500) / 3000 * 2.0, 10.0)
            detail = f"{fp16_tflops:.0f}T/{tdp:.0f}W={gf:.0f}GFLOPS/W (>H100=1413) → {score:.1f}/10"
    else:
        score = MEAN
        gf = 0.0
        detail = f"无功耗或算力数据 → 统计均值 {MEAN:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "gflops_per_watt": round(gf, 1), "tdp_w": tdp,
            "formula": "piecewise: P50(130)=2.5, P90(470)=5.0, H100(1413)=8.0, B200(2250)=10",
            "source": "chip.tdp_w (92% coverage) + chip.precision_perf",
            "missing": tdp <= 0 or fp16_tflops <= 0,
        },
    )


# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# Dimension #5: 兼容性评分 (Compatibility Score) — weight 40%
# ═══════════════════════════════════════════════════════════

def score_compatibility(cloud: int, compat_verified: int,
                         has_frameworks: bool = False) -> DimensionResult:
    """Composite: cloud support (+3) + verified compat (+4 max, 0.8/ea) + frameworks (+3).

    Max: 3 + 4 + 3 = 10.0.
    Coverage: cloud_available=0, software_stack=41, compatible_frameworks=19, verified_compat=10 rows.
    Missing data → 5.0 (neutral, previously 2.8).
    """
    cloud_val = int(float(cloud or 0))
    compat_val = int(compat_verified or 0)
    cloud_score = 3.0 if cloud_val >= 1 else 0.0
    compat_score = min(compat_val * 0.8, 4.0)
    fw_score = 3.0 if has_frameworks else 0.0

    score = min(cloud_score + compat_score + fw_score, 10.0)
    parts = []
    if cloud_score: parts.append(f"云可用+{cloud_score:.0f}")
    if compat_score > 0: parts.append(f"兼容{compat_val}条→+{compat_score:.1f}")
    if fw_score: parts.append(f"框架+{fw_score:.0f}")
    if parts:
        detail = " + ".join(parts) + f" = {score:.1f}/10"
    else:
        score = 5.0  # neutral default
        detail = f"无生态数据 → 默认 {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "cloud_available": cloud_val,
            "compat_verified_count": compat_val,
            "has_frameworks": has_frameworks,
            "formula": "min(cloud*3 + min(compat*0.8,4) + fw*3, 10); no-data→5.0",
            "source": "chip.cloud_available + chip_model_compatibility + chip.software_stack",
            "missing": not parts,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #4: 服务器节点效率 (Server Count Efficiency) — weight 60%
# 8卡（1节点）=10分，多节点扣分
# ═══════════════════════════════════════════════════════════

def score_server_count_efficiency(recommended_cards: int) -> DimensionResult:
    """8 cards per node = 10 (optimal), more nodes → linear decay.

    Formula: nodes = ceil(cards / 8)
    1 node (≤8 cards) → 10
    2 nodes (9-16) → 8
    3 nodes (17-24) → 6
    4 nodes (25-32) → 4
    5+ nodes → max(0, 2 - 0.5 per extra node above 4)
    """
    cards = max(recommended_cards, 1)
    nodes = (cards + 7) // 8  # ceiling division
    if nodes <= 1:
        score = 10.0
        detail = f"{cards}卡 = 1节点 → 满分 10.0/10"
    elif nodes <= 4:
        score = 10.0 - (nodes - 1) * 2.0  # 1→10, 2→8, 3→6, 4→4
        detail = f"{cards}卡 = {nodes}节点 → {score:.1f}/10"
    else:
        score = max(0.0, 4.0 - (nodes - 4) * 0.5)  # 5→3.5, 6→3.0, ...
        detail = f"{cards}卡 = {nodes}节点（>4） → {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "recommended_cards": cards, "nodes": nodes,
            "formula": "nodes=ceil(cards/8); 1→10, 2→8, 3→6, 4→4, >4→-0.5/node",
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
    """Major framework support. 7 frameworks × 1.5, capped at 10.
    No data → default 5.0.
    """
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    major_hits = sum(1 for fw in _MAJOR_FRAMEWORKS if fw in fw_text)
    if major_hits == 0:
        score = 5.0
        detail = "未检测到主流框架支持 → 默认 5.0/10"
    else:
        score = min(major_hits * 1.5, 10.0)
        detail = f"主流框架×{major_hits} (+{major_hits*1.5:.1f}) = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "major_frameworks_found": major_hits,
            "formula": "min(major*1.5, 10); no-data→5.0",
            "source": "chip.software_stack + chip.compatible_frameworks",
            "missing": major_hits == 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #7: 工具链兼容 (Toolchain Compatibility) — weight 15%
# Minor toolchains only
# ═══════════════════════════════════════════════════════════

def score_toolchain_compat(software_stack: str, compatible_frameworks: str) -> DimensionResult:
    """Minor toolchain support. 10 tools × 0.8, capped at 8.
    No data → default 5.0.
    """
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    minor_hits = sum(1 for fw in _MINOR_FRAMEWORKS if fw in fw_text)
    if minor_hits == 0:
        score = 5.0
        detail = "未检测到工具链支持 → 默认 5.0/10"
    else:
        score = min(minor_hits * 0.8, 8.0)
        detail = f"工具链×{minor_hits} (+{minor_hits*0.8:.1f}) = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "minor_tools_found": minor_hits,
            "formula": "min(minor*0.8, 8); no-data→5.0",
            "source": "chip.software_stack + chip.compatible_frameworks",
            "missing": minor_hits == 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #8: 实测验证度 (Benchmark Evidence) — weight 20%
# ═══════════════════════════════════════════════════════════

def score_benchmark_evidence(benchmark_count: int, max_mfu: Optional[float],
                              max_tps: Optional[float]) -> DimensionResult:
    """Reward chips with real benchmark data. No data → 5.0 (neutral default).

    Count-based: 0→5.0, 1-3→5.5-6.5, 4-13→6.5-8.5, 13+→8.5-9.5.
    Modest MFU/TPS bonus (±0.5 max) rather than multiplier.
    """
    if benchmark_count == 0:
        return DimensionResult(score=5.0, detail="无实测benchmark数据 → 默认 5.0/10",
                               raw_values={"benchmark_count": 0,
                                           "source": "chip_model_benchmarks (33 matched / 430 total, 3% coverage)",
                                           "missing": True})

    # Count-based base score (modest range)
    if benchmark_count <= 3:
        base = 5.0 + benchmark_count / 3.0 * 1.5  # 1→5.5, 2→6.0, 3→6.5
    elif benchmark_count <= 13:
        base = 6.5 + (benchmark_count - 3) / 10.0 * 2.0  # 4→6.7, 13→8.5
    else:
        base = min(8.5 + (benchmark_count - 13) / 87.0 * 1.0, 9.5)  # capped at 9.5

    # Modest MFU bonus (±0.25)
    mfu = float(max_mfu or 0)
    mfu_bonus = 0.0
    if mfu > 40:
        mfu_bonus = 0.25
    elif mfu > 0:
        mfu_bonus = -0.25

    # Modest TPS bonus (±0.25)
    tps = float(max_tps or 0)
    tps_bonus = 0.0
    if tps > 500:
        tps_bonus = 0.25
    elif tps > 0:
        tps_bonus = -0.25

    score = min(base + mfu_bonus + tps_bonus, 10.0)

    detail = f"{benchmark_count}条实测"
    if mfu > 0: detail += f" MFU={mfu:.0f}%"
    if tps > 0: detail += f" TPS={tps:.0f}"
    detail += f" → {score:.1f}/10"

    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "benchmark_count": benchmark_count, "max_mfu_pct": mfu,
            "max_throughput_tok_s": tps,
            "base_score": round(base, 2), "mfu_bonus": round(mfu_bonus, 2),
            "tps_bonus": round(tps_bonus, 2),
            "formula": "count-base(5.0-9.5) + MFU/TPS bonus(±0.25); no-data→5.0",
            "source": "chip_model_benchmarks",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #9: 来源真实度 (Source Credibility) — weight 10%
# ═══════════════════════════════════════════════════════════

def score_source_credibility(official_ratio: float) -> DimensionResult:
    """How much of the chip's data comes from official sources.

    official_ratio = official_fields / total_fields (from field_provenance)
    ≥80% official → 10, ≥60% → 8, ≥40% → 7, ≥20% → 6, <20% → 5
    No provenance data → 5.0 (neutral, data comes from web_crawl mostly)
    """
    if official_ratio < 0:
        return DimensionResult(
            score=5.0, detail="无来源溯源数据 → 默认 5.0/10",
            raw_values={"official_ratio": 0, "missing": True},
        )
    if official_ratio >= 0.80:
        score = 10.0
        detail = f"官方来源 {official_ratio*100:.0f}%（≥80%）→ 10/10"
    elif official_ratio >= 0.60:
        score = 8.0
        detail = f"官方来源 {official_ratio*100:.0f}%（60-80%）→ 8/10"
    elif official_ratio >= 0.40:
        score = 7.0
        detail = f"官方来源 {official_ratio*100:.0f}%（40-60%）→ 7/10"
    elif official_ratio >= 0.20:
        score = 6.0
        detail = f"官方来源 {official_ratio*100:.0f}%（20-40%）→ 6/10"
    else:
        score = 5.0
        detail = f"官方来源 {official_ratio*100:.0f}%（<20%）→ 5/10"
    return DimensionResult(
        score=score, detail=detail,
        raw_values={
            "official_ratio": round(official_ratio, 3),
            "formula": "official_fields/total_fields; ≥80%=10, ≥60%=8, ≥40%=7, ≥20%=6, <20%=5",
            "source": "field_provenance table — source_type + is_official",
        },
    )


# ═══════════════════════════════════════════════════════════
# Aggregator — compute all dimensions and weighted total
# ═══════════════════════════════════════════════════════════

def aggregate_score(
    ctx: RecommendContext,
    cat_weights: CategoryWeights,
    prefer_domestic: bool = False,
    prefer_vendor: Optional[str] = None,
) -> ScoringResult:
    """v4.2: Compute all dimension scores, aggregate into 3 categories.

    Formula:
      total = Cat_A × w_A + Cat_B × w_B + Cat_C × w_C    (0-100 scale)
    where each category score = Σ(sub_dim_score × sub_weight) within category.
    """

    chip = ctx.chip

    # ── Step 1: Compute all 9 sub-dimension scores ──

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

    # D5: 兼容性评分（原生态成熟度）
    has_fw = bool((chip.get("software_stack") or "").strip() or
                  (chip.get("compatible_frameworks") or "").strip())
    dims["compatibility_score"] = score_compatibility(
        int(float(chip.get("cloud_available", 0) or 0)),
        ctx.compat_verified_count,
        has_frameworks=has_fw,
    )

    # D6: 框架兼容
    dims["framework_compat"] = score_framework_compat(
        str(chip.get("software_stack", "") or ""),
        str(chip.get("compatible_frameworks", "") or ""),
    )

    # D7: 工具链兼容
    dims["toolchain_compat"] = score_toolchain_compat(
        str(chip.get("software_stack", "") or ""),
        str(chip.get("compatible_frameworks", "") or ""),
    )

    # D8: 实测验证度
    dims["benchmark_evidence"] = score_benchmark_evidence(
        ctx.benchmark_count, ctx.max_benchmark_mfu, ctx.max_benchmark_tps,
    )

    # D9: 来源真实度
    dims["source_credibility"] = score_source_credibility(ctx.official_ratio)

    # ── Step 2: Aggregate into 3 categories ──

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
                "compatibility_score": "D5兼容", "framework_compat": "D6框架",
                "toolchain_compat": "D7工具", "benchmark_evidence": "D8实测",
                "source_credibility": "D9来源",
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
        total_score=round(total * 10, 1),  # scale to 0-100
        categories=categories,
        dimensions=dims,         # flat backward-compat
        version="4.2.0",
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
     "category": "compute_power", "sub_weight": 0.60},
    {"id": "bandwidth_adequacy",    "name_cn": "带宽充裕度",   "name_en": "Bandwidth Adequacy",
     "desc": "总显存带宽(vram_bw×卡数)相对模型带宽需求的充裕程度", "unit": "比率",
     "category": "compute_power", "sub_weight": 0.40},
    {"id": "power_efficiency",      "name_cn": "能效比",      "name_en": "Power Efficiency",
     "desc": "每瓦功耗产出的算力", "unit": "GFLOPS/W",
     "category": "cost_effectiveness", "sub_weight": 0.40},
    {"id": "server_count_efficiency","name_cn": "节点效率",    "name_en": "Server Count Efficiency",
     "desc": "8卡=1节点满分，多节点扣分", "unit": "节点数",
     "category": "cost_effectiveness", "sub_weight": 0.60},
    {"id": "compatibility_score",   "name_cn": "兼容性评分",   "name_en": "Compatibility Score",
     "desc": "云平台可用+已验证兼容模型数+框架支持", "unit": "综合分",
     "category": "ecosystem_maturity", "sub_weight": 0.40},
    {"id": "framework_compat",      "name_cn": "框架兼容",     "name_en": "Framework Compatibility",
     "desc": "主流框架支持数(PyTorch/TF/JAX等)", "unit": "框架数",
     "category": "ecosystem_maturity", "sub_weight": 0.15},
    {"id": "toolchain_compat",      "name_cn": "工具链兼容",   "name_en": "Toolchain Compatibility",
     "desc": "辅助工具链支持数(DeepSpeed/TensorRT等)", "unit": "工具数",
     "category": "ecosystem_maturity", "sub_weight": 0.15},
    {"id": "benchmark_evidence",    "name_cn": "实测验证度",   "name_en": "Benchmark Evidence",
     "desc": "是否有实测benchmark数据(MFU/吞吐)", "unit": "证据分",
     "category": "ecosystem_maturity", "sub_weight": 0.20},
    {"id": "source_credibility",    "name_cn": "来源真实度",   "name_en": "Source Credibility",
     "desc": "数据来源中官方资料占比", "unit": "比率",
     "category": "ecosystem_maturity", "sub_weight": 0.10},
]
