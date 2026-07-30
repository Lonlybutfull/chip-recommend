#!/usr/bin/env python3
"""
Fill config_json and card_data_json for gated models via HF API.
Uses the /api/models/{id}?config=true endpoint which returns config even for gated repos.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chip_model.database import update_model_fields, get_db_path

PROXIES = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}

GATED_MODELS = [
    ('meta-llama/Llama-3.1-405B', 'Llama 3.1 405B'),
    ('meta-llama/Llama-3.1-70B', 'Llama 3.1 70B'),
    ('meta-llama/Llama-3.3-70B-Instruct', 'Llama 3.3 70B'),
    ('meta-llama/Llama-4-Maverick-17B-128E-Instruct', 'Llama 4 Maverick MoE'),
    ('meta-llama/Llama-4-Scout-17B-16E-Instruct', 'Llama 4 Scout MoE'),
    ('meta-llama/Llama-3.1-8B', 'Llama 3.1 8B'),
    ('meta-llama/Llama-3.2-1B', 'Llama 3.2 1B'),
    ('meta-llama/Llama-3.2-3B', 'Llama 3.2 3B'),
    ('google/gemma-2-27b', 'Gemma 2 27B'),
    ('google/gemma-2-9b', 'Gemma 2 9B'),
    ('google/gemma-2-2b', 'Gemma 2 2B'),
]


def fetch_via_api(model_id: str) -> tuple[dict | None, dict | None]:
    """Fetch config and cardData from HF API endpoint."""
    url = f'https://huggingface.co/api/models/{model_id}'
    try:
        r = requests.get(url, params={'config': 'true'}, proxies=PROXIES, timeout=30)
        r.raise_for_status()
        data = r.json()
        config = data.get('config', {})
        card = data.get('cardData')
        # config may contain tokenizer_config which has different type than model config
        # Strip tokenizer_config if present in config dict
        if isinstance(config, dict) and 'tokenizer_config' in config:
            config = {k: v for k, v in config.items() if k != 'tokenizer_config'}
        if not config:
            config = None
        if isinstance(card, dict) and card:
            card_data = card
        else:
            card_data = None
        return config, card_data
    except Exception as e:
        print(f'  [ERROR] API fetch: {e}')
        return None, None


def open_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def make_source(model_id: str) -> dict:
    return {
        "source_type": "official_datasheet",
        "source_url": f'https://huggingface.co/api/models/{model_id}?config=true',
        "source_detail": "Model config/cardData from HuggingFace API (gated model)",
        "confidence": "high",
        "is_official": True,
        "notes": f"HF API fetch for gated model {model_id}"
    }


def main():
    db_path = str(get_db_path())
    db = open_db(db_path)

    total = len(GATED_MODELS)
    success = 0
    fail = 0

    for i, (model_id, label) in enumerate(GATED_MODELS):
        print(f'[{i+1:2d}/{total}] {model_id} ({label})', flush=True)

        cur = db.execute("SELECT id FROM models WHERE model_id = ?", [model_id]).fetchone()
        if cur is None:
            print(f'  SKIP: not in DB')
            fail += 1
            continue
        row_id = cur[0]

        config, card_data = fetch_via_api(model_id)
        if config is None:
            print(f'  SKIP: config not available via API')
            fail += 1
            continue

        config_str = json.dumps(config, indent=2, ensure_ascii=False)
        n_keys = len(config)
        print(f'  config: {len(config_str):,} chars, {n_keys} keys', end='')

        fields = {'config_json': config_str}

        if card_data:
            card_str = json.dumps(card_data, indent=2, ensure_ascii=False)
            fields['card_data_json'] = card_str
            print(f', cardData: {len(card_str):,} chars', end='')
        else:
            print(f', cardData: (none)', end='')
        print()

        src = make_source(model_id)
        try:
            update_model_fields(db, row_id, fields, src)
            db.commit()
            print(f'  => OK: {list(fields.keys())}')
            success += 1
        except Exception as e:
            db.rollback()
            print(f'  => FAIL: {e}')
            fail += 1

        time.sleep(0.3)

    db.close()
    print(f'\n{"="*60}')
    print(f'Done: {success} success, {fail} failed, {total} total')


if __name__ == '__main__':
    main()
