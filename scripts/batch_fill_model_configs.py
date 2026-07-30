#!/usr/bin/env python3
"""
Batch fetch config.json and cardData for top 30 models.
Directly uses the database module for field-level provenance tracking.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

# ── Setup ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chip_model.database import update_model_fields, get_db_path

PROXIES = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}

# Priority-sorted target model IDs (most important first)
TARGET_MODELS = [
    # Tier 1: Largest LLMs first
    ('deepseek-ai/DeepSeek-V3', 'DeepSeek-V3 MoE 671B'),
    ('deepseek-ai/DeepSeek-R1', 'DeepSeek-R1 Reasoning 671B'),
    ('meta-llama/Llama-3.1-405B', 'Llama 3.1 405B'),
    ('Qwen/Qwen3-235B-A22B', 'Qwen3 235B MoE'),
    ('meta-llama/Llama-3.1-70B', 'Llama 3.1 70B'),
    ('meta-llama/Llama-3.3-70B-Instruct', 'Llama 3.3 70B'),
    ('Qwen/Qwen2.5-72B-Instruct', 'Qwen2.5 72B'),
    ('meta-llama/Llama-4-Maverick-17B-128E-Instruct', 'Llama 4 Maverick MoE'),
    ('meta-llama/Llama-4-Scout-17B-16E-Instruct', 'Llama 4 Scout MoE'),
    ('mistralai/Mixtral-8x22B-v0.1', 'Mixtral 8x22B MoE'),
    # Tier 2: Medium-large LLMs
    ('Qwen/Qwen3-8B', 'Qwen3 8B'),
    ('Qwen/Qwen3-14B', 'Qwen3 14B'),
    ('Qwen/Qwen3-32B', 'Qwen3 32B'),
    ('Qwen/Qwen2.5-7B-Instruct', 'Qwen2.5 7B'),
    ('Qwen/Qwen2.5-14B-Instruct', 'Qwen2.5 14B'),
    ('Qwen/Qwen2.5-32B-Instruct', 'Qwen2.5 32B'),
    ('Qwen/Qwen2.5-Coder-7B', 'Qwen2.5 Coder 7B'),
    ('Qwen/Qwen2.5-Coder-32B', 'Qwen2.5 Coder 32B'),
    ('Qwen/QwQ-32B', 'QwQ 32B Reasoning'),
    ('meta-llama/Llama-3.1-8B', 'Llama 3.1 8B'),
    ('meta-llama/Llama-3.2-1B', 'Llama 3.2 1B'),
    ('meta-llama/Llama-3.2-3B', 'Llama 3.2 3B'),
    ('mistralai/Mistral-7B-v0.3', 'Mistral 7B v0.3'),
    ('mistralai/Mixtral-8x7B-v0.1', 'Mixtral 8x7B MoE'),
    ('google/gemma-2-27b', 'Gemma 2 27B'),
    ('google/gemma-2-9b', 'Gemma 2 9B'),
    ('google/gemma-2-2b', 'Gemma 2 2B'),
    ('microsoft/phi-4', 'Phi-4 14B'),
    ('01-ai/Yi-1.5-34B', 'Yi 1.5 34B'),
    ('zai-org/glm-4-9b-chat', 'GLM-4 9B Chat'),
]


def fetch_config_json(model_id: str) -> dict | None:
    """Fetch model config.json from HuggingFace, trying multiple common branch names."""
    branches = ['main', 'master']
    for branch in branches:
        url = f'https://huggingface.co/{model_id}/raw/{branch}/config.json'
        try:
            r = requests.get(url, proxies=PROXIES, timeout=30)
            if r.status_code == 200:
                return r.json()
            elif r.status_code != 404:
                print(f'    [HTTP {r.status_code}] {url}')
        except Exception as e:
            print(f'    [ERROR] {url}: {e}')
    print(f'    config.json not found on any branch')
    return None


def extract_card_data(db_path: str, model_id: str) -> str | None:
    """Extract cardData from existing api_response_json."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT api_response_json FROM models WHERE model_id = ?",
            [model_id]
        ).fetchone()
        if cur is None or not cur[0]:
            return None
        data = json.loads(cur[0])
        card = data.get('cardData')
        if card:
            return json.dumps(card, indent=2, ensure_ascii=False)
        return None
    except Exception as e:
        print(f'  [WARN] cardData extraction failed: {e}')
        return None
    finally:
        conn.close()


def make_source(model_id: str) -> dict:
    """Build provenance source dict for both config_json and card_data_json."""
    return {
        "source_type": "official_datasheet",
        "source_url": f'https://huggingface.co/{model_id}/raw/main/config.json',
        "source_detail": f"Model config.json and cardData from HuggingFace",
        "confidence": "high",
        "is_official": True,
        "notes": f"HF config.json + cardData fetch for {model_id}"
    }


def open_db(db_path: str):
    """Open a writable database connection with WAL + row factory."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def main():
    db_path = str(get_db_path())
    db = open_db(db_path)

    total = len(TARGET_MODELS)
    success_count = 0
    fail_count = 0

    for i, (model_id, label) in enumerate(TARGET_MODELS):
        print(f'[{i+1:2d}/{total}] {model_id} ({label})', flush=True)

        # Get DB row id
        cur = db.execute("SELECT id FROM models WHERE model_id = ?", [model_id]).fetchone()
        if cur is None:
            print(f'  SKIP: model not in DB')
            fail_count += 1
            continue
        row_id = cur[0]

        # 1. Fetch config.json from HF
        config = fetch_config_json(model_id)
        if config is None:
            print(f'  SKIP: config.json fetch failed')
            fail_count += 1
            continue

        config_json_str = json.dumps(config, indent=2, ensure_ascii=False)
        n_keys = len(config)
        print(f'  config.json: {len(config_json_str):,} chars, {n_keys} keys', end='')

        # 2. Extract cardData from existing api_response_json in DB
        card_data_str = extract_card_data(db_path, model_id)
        if card_data_str:
            print(f', cardData: {len(card_data_str):,} chars', end='')
        else:
            print(f', cardData: (none)', end='')
        print()

        # 3. Build fields dict
        fields = {'config_json': config_json_str}
        if card_data_str:
            fields['card_data_json'] = card_data_str

        # 4. Update using database module (handles provenance automatically)
        src = make_source(model_id)
        try:
            update_model_fields(db, row_id, fields, src)
            db.commit()
            print(f'  => OK: {list(fields.keys())}')
            success_count += 1
        except Exception as e:
            db.rollback()
            print(f'  => FAIL: {e}')
            fail_count += 1

        # Small delay between requests
        time.sleep(0.3)

    db.close()
    print(f'\n{"="*60}')
    print(f'Done: {success_count} success, {fail_count} failed, {total} total')


if __name__ == '__main__':
    main()
