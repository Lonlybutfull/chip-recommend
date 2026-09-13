"""Regression tests for transparent VRAM/card sizing and MoE semantics."""

import json

from chip_model.scoring import (
    CAT_WEIGHTS_INFER,
    CAT_WEIGHTS_QUANTIZE,
    CAT_WEIGHTS_TRAIN,
    DIMENSION_META,
    SUB_DIMS_COMPUTE_POWER,
    estimate_card_count,
    estimate_inference_concurrency_cards,
    estimate_vram_total,
    resolve_arch_params,
    resolve_moe_metadata,
)


def test_category_weights_prioritize_ecosystem_and_benchmark_evidence():
    expected = {
        "compute_power": 0.20,
        "cost_effectiveness": 0.10,
        "ecosystem_maturity": 0.40,
        "benchmark_evidence": 0.30,
    }
    for weights in (CAT_WEIGHTS_TRAIN, CAT_WEIGHTS_INFER, CAT_WEIGHTS_QUANTIZE):
        assert weights.to_dict() == expected
        assert weights.validate()


def test_bandwidth_weight_is_reduced_and_summary_mentions_concurrency():
    assert SUB_DIMS_COMPUTE_POWER == {
        "compute_perf": 0.90,
        "bandwidth_adequacy": 0.10,
    }
    meta = {item["id"]: item for item in DIMENSION_META}
    assert meta["compute_perf"]["sub_weight"] == 0.90
    assert meta["bandwidth_adequacy"]["sub_weight"] == 0.10
    assert "更多并发请求" in meta["bandwidth_adequacy"]["explain_cn"]


def test_sft_34b_breaks_vram_into_model_state_and_activations():
    arch = {
        "num_layers": 32,
        "num_kv_layers": 32,
        "num_kv_heads": 8,
        "head_dim": 128,
        "hidden_size": 4096,
    }
    result = estimate_vram_total(
        34.0, scenario="train", stage="sft", method="full_param",
        batch_size=1, seq_len=2048, arch=arch,
    )

    assert result["weight_vram"] == 510.0
    assert result["activation_vram"] == 10.7
    assert result["min_vram"] == 520.7
    components = result["calculation"]["components"]
    assert [c["id"] for c in components] == ["model_states", "activations"]
    assert components[0]["inputs"] == {
        "params_b": 34.0,
        "bytes_per_param": 12.0,
        "safety_factor": 1.25,
    }


def test_moe_inference_uses_total_weights_not_active_parameters():
    arch = {
        "num_layers": 40,
        "num_kv_layers": 10,
        "num_kv_heads": 2,
        "head_dim": 256,
        "hidden_size": 2048,
    }
    result = estimate_vram_total(
        36.0, scenario="inference", quant="fp16",
        moe_activated_B=3.0, max_context=4096, arch=arch,
    )

    # 36B total × 2 bytes × 1.25; 3B active/token is compute metadata only.
    assert result["weight_vram"] == 90.0
    assert result["min_vram"] == result["full_vram"] == 90.1
    assert result["calculation"]["parameter_basis"] == "total"
    assert result["calculation"]["active_params_b"] == 3.0


def test_inference_single_request_kv_includes_input_and_output_tokens():
    arch = {
        "num_layers": 32,
        "num_kv_layers": 32,
        "num_kv_heads": 8,
        "head_dim": 128,
        "hidden_size": 4096,
    }
    result = estimate_vram_total(
        7.0, scenario="inference", quant="fp16",
        input_len=4096, output_len=512, arch=arch,
    )

    assert result["total_context"] == 4608
    assert result["kv_cache_gb"] == 0.604
    assert result["ideal_kv_gb"] == result["kv_cache_gb"]
    assert result["full_vram"] == 18.1
    assert result["min_vram"] == result["full_vram"]
    assert result["calculation"]["full"]["component_ids"] == [
        "weights", "kv_cache_request",
    ]
    kv_component = result["calculation"]["components"][1]
    assert kv_component["label"] == "单请求峰值 KV Cache"
    assert kv_component["inputs"]["input_tokens"] == 4096
    assert kv_component["inputs"]["output_tokens"] == 512
    assert kv_component["inputs"]["total_context"] == 4608
    assert kv_component["inputs"]["kv_cache_dtype"] == "BF16/FP16"
    assert kv_component["inputs"]["bytes_per_element"] == 2


def test_target_concurrency_uses_one_weight_copy_in_shared_pool():
    result = estimate_inference_concurrency_cards(
        weight_vram_gb=992.5,
        per_request_kv_gb=17.180,
        concurrency=100,
        per_card_vram_gb=96.0,
    )

    assert result["target_vram_gb"] == 2710.5
    assert result["capacity_raw_cards"] == 29
    assert result["rounded_cards"] == 32
    assert result["throughput_raw_cards"] is None
    assert "实例" not in result["formula"]


def test_target_concurrency_applies_throughput_only_when_explicit():
    no_target = estimate_inference_concurrency_cards(
        100.0, 1.0, 100, 80.0, per_card_tps=1000.0,
    )
    explicit_target = estimate_inference_concurrency_cards(
        100.0, 1.0, 100, 80.0,
        per_card_tps=1000.0, per_request_tps=50.0,
    )

    assert no_target["throughput_raw_cards"] is None
    assert explicit_target["throughput_raw_cards"] == 5
    assert explicit_target["throughput_rounded_cards"] == 8


def test_inference_more_output_tokens_increase_complete_request_vram():
    arch = {
        "num_layers": 32,
        "num_kv_layers": 32,
        "num_kv_heads": 8,
        "head_dim": 128,
        "hidden_size": 4096,
    }
    short = estimate_vram_total(
        7.0, scenario="inference", input_len=4096, output_len=128, arch=arch,
    )
    long = estimate_vram_total(
        7.0, scenario="inference", input_len=4096, output_len=4096, arch=arch,
    )

    assert long["total_context"] > short["total_context"]
    assert long["kv_cache_gb"] > short["kv_cache_gb"]
    assert long["full_vram"] > short["full_vram"]


def test_moe_full_parameter_training_uses_total_parameters():
    arch = {
        "num_layers": 40,
        "num_kv_layers": 10,
        "num_kv_heads": 2,
        "head_dim": 256,
        "hidden_size": 2048,
    }
    result = estimate_vram_total(
        36.0, scenario="train", stage="sft", method="full_param",
        moe_activated_B=3.0, batch_size=1, seq_len=2048, arch=arch,
    )

    assert result["weight_vram"] == 540.0
    assert result["calculation"]["parameter_basis"] == "total"


def test_card_count_exact_division_does_not_add_an_extra_card():
    result = estimate_card_count(512.0, 128.0)
    assert result["raw_cards"] == 4
    assert result["rounded_cards"] == 4


def test_nested_text_config_resolves_moe_architecture_and_active_params():
    config = {
        "model_type": "qwen3_5_moe",
        "text_config": {
            "model_type": "qwen3_5_moe_text",
            "hidden_size": 2048,
            "num_hidden_layers": 40,
            "num_attention_heads": 16,
            "num_key_value_heads": 2,
            "head_dim": 256,
            "num_experts": 256,
            "num_experts_per_tok": 8,
            "layer_types": ["linear_attention"] * 30 + ["full_attention"] * 10,
        },
    }
    raw = json.dumps(config)

    arch = resolve_arch_params(raw, 36.0)
    moe = resolve_moe_metadata("Qwen/Qwen3.5-35B-A3B", "MoE", 36.0, raw)

    assert arch["num_layers"] == 40
    assert arch["num_kv_layers"] == 10
    assert arch["num_experts"] == 256
    assert moe["is_moe"] is True
    assert moe["active_params_b"] == 3.0
    assert moe["weight_params_b"] == 36.0
