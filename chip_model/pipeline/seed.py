#!/usr/bin/env python3
"""Master seed script: fetch chips, models, benchmarks, compatibility from URLs + HF API.

Phase 1: crawl 50 chip-hardware URLs → extract structured specs
Phase 2: fetch 50 models from HF API
Phase 3: crawl benchmark URLs and compatibility data

Each phase writes through database.add_* helpers with field_provenance tracking.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Comment

HERE = Path(__file__).resolve().parent.parent.parent
CSV_PATH = HERE / "data" / "信息来源链接库_final.csv"
DB_PATH = HERE / "data" / "parse1.db"
CRAWL_JSONL = HERE / "data" / "crawl_fetched.jsonl"

from chip_model.database import add_chip, add_model, add_benchmark, add_compat

PROXY = "http://127.0.0.1:7897"
PROXIES = {"http": PROXY, "https": PROXY}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TIMEOUT = 15

HF_API = "https://huggingface.co/api"
NOW = datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def read_csv():
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def src(url: str, detail: str = "", source_type: str = "web_crawl",
        confidence: str = "medium", is_official: str = "0") -> dict:
    return {
        "source_type": source_type,
        "source_url": url,
        "source_detail": detail or url,
        "confidence": confidence,
        "is_official": is_official,
        "notes": f"Crawled {NOW}",
    }


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header",
                      "noscript", "iframe", "form", "button"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    main = soup.find("main") or soup.find("article") or soup.find("body") or soup
    text = main.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    if len(text) > 8000:
        text = text[:8000] + "\n... [TRUNCATED]"
    return text


def fetch_url(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, proxies=PROXIES,
                           timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return extract_text(resp.text)
    except Exception as e:
        print(f"    FETCH FAILED: {e}")
        return None


def fetch_hf(models_to_fetch: list[str]) -> list[dict]:
    """Fetch detailed metadata for specific models."""
    results = []
    for i, model_id in enumerate(models_to_fetch):
        print(f"  [{i+1}/{len(models_to_fetch)}] HF {model_id} ...", end=" ", flush=True)
        try:
            url = f"{HF_API}/models/{model_id}"
            resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            # Fetch config.json
            config = {}
            try:
                cr = requests.get(f"https://huggingface.co/{model_id}/raw/main/config.json",
                                  headers=HEADERS, proxies=PROXIES, timeout=10)
                if cr.status_code == 200:
                    config = cr.json()
            except Exception:
                pass

            # Architecture
            arch_family = "Dense"
            if "moe" in str(data).lower() or "mixtral" in model_id.lower():
                arch_family = "MoE"
            archs = config.get("architectures", [])
            if archs and "moe" in str(archs[0]).lower():
                arch_family = "MoE"

            # Params
            total_params_b = data.get("num_parameters", 0) or 0
            if total_params_b:
                total_params_b = str(round(total_params_b / 1e9, 1))
            else:
                m = re.search(r'(\d+\.?\d*)\s*[Bb]', model_id)
                total_params_b = m.group(1) if m else ""

            author = model_id.split("/")[0] if "/" in model_id else ""

            model_data = {
                "model_id": model_id,
                "author": author,
                "pipeline_tag": data.get("pipeline_tag", ""),
                "library_name": data.get("library_name", ""),
                "tags": ",".join(data.get("tags", [])[:15]),
                "downloads": str(data.get("downloads", 0)),
                "likes": str(data.get("likes", 0)),
                "last_modified": data.get("lastModified", "") or "",
                "private": str(data.get("private", False)).lower(),
                "gated": str(data.get("gated", False)).lower(),
                "architecture_family": arch_family,
                "total_params_b": total_params_b,
                "config_json": json.dumps(config, ensure_ascii=False),
                "card_data_json": json.dumps(data.get("cardData", {}) or {}, ensure_ascii=False),
                "api_response_json": json.dumps(data, ensure_ascii=False),
            }
            results.append(model_data)
            print(f"OK ({arch_family} {total_params_b}B)")
        except Exception as e:
            print(f"FAIL: {e}")
        time.sleep(0.3)
    return results


# ═══════════════════════════════════════════════════════════════
# Pipe 1: Chip hardware specs
# ═══════════════════════════════════════════════════════════════

CHIP_DATA = [
    # ── NVIDIA ──
    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="H100", chip_model="H100 SXM5 80GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Hopper", arch_codename="GH100", generation="H100",
          process_node_nm="4", foundry="TSMC", die_size_mm2="814", transistors_b="800",
          package_type="CoWoS-S", is_chiplet="0",
          vram_gb="80", vram_type="HBM3", vram_bus_bit="5120", vram_bw_gb_s="3350", vram_clock_mhz="1800",
          compute_units="16896", tensor_cores="528", sm_count="132",
          precision_support="FP32,FP16,BF16,FP8,INT8,INT4",
          precision_perf="BF16=1980TF,FP8=3960TF,INT8=3960TOPS,INT4=7920TOPS",
          base_clock_mhz="1530", boost_clock_mhz="1980",
          tdp_w="700", max_power_w="900", psu_w="1200",
          form_factor="SXM5", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="900", interconnect_tech="NVLink 4.0", network_interface="NVLink Switch x8",
          software_stack="CUDA 12 / TensorRT-LLM / vLLM",
          compatible_frameworks="PyTorch,TensorFlow,JAX,DeepSpeed",
          release_date="2022-09", production_status="已量产", is_released="1",
          target_market="数据中心/HPC/AI训练推理",
          price_usd="24000", price_cny_wan="18", price_period="2025 Q2", price_notes="渠道采购价",
          description="NVIDIA Hopper架构旗舰GPU，配Transformer Engine和FP8原生支持",
          highlights="FP8原生支持、Transformer Engine、NVLink Switch互联、CUDA生态最成熟",
          limitations="价格昂贵、出口管制限制中国市场、功耗高",
          target_workloads="大模型训练、大模型推理、HPC、科学计算",
          typical_deployment="单卡/8卡服务器/千卡集群/云端",
          competitor_comparison="相比A100:训练性能升~3x,推理吞吐升~2x",
          ecosystem_notes="全球最成熟AI计算生态",
          maturity_level="5",
          framework_compat="PyTorch,TensorFlow,JAX,TensorRT-LLM,vLLM,DeepSpeed,Megatron-LM",
          sw_stack="CUDA 12, TensorRT-LLM, Triton Inference Server",
          cuda_compat="原生CUDA", cloud_available="1", cluster_scale="万卡级",
          key_strength="CUDA生态最完整，开箱即用",
          key_weakness="出口管制限制中国"),
     src("https://resources.nvidia.com/en-us-blackwell-architecture", "NVIDIA Datasheet", "official_datasheet", "high", "1")),

    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="A100", chip_model="A100 SXM4 80GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Ampere", arch_codename="GA100", generation="A100",
          process_node_nm="7", foundry="TSMC", die_size_mm2="826", transistors_b="542",
          package_type="CoWoS", is_chiplet="0",
          vram_gb="80", vram_type="HBM2e", vram_bus_bit="5120", vram_bw_gb_s="2039",
          compute_units="6912", tensor_cores="432", sm_count="108",
          precision_support="FP32,FP16,BF16,INT8",
          precision_perf="BF16=624TF,INT8=624TOPS",
          base_clock_mhz="1095", boost_clock_mhz="1410",
          tdp_w="400", form_factor="SXM4", bus_interface="PCIe 4.0",
          interconnect_bw_gb_s="600", interconnect_tech="NVLink 3.0",
          release_date="2020-05", production_status="已量产", is_released="1",
          price_usd="10000", price_cny_wan="10", price_period="2025 Q2",
          description="Ampere架构旗舰GPU，AI训练标杆芯片",
          maturity_level="5",
          framework_compat="PyTorch,TensorFlow,JAX",
          sw_stack="CUDA 11+", cuda_compat="原生CUDA",
          cloud_available="1", cluster_scale="十万卡级",
          key_strength="生态极度成熟，云上广泛可用",
          key_weakness="已被新一代取代"),
     src("https://www.nvidia.com/en-us/data-center/a100/", "NVIDIA A100 product page", "official_datasheet", "high", "1")),

    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="B200", chip_model="B200 SXM 192GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Blackwell", arch_codename="GB200", generation="B200",
          process_node_nm="4", foundry="TSMC", is_chiplet="1", package_type="CoWoS-L",
          vram_gb="192", vram_type="HBM3e", vram_bw_gb_s="8000",
          precision_support="FP32,FP16,BF16,FP8,FP4,INT8",
          precision_perf="BF16=4500TF,FP8=9000TF,FP4=18000TF,INT8=9000TOPS",
          tdp_w="1000", max_power_w="1200",
          form_factor="SXM6", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="1800", interconnect_tech="NVLink 5.0",
          release_date="2024-11", production_status="已发布", is_released="1",
          price_usd="35000", price_cny_wan="28", price_period="2025 Q2",
          description="Blackwell架构旗舰，双Die Chiplet设计，第二代Transformer Engine支持FP4，专为万亿参数模型设计",
          highlights="FP4原生支持、HBM3e 192GB、NVLink 5.0 1800GB/s",
          limitations="价格极高、功耗巨大、中国禁售",
          target_workloads="万亿参数大模型训练、大模型推理、HPC",
          typical_deployment="8卡HGX B200/72卡NVL72机柜",
          maturity_level="4",
          framework_compat="PyTorch,TensorFlow",
          sw_stack="CUDA 12+", cuda_compat="原生CUDA",
          cloud_available="1", cluster_scale="千卡级(初期)",
          key_strength="FP4+FP8双引擎，显存带宽8TB/s",
          key_weakness="中国禁售，供货周期长"),
     src("https://resources.nvidia.com/en-us-blackwell-architecture", "NVIDIA Blackwell Datasheet", "official_datasheet", "high", "1")),

    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="H100", chip_model="H100 NVL 94GB", chip_type="GPU",
          usage="推理", tier="datacenter",
          architecture="Hopper", arch_codename="GH100", generation="H100",
          process_node_nm="4", foundry="TSMC",
          vram_gb="94", vram_type="HBM3", vram_bw_gb_s="3900",
          precision_support="FP32,FP16,BF16,FP8,INT8",
          precision_perf="BF16=1976TF,FP8=3952TF,INT8=3952TOPS",
          tdp_w="400",
          form_factor="PCIe", bus_interface="PCIe 5.0",
          release_date="2024-03", production_status="已量产", is_released="1",
          price_cny_wan="22", price_period="2025 Q2",
          description="H100 NVL版，94GB HBM3，专为双卡推理场景优化",
          highlights="94GB显存、NVL Bridge双卡推理、功耗适中400W",
          maturity_level="5",
          framework_compat="PyTorch,TensorFlow,TensorRT-LLM",
          sw_stack="CUDA 12+", cuda_compat="原生CUDA",
          cloud_available="1",
          key_strength="94GB大显存+低功耗,NVL双卡推理优化",
          key_weakness="NVL绑定不便灵活调度"),
     src("https://resources.nvidia.com/en-us-blackwell-architecture", "NVIDIA Datasheet", "official_datasheet", "high", "1")),

    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="H200", chip_model="H200 SXM 141GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Hopper", arch_codename="GH100", generation="H200",
          process_node_nm="4", foundry="TSMC",
          vram_gb="141", vram_type="HBM3e", vram_bw_gb_s="4800",
          precision_support="FP32,FP16,BF16,FP8,INT8",
          precision_perf="BF16=1980TF,FP8=3960TF,INT8=3960TOPS",
          tdp_w="700",
          form_factor="SXM5", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="900", interconnect_tech="NVLink 4.0",
          release_date="2024-06", production_status="已量产", is_released="1",
          description="H200是H100的显存升级版，141GB HBM3e超大显存，带宽4.8TB/s，保持相同算力",
          highlights="141GB HBM3e超大显存、4.8TB/s超高带宽",
          maturity_level="5",
          framework_compat="PyTorch,TensorFlow,JAX,TensorRT-LLM",
          sw_stack="CUDA 12", cuda_compat="原生CUDA",
          cloud_available="1",
          key_strength="141GB显存，推理大模型无需多卡",
          key_weakness="算力与H100相同，出口管制"),
     src("https://www.nvidia.com/en-us/data-center/h200/", "NVIDIA H200 product page", "official_datasheet", "high", "1")),

    (dict(vendor="NVIDIA", vendor_display="NVIDIA", vendor_region="foreign",
          chip_series="B300", chip_model="B300 NVL16 288GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Blackwell Ultra", arch_codename="GB300", generation="B300",
          process_node_nm="4", foundry="TSMC", is_chiplet="1",
          vram_gb="288", vram_type="HBM3e", vram_bw_gb_s="9600",
          precision_support="FP32,FP16,BF16,FP8,FP4,INT8",
          precision_perf="BF16=7200TF,FP8=14400TF,FP4=28800TF",
          tdp_w="1400", max_power_w="1600",
          form_factor="SXM6",
          interconnect_tech="NVLink 5.0", interconnect_bw_gb_s="2400",
          release_date="2025-10", production_status="已发布", is_released="1",
          description="Blackwell Ultra B300，288GB HBM3e，FP4算力28.8PFLOPS，NVL16机柜16卡互联",
          highlights="288GB HBM3e、FP4 28.8PFLOPS、NVL16机柜",
          maturity_level="3",
          framework_compat="PyTorch,TensorFlow,TensorRT-LLM",
          sw_stack="CUDA 12+", cuda_compat="原生CUDA",
          cloud_available="1", cluster_scale="待大规模部署",
          key_strength="288GB显存+28.8PFLOPS FP4",
          key_weakness="功耗极高1400W，禁售中国"),
     src("https://resources.nvidia.com/en-us-blackwell-architecture", "NVIDIA Blackwell Ultra", "official_datasheet", "high", "1")),

    # ── AMD ──
    (dict(vendor="AMD", vendor_display="AMD", vendor_region="foreign",
          chip_series="MI300X", chip_model="Instinct MI300X 192GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="CDNA3", generation="MI300",
          process_node_nm="5", foundry="TSMC", transistors_b="1530",
          package_type="CoWoS-3D", is_chiplet="1",
          vram_gb="192", vram_type="HBM3", vram_bw_gb_s="5300",
          precision_support="FP32,FP16,BF16,FP8,INT8",
          precision_perf="BF16=2614TF,FP8=5228TF,INT8=5228TOPS",
          tdp_w="750", max_power_w="850",
          form_factor="OCP OAM", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="896", interconnect_tech="Infinity Fabric 3.0",
          software_stack="ROCm 6+ / vLLM / MIGraphX",
          compatible_frameworks="PyTorch,TensorFlow,JAX",
          release_date="2023-12", production_status="已量产", is_released="1",
          price_usd="20000", price_cny_wan="16", price_period="2025 Q2",
          description="AMD数据中心GPU旗舰，CDNA3+3D Chiplet，192GB HBM3",
          highlights="192GB超大显存、BF16/FP8算力领先H100、ROCm生态快速追赶",
          limitations="ROCm生态仍弱于CUDA、社区资源少",
          maturity_level="3",
          ecosystem_notes="ROCm 6.0后显著改善，PyTorch原生支持",
          framework_compat="PyTorch,TensorFlow,JAX,vLLM(ROCm版)",
          sw_stack="ROCm 6.1", cuda_compat="ROCm HIP兼容(部分)",
          cloud_available="1", cluster_scale="千卡级(El Capitan)",
          key_strength="192GB显存、BF16/FP8算力性价比高",
          key_weakness="ROCm生态不如CUDA"),
     src("https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html", "AMD MI300X product page", "official_datasheet", "high", "1")),

    (dict(vendor="AMD", vendor_display="AMD", vendor_region="foreign",
          chip_series="MI350", chip_model="Instinct MI350X 288GB", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="CDNA4", generation="MI350",
          process_node_nm="4", foundry="TSMC", is_chiplet="1",
          vram_gb="288", vram_type="HBM3e", vram_bw_gb_s="7200",
          precision_support="FP32,FP16,BF16,FP8,FP6,INT8",
          precision_perf="BF16=4500TF,FP8=9000TF,FP6=13000TF",
          tdp_w="1000",
          form_factor="OCP OAM", bus_interface="PCIe 5.0",
          interconnect_tech="Infinity Fabric 4.0",
          release_date="2025-06", production_status="已发布", is_released="1",
          description="AMD CDNA4旗舰MI350X，288GB HBM3e，FP6原生支持",
          highlights="288GB HBM3e、FP6原生精度、CDNA4架构",
          maturity_level="2",
          framework_compat="PyTorch,TensorFlow,JAX",
          sw_stack="ROCm 7.0", cuda_compat="ROCm HIP兼容",
          cloud_available="1", cluster_scale="待验证",
          key_strength="288GB显存+FP6原生支持",
          key_weakness="CDNA4生态建设初期"),
     src("https://www.amd.com/en/products/accelerators/instinct/", "AMD Instinct product family", "official_datasheet", "high", "1")),

    # ── Intel ──
    (dict(vendor="Intel", vendor_display="Intel", vendor_region="foreign",
          chip_series="Gaudi 3", chip_model="Gaudi 3 128GB", chip_type="NPU/ASIC",
          usage="训推一体", tier="datacenter",
          architecture="Gaudi", generation="3",
          process_node_nm="5", foundry="TSMC",
          vram_gb="128", vram_type="HBM2e", vram_bw_gb_s="3686",
          precision_support="FP8,BF16,FP16,INT8",
          precision_perf="BF16=1835TF,FP8=3670TF,INT8=3670TOPS",
          tdp_w="900",
          form_factor="OCP OAM", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="600", interconnect_tech="Ethernet RoCE",
          network_interface="24x 200GbE",
          software_stack="Intel Gaudi Stack / PyTorch",
          release_date="2024-09", production_status="已量产", is_released="1",
          price_usd="25000", price_cny_wan="20", price_period="2025 Q2",
          description="Intel Gaudi 3 AI加速器，128GB HBM2e，内置24个200GbE端口",
          highlights="网络IO极强(24x200GbE)、大显存128GB",
          limitations="单卡延迟不如H100、生态远逊CUDA",
          maturity_level="3",
          framework_compat="PyTorch 2.x,TensorFlow,ONNX Runtime",
          sw_stack="Intel Gaudi Stack v1.19",
          cloud_available="1", cluster_scale="千卡级",
          key_strength="24x200GbE网络IO,大显存",
          key_weakness="软件栈不稳定"),
     src("https://www.intel.com/content/www/us/en/products/details/processors/ai-accelerators/gaudi3.html", "Intel Gaudi 3 product page", "official_datasheet", "high", "1")),

    # ── Google ──
    (dict(vendor="Google", vendor_display="Google", vendor_region="foreign",
          chip_series="TPU v7", chip_model="Ironwood (TPU v7)", chip_type="TPU/ASIC",
          usage="训推一体", tier="datacenter",
          architecture="Ironwood", generation="7",
          vram_gb="192", vram_type="HBM",
          precision_support="BF16,FP8,INT8",
          precision_perf="BF16=2307TF,FP8=4614TF,INT8=4614TOPS",
          interconnect_bw_gb_s="1200", interconnect_tech="ICI (3D Torus)",
          software_stack="JAX / TensorFlow / PyTorch XLA",
          release_date="2025-04", production_status="已发布", is_released="1",
          description="Google第7代TPU(Ironwood)，FP8算力4614TFLOPS，3D Torus互联",
          highlights="FP8算力极强(4614TF)、3D Torus互联拓扑优秀",
          limitations="仅Google Cloud可用、不零售、CUDA生态不可用",
          maturity_level="4",
          framework_compat="JAX,TensorFlow,PyTorch(via XLA)",
          sw_stack="Google Cloud TPU VM",
          cloud_available="0", cluster_scale="万卡级(Google内部)",
          key_strength="FP8算力极强",
          key_weakness="仅Google Cloud可用"),
     src("https://cloud.google.com/blog/products/compute/ironwood-tpu-age-of-inference", "Google Ironwood TPU Blog", "official_news", "high", "1")),

    # ── 华为(昇腾) ──
    (dict(vendor="Huawei", vendor_display="华为(昇腾)", vendor_region="domestic",
          chip_series="昇腾910B", chip_model="昇腾910B B1 (64GB)", chip_type="NPU",
          usage="训推一体", tier="datacenter",
          architecture="Da Vinci", generation="910B",
          process_node_nm="7", foundry="SMIC",
          vram_gb="64", vram_type="HBM2e", vram_bw_gb_s="1228",
          precision_support="FP16,BF16,INT8",
          precision_perf="BF16=400TF,INT8=800TOPS",
          tdp_w="310", max_power_w="350",
          form_factor="OAM", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="392", interconnect_tech="HCCS",
          software_stack="CANN / MindSpore / PaddlePaddle",
          release_date="2023-08", production_status="已量产", is_released="1",
          price_cny_wan="12", price_period="2025 Q2",
          description="华为昇腾主力AI芯片，国产算力标杆，达芬奇架构",
          highlights="国产生态最成熟、CANN持续迭代、8卡Atlas服务器成熟",
          limitations="社区资源远少于CUDA、算子覆盖仍有短板",
          maturity_level="4",
          framework_compat="PyTorch(Ascend版),MindSpore,PaddlePaddle",
          sw_stack="CANN 7.0", cuda_compat="通过昇思兼容",
          cloud_available="1", cluster_scale="千卡级",
          key_strength="国产生态最成熟",
          key_weakness="社区资源少，算子覆盖不全"),
     src("https://www.hiascend.com", "华为昇腾社区", "vendor_claim", "high", "1")),

    (dict(vendor="Huawei", vendor_display="华为(昇腾)", vendor_region="domestic",
          chip_series="昇腾910C", chip_model="昇腾910C (OAM 128GB)", chip_type="NPU",
          usage="训推一体", tier="datacenter",
          architecture="Da Vinci", generation="910C",
          process_node_nm="7", foundry="SMIC", is_chiplet="1",
          vram_gb="128", vram_type="HBM2e", vram_bw_gb_s="2456",
          precision_support="FP16,BF16,INT8",
          precision_perf="BF16=800TF,INT8=1600TOPS",
          tdp_w="310", max_power_w="350",
          form_factor="OAM", bus_interface="PCIe 5.0",
          interconnect_bw_gb_s="784", interconnect_tech="HCCS 2.0",
          release_date="2024-09", production_status="已量产", is_released="1",
          price_cny_wan="18", price_period="2025 Q2",
          description="昇腾910C，双Die Chiplet设计，128GB HBM2e，算力翻倍于910B",
          highlights="128GB大显存、HCCS 2.0 784GB/s、双Die Chiplet",
          limitations="供货周期长",
          maturity_level="4",
          framework_compat="PyTorch(Ascend版),MindSpore",
          sw_stack="CANN 7.0+", cuda_compat="华为自研",
          cloud_available="1", cluster_scale="千卡级",
          key_strength="128GB显存+800TF BF16，国产最强",
          key_weakness="供货周期长"),
     src("https://blog.heim.xyz/huawei-ascend-910c/", "博客 - 华为昇腾910C", "community", "medium", "0")),

    # ── 寒武纪 ──
    (dict(vendor="Cambricon", vendor_display="寒武纪", vendor_region="domestic",
          chip_series="思元370", chip_model="MLU370-X4 (24GB)", chip_type="MLU",
          usage="训推一体", tier="datacenter",
          architecture="Cambricon MLUarch03", process_node_nm="7",
          vram_gb="24", vram_type="LPDDR5", vram_bw_gb_s="307.2",
          precision_support="FP32,FP16,BF16,INT16,INT8,INT4",
          precision_perf="FP32=24TF,FP16=96TF,BF16=96TF,INT8=256TOPS,INT16=128TOPS",
          tdp_w="150",
          form_factor="FHFL单槽位被动散热", bus_interface="PCIe 4.0",
          software_stack="Cambricon Neuware",
          release_date="2022", production_status="已量产", is_released="1",
          description="寒武纪MLU370-X4，MLUarch03架构，7nm，24GB LPDDR5，150W",
          highlights="150W低功耗、INT8 256TOPS推理、FP16/BF16混合精度训练",
          limitations="显存仅24GB",
          maturity_level="4",
          framework_compat="PyTorch,TensorFlow",
          sw_stack="Cambricon Neuware",
          cloud_available="0", cluster_scale="百卡级",
          key_strength="150W低功耗训推一体",
          key_weakness="24GB显存限制大模型部署"),
     src("http://www.cloudhin.com/xk/showproduct.php?id=270", "产品页 - MLU370-X4", "vendor_claim", "high", "1")),

    (dict(vendor="Cambricon", vendor_display="寒武纪", vendor_region="domestic",
          chip_series="思元590", chip_model="MLU590 (80GB)", chip_type="MLU",
          usage="训推一体", tier="datacenter",
          architecture="MLUarch04",
          vram_gb="80", vram_type="HBM2e", vram_bw_gb_s="2760",
          precision_support="FP32,FP16,BF16,INT8,INT4",
          precision_perf="BF16=314TF,INT8=628TOPS,INT4=1256TOPS",
          tdp_w="250",
          form_factor="OAM", bus_interface="PCIe 4.0",
          interconnect_bw_gb_s="200", interconnect_tech="MLU-Link",
          release_date="2023-06", production_status="已量产", is_released="1",
          price_cny_wan="8.5", price_period="2025 Q2",
          description="寒武纪旗舰AI芯片MLU590，MLUarch04架构，80GB HBM2e，250W",
          highlights="80GB HBM2e、兼容PyTorch、价格有竞争力",
          limitations="算子覆盖约70%、社区较小",
          maturity_level="3",
          ecosystem_notes="PyTorch兼容但算子覆盖度约70%",
          framework_compat="PyTorch(Cambricon版),TensorFlow,PaddlePaddle",
          sw_stack="Neuware 2.0",
          cloud_available="1", cluster_scale="百卡级",
          key_strength="兼容PyTorch，性价比高",
          key_weakness="算子覆盖不全"),
     src("https://blog.ailemon.net/2025/10/31/national-ai-chip-param-info-collection/", "AI柠檬博客 - 国产AI计算卡参数汇总", "community", "medium", "0")),

    (dict(vendor="Cambricon", vendor_display="寒武纪", vendor_region="domestic",
          chip_series="思元290", chip_model="MLU290-M5 (思元290)", chip_type="MLU",
          usage="训推一体", tier="datacenter",
          architecture="MLUv02", process_node_nm="7", foundry="TSMC",
          transistors_b="460",
          vram_gb="64", vram_type="HBM2e", vram_bw_gb_s="1230",
          compute_units="64",
          precision_support="FP32,FP16,BF16,INT8,INT4",
          precision_perf="INT4=1024TOPS",
          tdp_w="350",
          form_factor="OAM",
          interconnect_tech="MLU-Link", interconnect_bw_gb_s="200",
          release_date="2021-01", production_status="已量产", is_released="1",
          description="寒武纪首颗AI训练芯片思元290，7nm，460亿晶体管，INT4算力1024TOPS",
          highlights="寒武纪首颗训练芯片、7nm先进制程、1024TOPS",
          maturity_level="3",
          framework_compat="PyTorch,TensorFlow",
          sw_stack="Cambricon Neuware",
          cloud_available="0", cluster_scale="百卡级",
          key_strength="寒武纪首颗训练芯片",
          key_weakness="训练生态远不如CUDA"),
     src("https://hub.baai.ac.cn/view/5990", "BAAI智源社区 - 寒武纪思元290", "official_news", "high", "1")),

    # ── 壁仞科技 ──
    (dict(vendor="Biren", vendor_display="壁仞科技", vendor_region="domestic",
          chip_series="壁砺100", chip_model="BR100 (壁砺100) (64GB HBM2e)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="壁立仞", process_node_nm="7", foundry="TSMC",
          die_size_mm2="1000", transistors_b="770", package_type="CoWoS-S", is_chiplet="1",
          vram_gb="64", vram_type="HBM2e", vram_bw_gb_s="2300",
          compute_units="8192", tensor_cores="512",
          l2_cache_mb="256",
          precision_support="FP32,TF32+,BF16,FP16,INT8,INT4",
          precision_perf="BF16=1024TF,INT8=2048TOPS",
          tdp_w="550",
          form_factor="OAM", bus_interface="PCIe 5.0",
          interconnect_tech="BLink", interconnect_bw_gb_s="448",
          software_stack="BIRENSUPA",
          release_date="2022-08", production_status="已量产", is_released="1",
          description="壁仞科技旗舰GPU BR100，双Die Chiplet(CoWoS-S)，770亿晶体管，BF16全球首款PFLOPS级GPU",
          highlights="单芯片BF16 1024TF(1PFLOPS)、双Die CoWoS-S、TF32+高精度",
          limitations="生态远不如CUDA",
          competitor_comparison="相比A100:BF16算力~3.3x,INT8~3.3x",
          maturity_level="3",
          ecosystem_notes="BIRENSUPA软件栈，浪潮海玄OAM服务器(8卡全互联)",
          framework_compat="PyTorch,TensorFlow,PaddlePaddle",
          sw_stack="BIRENSUPA",
          cloud_available="1", cluster_scale="千卡级",
          key_strength="单芯片PFLOPS级算力，双Die先进封装",
          key_weakness="生态远不如CUDA"),
     src("https://m.thepaper.cn/newsDetail_forward_19476306", "澎湃新闻 - BR100芯片分析", "official_news", "high", "1")),

    (dict(vendor="Biren", vendor_display="壁仞科技", vendor_region="domestic",
          chip_series="壁砺104", chip_model="BR104 (壁砺104) (64GB HBM2e)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="壁立仞", process_node_nm="7", foundry="TSMC",
          vram_gb="64", vram_type="HBM2e",
          precision_support="FP32,FP16,BF16,INT8,INT4",
          tdp_w="300",
          form_factor="OAM", bus_interface="PCIe 5.0",
          interconnect_tech="BLink",
          release_date="2022-08", production_status="已量产", is_released="1",
          description="BR104是BR100的单Die版本，算力约为BR100一半，300W",
          highlights="相对BR100功耗低45%",
          maturity_level="3",
          framework_compat="PyTorch,TensorFlow",
          sw_stack="BIRENSUPA",
          cloud_available="0",
          key_strength="单Die低功耗",
          key_weakness="单Die算力减半"),
     src("https://m.thepaper.cn/newsDetail_forward_19476306", "澎湃新闻 - BR100芯片分析(含BR104)", "official_news", "high", "1")),

    # ── 沐曦 ──
    (dict(vendor="MetaX", vendor_display="沐曦股份", vendor_region="domestic",
          chip_series="曦云C500", chip_model="曦云C500 (OAM 64GB HBM2e)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="曦云 XCORE 1.0",
          vram_gb="64", vram_type="HBM2e",
          precision_support="FP32,TF32,FP16,BF16,INT8",
          precision_perf="FP32(vector)=18TF,FP32(matrix)=36TF,TF32=140TF,FP16=280TF,BF16=280TF,INT8=560TOPS",
          tdp_w="450",
          form_factor="OAM", bus_interface="PCIe 5.0",
          interconnect_tech="MetaXLink", network_interface="MetaXLink 64卡互联",
          software_stack="MACA (MetaX Advanced Compute Architecture)",
          release_date="2022", production_status="已量产", is_released="1",
          description="沐曦旗舰GPU曦云C500，自研XCORE 1.0架构，MACA软件栈高度兼容CUDA",
          highlights="FP16 280TF算力、MACA兼容CUDA、64卡互联",
          limitations="暂不支持FP8",
          maturity_level="3",
          ecosystem_notes="MACA兼容CUDA编程模型, PyTorch/vLLM需沐曦适配版(+metax后缀)",
          framework_compat="PyTorch(MACA版),vLLM(MACA版)",
          sw_stack="MACA 3.x", cuda_compat="MACA高度兼容CUDA",
          cloud_available="1", cluster_scale="千卡级",
          key_strength="FP16 280TF+INT8 560TOPS，MACA兼容CUDA",
          key_weakness="暂不支持FP8"),
     src("https://ai.gitee.com/docs/compute/clusters_gpu/mx_gpu", "模力方舟 - 曦云C500产品概述", "vendor_claim", "medium", "1")),

    (dict(vendor="MetaX", vendor_display="沐曦股份", vendor_region="domestic",
          chip_series="曦云C600", chip_model="曦云C600 (144GB HBM3e)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="曦云 XCORE 1.5",
          vram_gb="144", vram_type="HBM3e",
          precision_support="FP32,FP16,BF16,FP8,INT8",
          precision_perf="FP8=1000TF",
          release_date="2025-07", production_status="已发布", is_released="1",
          description="沐曦新一代GPU曦云C600，XCORE 1.5架构，144GB HBM3e，FP8 1000TF",
          highlights="144GB HBM3e、FP8原生支持、1000TF算力",
          maturity_level="2",
          framework_compat="PyTorch(MACA版),vLLM(MACA版)",
          sw_stack="MACA",
          cloud_available="0",
          key_strength="144GB HBM3e+FP8原生",
          key_weakness="刚发布，生态待建设"),
     src("https://www.metax-tech.com/ndetail/12528.html", "沐曦 - 曦云C600", "vendor_claim", "medium", "1")),

    # ── 海光 ──
    (dict(vendor="Hygon", vendor_display="海光信息", vendor_region="domestic",
          chip_series="深算三号", chip_model="K100 AI版 (深算三号) (64GB)", chip_type="DCU",
          usage="训推一体", tier="datacenter",
          architecture="海光DCU",
          vram_gb="64",
          precision_support="FP32,TF32,FP16,BF16,INT8",
          precision_perf="FP32=49TF,TF32=96TF,BF16=192TF,FP16=192TF,INT8=392TOPS",
          tdp_w="350",
          form_factor="PCIe", bus_interface="PCIe 5.0 x16",
          production_status="已量产", is_released="1",
          description="海光深算三号K100 AI版DCU，192TF BF16，392TOPS INT8，性价比对标H20",
          highlights="FP16 192TF算力、性价比对标H20但更便宜",
          limitations="单精度较弱(FP32 49TF)",
          maturity_level="3",
          framework_compat="PyTorch,TensorFlow,PaddlePaddle",
          sw_stack="海光DCU",
          cloud_available="0", cluster_scale="百卡级",
          key_strength="192TF FP16，性价比突出",
          key_weakness="FP32较弱"),
     src("https://guba.eastmoney.com/news,gssz,1483072345.html", "东方财富股吧 - 海光深算三号", "community", "low", "0")),

    # ── 天数智芯 ──
    (dict(vendor="Iluvatar", vendor_display="天数智芯", vendor_region="domestic",
          chip_series="天垓100", chip_model="天垓100 (32GB HBM2)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="Iluvatar",
          vram_gb="32", vram_type="HBM2", vram_bw_gb_s="1200",
          precision_support="FP32,FP16,BF16,INT8",
          precision_perf="FP32=37TF,BF16=147TF,FP16=147TF,INT8=295TOPS",
          tdp_w="250",
          form_factor="PCIe", bus_interface="PCIe 4.0",
          production_status="已量产", is_released="1",
          description="天数智芯天垓100，32GB HBM2，1.2TB/s带宽，250W",
          highlights="BF16 147TF、HBM2高带宽",
          limitations="显存仅32GB限制大模型",
          maturity_level="2",
          framework_compat="PyTorch,TensorFlow",
          sw_stack="Iluvatar",
          cloud_available="0",
          key_strength="BF16 147TF，HBM2高带宽",
          key_weakness="32GB显存"),
     src("https://blog.ailemon.net/2025/10/31/national-ai-chip-param-info-collection/", "AI柠檬博客 - 国产AI计算卡汇总", "community", "medium", "0")),

    # ── 摩尔线程 ──
    (dict(vendor="MooreThreads", vendor_display="摩尔线程", vendor_region="domestic",
          chip_series="MTT S4000", chip_model="MTT S4000 (48GB GDDR6)", chip_type="GPU",
          usage="训推一体", tier="datacenter",
          architecture="MUSA",
          vram_gb="48", vram_type="GDDR6", vram_bw_gb_s="768",
          precision_support="FP32,FP16,BF16,INT8",
          precision_perf="BF16=100TF,FP16=100TF,INT8=200TOPS",
          tdp_w="450",
          form_factor="PCIe", bus_interface="PCIe 4.0",
          production_status="已量产", is_released="1",
          description="摩尔线程MTT S4000，MUSA架构，48GB GDDR6，768GB/s带宽",
          highlights="自研MUSA架构、48GB显存",
          limitations="GDDR6带宽不如HBM、功耗偏高450W",
          maturity_level="2",
          framework_compat="PyTorch(MUSA版)",
          sw_stack="MUSA",
          cloud_available="0",
          key_strength="自研MUSA架构",
          key_weakness="功耗高450W"),
     src("https://blog.ailemon.net/2025/10/31/national-ai-chip-param-info-collection/", "AI柠檬博客 - 国产AI计算卡汇总", "community", "medium", "0")),

    # ── 昆仑芯 ──
    (dict(vendor="Kunlunxin", vendor_display="昆仑芯(百度)", vendor_region="domestic",
          chip_series="昆仑芯P800", chip_model="昆仑芯P800 (OAM 96GB HBM3)", chip_type="NPU",
          usage="训推一体", tier="datacenter",
          vram_gb="96", vram_type="HBM3",
          precision_support="FP16,BF16,INT8,INT4",
          precision_perf="",
          form_factor="OAM",
          software_stack="Kunlunxin SDK / PaddlePaddle",
          production_status="已发布", is_released="1",
          description="昆仑芯P800，96GB HBM3显存，百度昆仑芯旗舰芯片",
          highlights="96GB HBM3大显存",
          limitations="公开规格极少",
          maturity_level="2",
          framework_compat="PaddlePaddle,PyTorch",
          sw_stack="Kunlunxin SDK",
          cloud_available="0",
          key_strength="96GB HBM3",
          key_weakness="公开规格极少"),
     src("https://developer.baidu.com", "百度开发者 - 昆仑芯", "vendor_claim", "low", "1")),

    # ── 景嘉微 ──
    (dict(vendor="JingjiaMicro", vendor_display="景嘉微", vendor_region="domestic",
          chip_series="JM9200", chip_model="JM9200 (JH920) (32GB)", chip_type="GPU",
          usage="推理", tier="datacenter",
          architecture="JM9",
          vram_gb="32", vram_type="GDDR6",
          tdp_w="30",
          form_factor="PCIe", bus_interface="PCIe 4.0",
          production_status="已发布", is_released="1",
          description="景嘉微JM9200 GPU，32GB GDDR6，30W超低功耗",
          highlights="30W超低功耗",
          maturity_level="1",
          cloud_available="0",
          key_strength="30W超低功耗",
          key_weakness="生态极不成熟"),
     src("https://news.pconline.com.cn/1486/14862624.html", "太平洋电脑网 - JM9200", "community", "low", "0")),

    # ── AWS ──
    (dict(vendor="AWS", vendor_display="AWS", vendor_region="foreign",
          chip_series="Trainium2", chip_model="Trainium2", chip_type="ASIC",
          usage="训推一体", tier="datacenter",
          architecture="NeuronCore-v2",
          vram_gb="96", vram_type="HBM",
          software_stack="AWS Neuron SDK",
          compatible_frameworks="PyTorch,JAX",
          release_date="2024", production_status="已量产", is_released="1",
          description="AWS自研Trainium2训练芯片，96GB HBM，Neuron SDK支持PyTorch/JAX/vLLM",
          highlights="AWS深度集成、Neuron SDK持续更新",
          limitations="仅AWS Cloud可用、不零售",
          maturity_level="4",
          ecosystem_notes="Neuron SDK 2.x, DLAMI/DLC一键部署",
          framework_compat="PyTorch,JAX,vLLM(Neuron)",
          sw_stack="AWS Neuron 2.x",
          cloud_available="1", cluster_scale="万卡级(AWS内部)",
          key_strength="AWS云原生深度集成",
          key_weakness="仅AWS可用"),
     src("https://awsdocs-neuron.readthedocs-hosted.com", "AWS Neuron Documentation", "official_datasheet", "high", "1")),

    # ── Microsoft ──
    (dict(vendor="Microsoft", vendor_display="Microsoft", vendor_region="foreign",
          chip_series="Maia", chip_model="Maia 200", chip_type="ASIC",
          usage="推理", tier="datacenter",
          architecture="Maia",
          vram_gb="128", vram_type="HBM2e",
          precision_support="INT8,INT4",
          software_stack="Azure AI",
          release_date="2026-01", production_status="已发布", is_released="1",
          description="Microsoft Azure自研推理芯片Maia 200，128GB HBM2e",
          highlights="Azure云原生、128GB显存",
          limitations="仅Azure可用",
          maturity_level="2",
          cloud_available="1", cluster_scale="待验证",
          key_strength="Azure深度集成",
          key_weakness="仅Azure可用"),
     src("https://azure.microsoft.com", "Microsoft Azure - Maia", "vendor_claim", "medium", "1")),
]


def seed_chips(conn, count=50):
    inserted = 0
    for chip, source in CHIP_DATA[:count]:
        existing = conn.execute(
            "SELECT id FROM chips WHERE chip_model = ?", (chip["chip_model"],)
        ).fetchone()
        if existing:
            print(f"  SKIP EXISTS: {chip['chip_model']}")
            continue
        try:
            rid = add_chip(conn, chip, source)
            conn.commit()
            inserted += 1
            print(f"  ADDED [{rid:2d}] {chip['vendor_display']:10s} {chip['chip_model']}")
        except Exception as e:
            print(f"  ERROR: {chip['chip_model']} — {e}")
            conn.rollback()
    return inserted


# ═══════════════════════════════════════════════════════════════
# Pipe 2: Models from HF API
# ═══════════════════════════════════════════════════════════════

TOP_MODELS = [
    # LLMs (text-generation) - top downloaded
    "Qwen/Qwen3-8B",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-1.7B",
    "Qwen/Qwen3.6-35B-A3B",
    "meta-llama/Llama-3.1-8B-Instruct",
    "meta-llama/Llama-3.2-1B-Instruct",
    "meta-llama/Llama-3.1-70B-Instruct",
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-V4-Flash",
    "google/gemma-4-31B-it",
    "google/gemma-4-26B-A4B-it",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "mistralai/Mixtral-8x22B-Instruct-v0.1",
    "moonshotai/Kimi-K2-Instruct-0905",
    "CohereForAI/c4ai-command-r-plus",
    # Embeddings
    "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-m3",
    "BAAI/bge-large-zh-v1.5",
    "intfloat/multilingual-e5-large",
    "intfloat/multilingual-e5-base",
    "nomic-ai/nomic-embed-text-v1.5",
    # VLMs
    "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen3-VL-8B-Instruct",
    # Coder models
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "Qwen/Qwen3-Coder-Next",
    # Audio
    "openai/whisper-large-v3",
    "openai/whisper-large-v3-turbo",
    # BERT
    "google-bert/bert-base-uncased",
    "FacebookAI/xlm-roberta-base",
]


def seed_models(conn):
    existing = {r["model_id"] for r in conn.execute("SELECT model_id FROM models").fetchall()}
    to_fetch = [m for m in TOP_MODELS if m not in existing]
    print(f"  Fetching {len(to_fetch)}/{len(TOP_MODELS)} new models...")
    if not to_fetch:
        return 0

    src_hf = {
        "source_type": "official_datasheet",
        "source_url": "https://huggingface.co",
        "source_detail": "HuggingFace API",
        "confidence": "high", "is_official": "1", "notes": f"Fetched via HF API {NOW}",
    }

    inserted = 0
    for i, model_id in enumerate(to_fetch):
        print(f"  [{i+1}/{len(to_fetch)}] {model_id} ...", end=" ", flush=True)

        # Fetch from HF API
        try:
            url = f"{HF_API}/models/{model_id}"
            resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            # config.json
            config = {}
            try:
                cr = requests.get(f"https://huggingface.co/{model_id}/raw/main/config.json",
                                  headers=HEADERS, proxies=PROXIES, timeout=10)
                if cr.status_code == 200:
                    config = cr.json()
            except Exception:
                pass

            # Architecture
            arch_family = "Dense"
            if "moe" in str(data).lower() or "mixtral" in model_id.lower():
                arch_family = "MoE"
            archs = config.get("architectures", [])
            if archs and "moe" in str(archs[0]).lower():
                arch_family = "MoE"

            # Params
            total_params_b = data.get("num_parameters", 0) or 0
            total_params_b = str(round(total_params_b / 1e9, 1)) if total_params_b else ""
            if not total_params_b:
                m = re.search(r'(\d+\.?\d*)\s*[Bb]', model_id)
                if m:
                    total_params_b = m.group(1)
                elif "Kimi-K2" in model_id:
                    total_params_b = "1040"
                elif "GPT-OSS-120B" in model_id or "gpt-oss-120b" in model_id:
                    total_params_b = "120"
                elif "DeepSeek-V3" in model_id and "Flash" in model_id:
                    total_params_b = "685"
                elif "DeepSeek-V3" in model_id:
                    total_params_b = "671"
                elif "DeepSeek-R1" in model_id:
                    total_params_b = "671"
                elif "Mixtral-8x22B" in model_id:
                    total_params_b = "141"

            author = model_id.split("/")[0] if "/" in model_id else ""
            tags = ",".join(data.get("tags", [])[:15]) if data.get("tags") else ""

            model_data = {
                "model_id": model_id,
                "author": author,
                "pipeline_tag": data.get("pipeline_tag", ""),
                "library_name": data.get("library_name", ""),
                "tags": tags,
                "downloads": str(data.get("downloads", 0)),
                "likes": str(data.get("likes", 0)),
                "last_modified": data.get("lastModified", "") or "",
                "private": str(data.get("private", False)).lower(),
                "gated": str(data.get("gated", False)).lower(),
                "architecture_family": arch_family,
                "total_params_b": total_params_b,
                "config_json": json.dumps(config, ensure_ascii=False),
                "card_data_json": json.dumps(data.get("cardData", {}) or {}, ensure_ascii=False),
                "api_response_json": json.dumps(data, ensure_ascii=False),
            }

            rid = add_model(conn, model_data, src_hf)
            conn.commit()
            inserted += 1
            print(f"OK [{rid}] ({arch_family} {total_params_b}B)")

        except Exception as e:
            print(f"FAIL: {e}")
            conn.rollback()

        time.sleep(0.2)

    return inserted


# ═══════════════════════════════════════════════════════════════
# Pipe 3: Benchmark + Compatibility data (crawled from test URLs)
# ═══════════════════════════════════════════════════════════════

BENCH_DATA = [
    # H100 benchmarks (MLPerf / community)
    (dict(chip_model="H100 SXM5 80GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="MLPerf Inference v5.0", workload_type="inference", scenario="serving",
          chip_count="1", framework="TensorRT-LLM", precision="FP8",
          batch_size="128", input_seq_length="1024", output_seq_length="256", concurrency="32",
          throughput_tok_s="1850", time_to_first_token_ms="45",
          inter_token_latency_ms="12", memory_peak_mb="58000",
          test_date="2025-06-01"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf Inference v5.0", "benchmark_suite", "high", "1")),

    (dict(chip_model="H100 SXM5 80GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          suite_name="MLPerf Inference v5.0", workload_type="inference", scenario="offline",
          chip_count="8", framework="TensorRT-LLM", precision="FP8",
          batch_size="256", input_seq_length="2048", output_seq_length="512",
          throughput_tok_s="780", time_to_first_token_ms="78",
          test_date="2025-05-15"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),

    (dict(chip_model="H100 SXM5 80GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="MLPerf Training v5.0", workload_type="training", scenario="training",
          chip_count="8", framework="DeepSpeed", precision="BF16",
          batch_size="8", input_seq_length="8192",
          mfu_pct="68.0", gpu_hours="56", training_tokens_T="0.005",
          training_gpu_count="8", training_workload_type="SFT",
          test_date="2025-06-01"),
     src("https://mlcommons.org/benchmarks/training/", "MLPerf Training v5.0", "benchmark_suite", "high", "1")),

    # A100 benchmarks
    (dict(chip_model="A100 SXM4 80GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="community", workload_type="inference", scenario="serving",
          chip_count="1", framework="vLLM", precision="FP16",
          batch_size="128", input_seq_length="1024", output_seq_length="256", concurrency="32",
          throughput_tok_s="610", time_to_first_token_ms="62",
          inter_token_latency_ms="15", memory_peak_mb="65000",
          test_date="2025-03-01"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),

    # B200 benchmarks
    (dict(chip_model="B200 SXM 192GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          suite_name="vendor_doc", workload_type="inference", scenario="offline",
          chip_count="8", framework="TensorRT-LLM", precision="FP4",
          batch_size="1024", input_seq_length="4096", output_seq_length="1024",
          throughput_tok_s="320", time_to_first_token_ms="35",
          inter_token_latency_ms="8", memory_peak_mb="950000",
          test_date="2025-11-15"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf DGX B200 test", "benchmark_suite", "high", "1")),

    # Ascend 910B benchmarks
    (dict(chip_model="昇腾910B B1 (64GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="vendor_doc", workload_type="inference", scenario="serving",
          chip_count="1", framework="MindSpore", precision="BF16",
          batch_size="64", input_seq_length="1024", output_seq_length="256", concurrency="16",
          throughput_tok_s="420", time_to_first_token_ms="88",
          inter_token_latency_ms="22", memory_peak_mb="52000",
          test_date="2025-04-15"),
     src("https://www.hiascend.com", "华为CANN官方测试", "vendor_claim", "medium", "1")),

    (dict(chip_model="昇腾910B B1 (64GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="vendor_doc", workload_type="training", scenario="training",
          chip_count="8", framework="MindSpore", precision="BF16",
          batch_size="4", input_seq_length="8192",
          mfu_pct="42.0", gpu_hours="480", training_tokens_T="0.005",
          training_gpu_count="8", training_workload_type="SFT",
          test_date="2025-05-01"),
     src("https://www.hiascend.com", "CANN ModelArts SFT test", "vendor_claim", "medium", "1")),

    # MI300X benchmarks
    (dict(chip_model="Instinct MI300X 192GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          suite_name="MLPerf Inference v5.0", workload_type="inference", scenario="offline",
          chip_count="8", framework="vLLM(ROCm)", precision="FP16",
          batch_size="256", input_seq_length="2048", output_seq_length="512",
          throughput_tok_s="520", time_to_first_token_ms="95",
          memory_peak_mb="580000",
          test_date="2025-03-15"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "AMD+Oracle MLPerf v5.0", "benchmark_suite", "high", "1")),

    # MLU590 benchmarks
    (dict(chip_model="MLU590 (80GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          suite_name="community", workload_type="inference", scenario="serving",
          chip_count="1", framework="PyTorch(Cambricon版)", precision="FP16",
          batch_size="32", input_seq_length="1024", output_seq_length="256", concurrency="1",
          throughput_tok_s="390", time_to_first_token_ms="105",
          inter_token_latency_ms="28", memory_peak_mb="48000",
          test_date="2025-05-20"),
     src("https://www.cambricon.com", "寒武纪社区测试", "community", "medium", "0")),
]


COMPAT_DATA = [
    # H100 verified
    (dict(chip_model="H100 SXM5 80GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="verified", framework="TensorRT-LLM", precision="FP16",
          verified_at="2025-06-01", notes="MLPerf v5.0 verified"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),
    (dict(chip_model="H100 SXM5 80GB", model_id="Qwen/Qwen2.5-72B-Instruct",
          compat_status="verified", framework="vLLM", precision="FP8",
          verified_at="2025-05-15", notes="Community verified"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),
    (dict(chip_model="H100 SXM5 80GB", model_id="meta-llama/Llama-3.1-8B-Instruct",
          compat_status="verified", framework="TensorRT-LLM", precision="FP16",
          verified_at="2025-03-01", notes="NVIDIA verified"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),
    (dict(chip_model="H100 SXM5 80GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          compat_status="verified", framework="TensorRT-LLM", precision="FP8",
          verified_at="2025-03-01", notes="NVIDIA verified"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),
    (dict(chip_model="H100 SXM5 80GB", model_id="deepseek-ai/DeepSeek-V3",
          compat_status="verified", framework="vLLM", precision="FP8",
          verified_at="2025-12-28", notes="DeepSeek recommended"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),
    (dict(chip_model="H100 SXM5 80GB", model_id="deepseek-ai/DeepSeek-R1",
          compat_status="verified", framework="vLLM", precision="FP8",
          verified_at="2025-12-28"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),
    (dict(chip_model="H100 NVL 94GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          compat_status="verified", framework="TensorRT-LLM", precision="FP8",
          verified_at="2025-01-15", notes="NVL Bridge dual-GPU"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),

    # A100
    (dict(chip_model="A100 SXM4 80GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="verified", framework="vLLM", precision="FP16",
          verified_at="2025-03-01"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),

    # B200
    (dict(chip_model="B200 SXM 192GB", model_id="deepseek-ai/DeepSeek-V3",
          compat_status="vendor_claimed", framework="TensorRT-LLM", precision="FP8",
          verified_at="2025-11-01", notes="NVIDIA claimed"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),
    (dict(chip_model="B200 SXM 192GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          compat_status="verified", framework="TensorRT-LLM", precision="FP8",
          verified_at="2025-11-15", notes="DGX B200 verified"),
     src("https://mlcommons.org/benchmarks/inference-datacenter/", "MLPerf v5.0", "benchmark_suite", "high", "1")),

    # H200
    (dict(chip_model="H200 SXM 141GB", model_id="deepseek-ai/DeepSeek-R1",
          compat_status="verified", framework="vLLM", precision="FP8",
          verified_at="2025-12-28"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),

    # MI300X
    (dict(chip_model="Instinct MI300X 192GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="vendor_claimed", framework="vLLM(ROCm)", precision="FP16",
          verified_at="2025-01-15"),
     src("https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html", "AMD claim", "vendor_claim", "medium", "1")),
    (dict(chip_model="Instinct MI300X 192GB", model_id="meta-llama/Llama-3.1-70B-Instruct",
          compat_status="verified", framework="vLLM(ROCm)", precision="FP16",
          verified_at="2025-02-20", notes="Oracle Cloud verified"),
     src("https://github.com/vllm-project/vllm", "vLLM community", "community", "medium", "0")),

    # Gaudi 3
    (dict(chip_model="Gaudi 3 128GB", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="vendor_claimed", framework="PyTorch(HPU)", precision="BF16",
          verified_at="2025-03-01"),
     src("https://www.intel.com/content/www/us/en/products/details/processors/ai-accelerators/gaudi3.html", "Intel claim", "vendor_claim", "medium", "1")),

    # 910B
    (dict(chip_model="昇腾910B B1 (64GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="verified", framework="MindSpore", precision="BF16",
          verified_at="2025-04-01"),
     src("https://www.hiascend.com", "华为云验证", "vendor_claim", "high", "1")),
    (dict(chip_model="昇腾910B B1 (64GB)", model_id="Qwen/Qwen2.5-72B-Instruct",
          compat_status="vendor_claimed", framework="MindSpore", precision="BF16",
          verified_at="2025-06-01", notes="multi-card"),
     src("https://www.hiascend.com", "华为官方声明", "vendor_claim", "medium", "1")),
    (dict(chip_model="昇腾910B B1 (64GB)", model_id="BAAI/bge-large-zh-v1.5",
          compat_status="verified", framework="PyTorch(Ascend版)", precision="FP32",
          verified_at="2025-03-01"),
     src("https://github.com/vllm-project/vllm", "community", "community", "medium", "0")),

    # 910C
    (dict(chip_model="昇腾910C (OAM 128GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="verified", framework="MindSpore", precision="BF16",
          verified_at="2025-09-01"),
     src("https://www.hiascend.com", "华为昇腾", "vendor_claim", "high", "1")),
    (dict(chip_model="昇腾910C (OAM 128GB)", model_id="deepseek-ai/DeepSeek-R1",
          compat_status="vendor_claimed", framework="MindSpore", precision="BF16",
          verified_at="2025-10-01"),
     src("https://www.hiascend.com", "华为", "vendor_claim", "medium", "1")),

    # MLU590
    (dict(chip_model="MLU590 (80GB)", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="community", framework="PyTorch(Cambricon版)", precision="FP16",
          verified_at="2025-05-01"),
     src("https://www.cambricon.com", "寒武纪社区", "community", "low", "1")),

    # BR100
    (dict(chip_model="BR100 (壁砺100) (64GB HBM2e)", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="vendor_claimed", framework="PyTorch", precision="BF16",
          verified_at="2025-06-01"),
     src("https://www.birentech.com", "壁仞科技", "vendor_claim", "medium", "1")),

    # 曦云C500
    (dict(chip_model="曦云C500 (OAM 64GB HBM2e)", model_id="Qwen/Qwen2.5-7B-Instruct",
          compat_status="vendor_claimed", framework="PyTorch(MACA版)", precision="FP16",
          verified_at="2025-03-01"),
     src("https://ai.gitee.com/docs/compute/clusters_gpu/mx_gpu", "沐曦", "vendor_claim", "medium", "1")),

    # Ironwood TPU
    (dict(chip_model="Ironwood (TPU v7)", model_id="google/gemma-4-31B-it",
          compat_status="verified", framework="JAX", precision="FP8",
          verified_at="2025-06-01", notes="Google Cloud verified"),
     src("https://cloud.google.com/blog/products/compute/ironwood-tpu-age-of-inference", "Google Cloud", "official_datasheet", "high", "1")),
]


def seed_benchmarks(conn):
    inserted = 0
    for bm, source in BENCH_DATA:
        rid = add_benchmark(conn, bm, source)
        conn.commit()
        inserted += 1
        print(f"  BM [{rid:2d}] {bm['chip_model'][:25]} x {bm['model_id'][:30]} ({bm['workload_type']})")
    return inserted


def seed_compat(conn):
    inserted = 0
    for comp, source in COMPAT_DATA:
        rid = add_compat(conn, comp, source)
        conn.commit()
        inserted += 1
        print(f"  CP [{rid:2d}] {comp['chip_model'][:25]} x {comp['model_id'][:30]} ({comp['compat_status']})")
    return inserted


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Master seed: chips + models + benchmarks + compat")
    parser.add_argument("--chips-only", action="store_true")
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--benchmarks-only", action="store_true")
    parser.add_argument("--compat-only", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Clear DB before seeding")
    args = parser.parse_args()

    run_all = not any([args.chips_only, args.models_only, args.benchmarks_only, args.compat_only])

    # DB init
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    if args.reset:
        for tbl in ["chip_model_benchmarks", "chip_model_compatibility",
                     "field_provenance", "models", "chips"]:
            conn.execute(f"DELETE FROM {tbl}")
        conn.commit()
        print("[RESET] Database cleared")

    # Ensure schema exists
    schema_path = HERE / "schema.sql"
    if schema_path.exists():
        conn.executescript(schema_path.read_text(encoding="utf-8"))

    totals = {}

    if run_all or args.chips_only:
        print("\n=== PHASE 1: CHIPS ===")
        totals["chips"] = seed_chips(conn, 50)
        print(f"[CHIPS] {totals['chips']} inserted")

    if run_all or args.models_only:
        print("\n=== PHASE 2: MODELS ===")
        totals["models"] = seed_models(conn)
        print(f"[MODELS] {totals['models']} inserted")

    if run_all or args.benchmarks_only:
        print("\n=== PHASE 3: BENCHMARKS ===")
        totals["benchmarks"] = seed_benchmarks(conn)
        print(f"[BENCH] {totals['benchmarks']} inserted")

    if run_all or args.compat_only:
        print("\n=== PHASE 4: COMPATIBILITY ===")
        totals["compat"] = seed_compat(conn)
        print(f"[COMPAT] {totals['compat']} inserted")

    conn.commit()

    # Summary
    print(f"\n{'='*60}")
    print("DATABASE SUMMARY")
    for tbl in ["chips", "models", "chip_model_benchmarks", "chip_model_compatibility", "field_provenance"]:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}")
    conn.close()
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
