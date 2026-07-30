#!/usr/bin/env python3
"""
Fill missing card_data_json for 9 models via HF API.
"""
import json
import sqlite3
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chip_model.database import update_model_fields, get_db_path

PROXIES = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}

MODELS = [
    'deepseek-ai/DeepSeek-V3',
    'deepseek-ai/DeepSeek-R1',
    'Qwen/Qwen3-235B-A22B',
    'mistralai/Mixtral-8x22B-v0.1',
    'mistralai/Mistral-7B-v0.3',
    'mistralai/Mixtral-8x7B-v0.1',
    'microsoft/phi-4',
    '01-ai/Yi-1.5-34B',
    'zai-org/glm-4-9b-chat',
]


def open_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def main():
    db_path = str(get_db_path())
    db = open_db(db_path)

    for mid in MODELS:
        cur = db.execute("SELECT id FROM models WHERE model_id = ?", [mid]).fetchone()
        if cur is None:
            print(f'SKIP {mid}: not in DB')
            continue
        row_id = cur[0]

        r = requests.get(f'https://huggingface.co/api/models/{mid}', proxies=PROXIES, timeout=30)
        data = r.json()
        card = data.get('cardData')
        if not card:
            print(f'SKIP {mid}: no cardData')
            continue

        card_str = json.dumps(card, indent=2, ensure_ascii=False)
        fields = {'card_data_json': card_str}
        src = {
            "source_type": "official_datasheet",
            "source_url": f'https://huggingface.co/api/models/{mid}',
            "source_detail": "cardData from HuggingFace API",
            "confidence": "high",
            "is_official": True,
            "notes": f"HF API cardData fetch for {mid}"
        }

        update_model_fields(db, row_id, fields, src)
        db.commit()
        print(f'OK {mid}: cardData={len(card_str)} chars')

    db.close()
    print('Done')


if __name__ == '__main__':
    main()
