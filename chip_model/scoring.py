"""
AISHPerf Chip Recommendation Scoring Engine v3.4

10-dimension scoring with each dimension outputting 0.0-10.0,
weighted sum yields total 0-100.

v3.4 changes:
  - Missing-data fallback: replaced uniform 5.0 with per-dimension statistical
    means computed from all 1,093 chips (real database distributions)
  - Fixed parse_process_node() to handle pure numbers ("7"), TSMC naming ("4NP"),
    and "5nm/6nm" dual-node notation (was dropping 96.6% of process_node values)
  - Benchmark chip-name matching now uses normalized (lowercase, trimmed) lookup

v3.3 changes:
  - D1~D10 scoring formulas re-anchored on real chip data percentiles
    (P50/P75/P90/P95/P99 from 1,093-chip distribution)
  - D3 switched from price (0% coverage) to process_node_nm (98% coverage)
  - D5 switched from interconnect_bw (4% coverage) to vram_bw_gb_s (88% coverage)

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
    "compute_perf": 0.30,
    "vram_sufficiency": 0.30,
    "interconnect_quality": 0.15,
    "sla_satisfaction": 0.10,
    "production_readiness": 0.15,
}

SUB_DIMS_COST_EFFECTIVENESS = {
    "cost_efficiency": 0.50,   # process_node_nm
    "power_efficiency": 0.50,
}

SUB_DIMS_ECOSYSTEM = {
    "ecosystem_maturity": 0.40,
    "software_compat": 0.20,
    "benchmark_evidence": 0.30,
    "domestic_priority": 0.10,
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
    version: str = "4.0.0"


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
# Dimension #2: 显存充裕度 (VRAM Sufficiency) — weight 12-20%
# ═══════════════════════════════════════════════════════════

def score_vram_sufficiency(vram_gb: float, model_vram_total: float, vram_cards: int) -> DimensionResult:
    """How much VRAM headroom per card vs what the model needs.

    Uses `vram_cards` (VRAM-only constraint) to compute per-card need,
    NOT recommended_cards (which may include compute constraint amplification).

    VRAM GB: 983 chips, mean=18.8GB, median=8.0GB, P25=3.0, P75=16.0.
    Missing data → statistical mean 2.7 (983-chip average for 7B-FP16×1card scenario).
    """
    MEAN = 2.7  # statistical mean of 983 chips for typical 7B inference (97.4% coverage)
    if vram_gb <= 0 or model_vram_total <= 0:
        return DimensionResult(
            score=MEAN, detail=f"无显存数据 → 统计均值 {MEAN:.1f}/10",
            raw_values={"vram_gb": vram_gb, "model_vram_total": model_vram_total,
                        "source": "chip.vram_gb (983/1093 chips, 97.4% coverage)", "missing": True},
        )
    per_card_need = model_vram_total / max(vram_cards, 1)
    ratio = vram_gb / max(per_card_need, 0.1)
    if ratio > 0:
        score = 10.0 * (1.0 - math.exp(-0.5 * ratio))
        detail = f"显存{vram_gb:.0f}GB / 需求{per_card_need:.0f}GB = {ratio:.1f}× → {score:.1f}/10"
    else:
        score = MEAN
        detail = "无法计算显存充裕度 → 统计均值 2.7/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "vram_gb": vram_gb, "per_card_need_gb": round(per_card_need, 1),
            "vram_cards": vram_cards, "headroom_ratio": round(ratio, 2),
            "formula": "10*(1-exp(-0.5*headroom_ratio))",
            "source": "chip.vram_gb",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #3: 制程先进性 (Process Node) — weight 12-15%
# Price data has 0% coverage in database (0/1093 chips).
# Using process_node_nm instead (98% coverage, 1067/1093 chips).
# ═══════════════════════════════════════════════════════════

def score_process_node(process_node_nm: float) -> DimensionResult:
    """Process node score based on actual 1067-chip distribution (97.6% coverage).

    Data: mean=23.6nm, median=12nm, P25=7nm, P75=28nm, min=3nm, max=220nm.
    Tier-based: 3-5nm=10, 6-7nm=8, 8-10nm=6, 12-16nm=4, 20-28nm=3, 40-55nm=2, >=90nm=1
    Missing → statistical mean 5.3 (1067-chip average).
    """
    MEAN = 5.3  # statistical mean of 1067 chips (97.6% coverage)
    nm = float(process_node_nm or 0)
    if nm <= 0:
        return DimensionResult(score=MEAN, detail=f"无制程数据 → 统计均值 {MEAN:.1f}/10",
                               raw_values={"process_node_nm": 0, "source": "chip.process_node_nm (1067/1093 chips, 97.6% coverage)", "missing": True})
    if nm <= 5:     score, tier = 10.0, "3-5nm (顶级)"
    elif nm <= 7:   score, tier = 8.0,  "6-7nm (先进)"
    elif nm <= 10:  score, tier = 6.0,  "8-10nm (主流)"
    elif nm <= 16:  score, tier = 4.0,  "12-16nm (成熟)"
    elif nm <= 28:  score, tier = 3.0,  "20-28nm"
    elif nm <= 55:  score, tier = 2.0,  "40-55nm"
    else:           score, tier = 1.0,  "≥90nm (老旧)"
    detail = f"制程{nm:.0f}nm ({tier}) → {score:.0f}/10"
    return DimensionResult(
        score=score, detail=detail,
        raw_values={"process_node_nm": nm, "tier": tier,
                    "formula": "tier lookup: 3-5nm=10, 6-7=8, 8-10=6, 12-16=4, 20-28=3, 40-55=2, 90+=1",
                    "source": "chip.process_node_nm (1067/1093 chips, 98% coverage)"},
    )


# Legacy alias — keep old name for backward compat in aggregate_score
def score_cost_efficiency(fp16_tflops: float, price_cny_wan: float) -> DimensionResult:
    """DEPRECATED: price data has 0% coverage. Use score_process_node instead.
    Always returns neutral 5.0 with note about missing price data.
    """
    return DimensionResult(
        score=5.0, detail="价格数据缺失(数据库覆盖率0%) → 中性分 5.0/10",
        raw_values={"price_cny_wan": float(price_cny_wan or 0),
                    "source": "chip.price_cny_wan (0/1093 chips — NO DATA)",
                    "missing": True},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #4: 能效比 (Power Efficiency) — weight 8%
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
# Dimension #5: 互联扩展性 (Interconnect Quality) — weight 8-12%
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# Dimension #5: 互联扩展性 (Interconnect / VRAM BW) — weight 8-12%
# interconnect_bw_gb_s only has 4% coverage (45/1093 chips).
# Using vram_bw_gb_s instead (88% coverage, 962/1093 chips) as a proxy
# since high-bandwidth VRAM (HBM) correlates with high-end interconnect.
# ═══════════════════════════════════════════════════════════

def score_interconnect_quality(bw_gb_s: float, tech: str, vram_bw: float = 0.0) -> DimensionResult:
    """Multi-card scalability: VRAM bandwidth proxy + interconnect technology tier.

    VRAM BW data (962 chips, 88% coverage): mean=554GB/s, median=224, P25=83, P75=448, P90=1020.
    Piecewise: ≤224(P50)→0-2, 224-1020(P90)→2-5, 1020-4000→5-8, >4000→8-10.
    Tech bonus (max +2): NVLink/HCCS +2, Infinity Fabric/ICI/C2C +1.5, other +1.
    Missing → statistical mean 2.3 (962-chip average).
    """
    MEAN = 2.3  # statistical mean of 962 chips (88% coverage)
    vbw = float(vram_bw or 0)
    tech = (tech or "").strip()
    if vbw <= 0:
        return DimensionResult(
            score=MEAN, detail=f"无显存带宽数据 → 统计均值 {MEAN:.1f}/10",
            raw_values={"vram_bw_gb_s": 0, "tech": "",
                        "source": "chip.vram_bw_gb_s (962/1093 chips, 88% coverage)", "missing": True},
        )
    # VRAM BW → base score (0-8)
    if vbw <= 224:
        bw_score = vbw / 224.0 * 2.0
    elif vbw <= 1020:
        bw_score = 2.0 + (vbw - 224) / (1020 - 224) * 3.0
    elif vbw <= 4000:
        bw_score = 5.0 + (vbw - 1020) / (4000 - 1020) * 3.0
    else:
        bw_score = min(8.0 + (vbw - 4000) / 6300 * 2.0, 8.0)

    # Tech bonus (max 2.0)
    tech_lower = tech.lower()
    if "nvlink" in tech_lower or "hccs" in tech_lower:
        tech_bonus = 2.0
        tech_label = f"{tech[:20]} +2.0"
    elif any(t in tech_lower for t in ["infinity fabric", "ici", "matrixlink", "c2c"]):
        tech_bonus = 1.5
        tech_label = f"{tech[:20]} +1.5"
    elif tech:
        tech_bonus = 1.0
        tech_label = f"{tech[:20]} +1.0"
    else:
        tech_bonus = 0.0
        tech_label = "无互联技术"

    score = min(bw_score + tech_bonus, 10.0)
    detail = f"VRAM带宽{vbw:.0f}GB/s({bw_score:.1f}) + {tech_label} = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "vram_bw_gb_s": vbw, "bw_score": round(bw_score, 2),
            "interconnect_tech": tech, "tech_bonus": tech_bonus,
            "formula": "VRAM BW piecewise(0-8) + tech_bonus(0-2)",
            "source": "chip.vram_bw_gb_s (88% cov) + chip.interconnect_tech",
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #6: 生态成熟度 (Ecosystem Maturity) — weight 8-13%
# ═══════════════════════════════════════════════════════════

def score_ecosystem_maturity(cloud: int, compat_verified: int,
                             has_frameworks: bool = False) -> DimensionResult:
    """Composite: cloud support (+3) + verified compat (+4 max, 0.8/ea) + frameworks (+3).

    Max: 3 + 4 + 3 = 10.0. (v3.0: removed maturity_level — too abstract)
    Coverage: cloud_available=0, software_stack=41, compatible_frameworks=19, verified_compat=10 rows.
    Missing data → statistical mean 2.8 (48-chip average with any ecosystem signal).
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
        MEAN = 2.8  # statistical mean of 48 chips with any ecosystem data (4.4% coverage)
        score = MEAN
        detail = f"无生态数据 → 统计均值 {MEAN:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "cloud_available": cloud_val,
            "compat_verified_count": compat_val,
            "has_frameworks": has_frameworks,
            "formula": "min(cloud*3 + min(compat*0.8,4) + fw*3, 10)",
            "source": "chip.cloud_available + chip_model_compatibility + chip.software_stack",
            "missing": not parts,
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
        return DimensionResult(score=5.0, detail=f"无法估算训练天数 → 中性分 5.0/10",
                               raw_values={"target_days": target_days, "source": "server.py effective_per_card_day calculation", "missing": True})
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
        return DimensionResult(score=5.0, detail=f"无法估算推理吞吐 → 中性分 5.0/10",
                               raw_values={"target_tps": target_tps, "source": "benchmark or estimate_inference_tps()", "missing": True})
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

def score_production_readiness(status: str, is_released: str,
                                fp16_tflops: float = 0.0) -> DimensionResult:
    """How ready this chip is for production deployment.

    Data: 82% of chips have no production_status (896/1093). Among the 197 with status:
    量产=162, 已发布=14, 未公开发布=7, others=14. Overall mean score=5.78 (all 1093 chips).
    For missing status, infer from fp16 perf: >100T → likely production-grade → 7.0.
    Unknown with no signals → statistical mean 5.8.
    """
    MEAN = 5.8  # statistical mean of all 1093 chips (100% have is_released or status)
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
    elif fp16_tflops > 100:
        # High-performance chips without explicit status → likely production
        score, detail = 7.0, f"FP16={fp16_tflops:.0f}T → 推测量产级 → 7/10"
    else:
        score, detail = MEAN, f"状态未知 → 统计均值 {MEAN:.1f}/10"
    return DimensionResult(
        score=score, detail=detail,
        raw_values={"production_status": s, "is_released": r,
                    "fp16_tflops": fp16_tflops,
                    "formula": "status lookup + fp16>100 inference bonus",
                    "source": "chip.production_status (18% coverage) + chip.precision_perf"},
    )


# ═══════════════════════════════════════════════════════════
# Dimension #9: 软件栈兼容性 (Software Compatibility) — weight 8%
# ═══════════════════════════════════════════════════════════

# Major frameworks worth 2.5 each, minor worth 1.0 each
_MAJOR_FRAMEWORKS = ["pytorch", "tensorflow", "jax", "mindspore", "paddlepaddle", "vllm", "onnx"]
_MINOR_FRAMEWORKS = ["deepspeed", "megatron", "fsdp", "tensorrt", "openvino", "triton",
                      "llama.cpp", "sglang", "lmdeploy", "text-generation-inference"]


def score_software_compat(software_stack: str, compatible_frameworks: str) -> DimensionResult:
    """Framework support breadth. 7 major × 2.5 = 17.5, capped at 10.
    Coverage: software_stack=41 chips (3.7%), compatible_frameworks=19 chips (1.7%).
    Missing data → statistical mean 5.8 (23-chip average with any framework signal).
    D9 is bonus (weight=0.0 in weight budget), so missing score doesn't distort primary ranking.
    """
    fw_text = (str(software_stack or "") + " " + str(compatible_frameworks or "")).lower()
    major_hits = sum(1 for fw in _MAJOR_FRAMEWORKS if fw in fw_text)
    minor_hits = sum(1 for fw in _MINOR_FRAMEWORKS if fw in fw_text)
    score = min(major_hits * 2.5 + minor_hits * 1.0, 10.0)
    if major_hits == 0 and minor_hits == 0:
        MEAN = 5.8  # statistical mean of 23 chips with any framework data (2.1% coverage)
        score = MEAN
        detail = f"未检测到主流框架支持 → 统计均值 {MEAN:.1f}/10"
    else:
        detail = f"主流框架×{major_hits} (+{major_hits*2.5}) + 工具链×{minor_hits} (+{minor_hits}) = {score:.1f}/10"
    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "major_frameworks_found": major_hits, "minor_tools_found": minor_hits,
            "formula": "min(major*2.5 + minor*1.0, 10)",
            "source": "chip.software_stack + chip.compatible_frameworks",
            "missing": major_hits == 0 and minor_hits == 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# Dimension #10: 实测验证度 (Benchmark Evidence) — weight 7-8%
# ═══════════════════════════════════════════════════════════

def score_benchmark_evidence(benchmark_count: int, max_mfu: Optional[float],
                              max_tps: Optional[float], scenario: str) -> DimensionResult:
    """Reward chips with real benchmark data.

    Data: 430 distinct chips have benchmarks (table), but only 33 match chips table by name.
    Among the 33: mean score=6.22, median=5.95.
    Count-based tiers (primary): 0=missing, 1-3→5-6, 4-13→6-8, 13+→8-10.
    MFU/TPS as bonus multipliers (×0.8-1.2) rather than primary score drivers.
    Missing → statistical mean 6.2 (33-chip average).
    """
    if benchmark_count == 0:
        MEAN = 6.2  # statistical mean of 33 chips with matched benchmarks (3.0% coverage)
        return DimensionResult(score=MEAN, detail=f"无实测benchmark数据 → 统计均值 {MEAN:.1f}/10",
                               raw_values={"benchmark_count": 0,
                                           "source": "chip_model_benchmarks (33 matched / 430 total, 3% coverage)",
                                           "missing": True})

    # Count-based base score
    if benchmark_count <= 3:
        base = 5.0 + benchmark_count / 3.0 * 1.0  # 1→5.3, 2→5.7, 3→6.0
    elif benchmark_count <= 13:  # P90
        base = 6.0 + (benchmark_count - 3) / 10.0 * 2.0  # 4→6.2, 13→8.0
    else:
        base = min(8.0 + (benchmark_count - 13) / 87.0 * 2.0, 9.0)  # 100→10

    # MFU bonus (×0.9-1.1 multiplier)
    mfu = float(max_mfu or 0)
    mfu_mult = 1.0
    if mfu > 30:
        mfu_mult = 1.0 + min((mfu - 30) / 70.0, 0.1)  # 30%MFU→1.0, 100%→1.1
    elif mfu > 0:
        mfu_mult = 0.9  # low MFU penalty

    # TPS bonus (×0.9-1.1 multiplier)
    tps = float(max_tps or 0)
    tps_mult = 1.0
    if tps > 1000:
        tps_mult = 1.0 + min((tps - 1000) / 19000.0, 0.1)
    elif tps > 0:
        tps_mult = 0.9

    mult = (mfu_mult + tps_mult) / 2.0
    score = min(base * mult, 10.0)

    detail = f"{benchmark_count}条实测"
    if mfu > 0: detail += f" MFU={mfu:.0f}%"
    if tps > 0: detail += f" TPS={tps:.0f}"
    detail += f" → {score:.1f}/10"

    return DimensionResult(
        score=round(score, 2), detail=detail,
        raw_values={
            "benchmark_count": benchmark_count, "max_mfu_pct": mfu,
            "max_throughput_tok_s": tps,
            "base_score": round(base, 2), "mfu_multiplier": round(mfu_mult, 2),
            "tps_multiplier": round(tps_mult, 2),
            "formula": "count-based base(5-9) × MFU/TPS mult(0.9-1.1)",
            "source": "chip_model_benchmarks — count from db, MFU/TPS as bonus",
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
    cat_weights: CategoryWeights,
    prefer_domestic: bool = False,
    prefer_vendor: Optional[str] = None,
) -> ScoringResult:
    """v4.0: Compute all dimension scores, aggregate into 3 categories.

    Formula:
      total = Cat_A × w_A + Cat_B × w_B + Cat_C × w_C    (0-100 scale)
    where each category score = Σ(sub_dim_score × sub_weight) within category.
    """

    chip = ctx.chip

    # ── Step 1: Compute all 11 sub-dimension scores (same as v3.x) ──

    dims: dict[str, DimensionResult] = {}

    # D1: 算力性能
    dims["compute_perf"] = score_compute_perf(ctx.fp16_tflops)

    # D2: 显存充裕度
    dims["vram_sufficiency"] = score_vram_sufficiency(
        float(chip.get("vram_gb", 0) or 0), ctx.min_vram_total, ctx.vram_cards,
    )

    # D3: 制程先进性 (cost_efficiency key kept for API compat)
    _price_score = score_cost_efficiency(
        ctx.fp16_tflops, float(chip.get("price_cny_wan", 0) or 0),
    )
    _process_nm = parse_process_node(str(chip.get("process_node_nm", "") or ""))
    _process_score = score_process_node(_process_nm)
    dims["cost_efficiency"] = _process_score if _process_nm > 0 else _price_score

    # D4: 能效比
    dims["power_efficiency"] = score_power_efficiency(
        ctx.fp16_tflops, float(chip.get("tdp_w", 0) or 0),
    )

    # D5: 互联扩展
    dims["interconnect_quality"] = score_interconnect_quality(
        float(chip.get("interconnect_bw_gb_s", 0) or 0),
        str(chip.get("interconnect_tech", "") or ""),
        vram_bw=float(chip.get("vram_bw_gb_s", 0) or 0),
    )

    # D6: 生态成熟度
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
        fp16_tflops=ctx.fp16_tflops,
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

    # D11: 国产化优先
    dims["domestic_priority"] = score_domestic_priority(
        str(chip.get("vendor_region", "") or ""),
        prefer_domestic, prefer_vendor,
        str(chip.get("vendor", "") or ""),
    )

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
                "compute_perf": "D1算力", "vram_sufficiency": "D2显存",
                "interconnect_quality": "D5互联", "sla_satisfaction": "D7 SLA",
                "production_readiness": "D8量产", "cost_efficiency": "D3制程",
                "power_efficiency": "D4能效", "ecosystem_maturity": "D6生态",
                "software_compat": "D9软件", "benchmark_evidence": "D10实测",
                "domestic_priority": "D11国产",
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
        version="4.0.0",
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
     "category": "compute_power", "sub_weight": 0.30},
    {"id": "vram_sufficiency",      "name_cn": "显存充裕度",   "name_en": "VRAM Sufficiency",
     "desc": "单卡显存相比模型需求的余量倍数", "unit": "比率",
     "category": "compute_power", "sub_weight": 0.30},
    {"id": "cost_efficiency",       "name_cn": "制程先进性",   "name_en": "Process Node",
     "desc": "芯片制程工艺(nm)，越小越先进", "unit": "nm",
     "category": "cost_effectiveness", "sub_weight": 0.50},
    {"id": "power_efficiency",      "name_cn": "能效比",      "name_en": "Power Efficiency",
     "desc": "每瓦功耗产出的算力", "unit": "GFLOPS/W",
     "category": "cost_effectiveness", "sub_weight": 0.50},
    {"id": "interconnect_quality",  "name_cn": "互联扩展性",   "name_en": "Interconnect Quality",
     "desc": "多卡互联带宽+技术等级", "unit": "GB/s + 技术分",
     "category": "compute_power", "sub_weight": 0.15},
    {"id": "ecosystem_maturity",    "name_cn": "生态成熟度",   "name_en": "Ecosystem Maturity",
     "desc": "云平台可用+已验证兼容模型数+框架支持", "unit": "综合分",
     "category": "ecosystem_maturity", "sub_weight": 0.40},
    {"id": "sla_satisfaction",      "name_cn": "SLA满足度",    "name_en": "SLA Satisfaction",
     "desc": "训练天数/推理吞吐是否满足目标（量化场景固定5.0）", "unit": "SLA分",
     "category": "compute_power", "sub_weight": 0.10},
    {"id": "production_readiness",  "name_cn": "生产就绪度",   "name_en": "Production Readiness",
     "desc": "量产/已发布/未公开等状态", "unit": "等级",
     "category": "compute_power", "sub_weight": 0.15},
    {"id": "benchmark_evidence",    "name_cn": "实测验证度",   "name_en": "Benchmark Evidence",
     "desc": "是否有实测benchmark数据(MFU/吞吐)", "unit": "证据分",
     "category": "ecosystem_maturity", "sub_weight": 0.30},
    {"id": "software_compat",       "name_cn": "软件栈兼容",   "name_en": "Software Compatibility",
     "desc": "支持的主流框架和工具链数量", "unit": "框架数",
     "category": "ecosystem_maturity", "sub_weight": 0.20},
    {"id": "domestic_priority",     "name_cn": "国产化优先",   "name_en": "Domestic Priority",
     "desc": "国产/厂商偏好匹配加分", "unit": "偏好分",
     "category": "ecosystem_maturity", "sub_weight": 0.10},
]
