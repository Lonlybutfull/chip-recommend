"""Incremental HuggingFace API synchronizer for models already in the catalog."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from chip_model.database import get_db, update_model_fields


def _architecture(config: dict[str, Any], model_id: str, api: dict[str, Any]) -> str:
    text = json.dumps({"config": config, "api": api}, ensure_ascii=False).casefold()
    return "MoE" if "moe" in text or "mixtral" in model_id.casefold() else "Dense"


def _params_b(api: dict[str, Any], model_id: str) -> str:
    value = api.get("num_parameters") or (api.get("safetensors") or {}).get("total")
    if value:
        try:
            return str(round(float(value) / 1e9, 3)).rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            pass
    match = re.search(r"(?:^|[-_])([0-9]+(?:\.[0-9]+)?)B(?:[-_]|$)", model_id, re.I)
    return match.group(1) if match else ""


def sync_known_models(
    *,
    db_path: str | Path,
    limit: int = 20,
    session: requests.Session | None = None,
    proxy: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    client = session or requests.Session()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "AISHPerf-ModelSync/1.0", "Accept": "application/json"}
    with get_db(db_path, readonly=True) as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM models WHERE COALESCE(model_id,'')!='' "
                "ORDER BY COALESCE(updated_at,'') ASC,id LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        ]
    counts = {
        "selected": len(rows), "updated": 0, "unchanged": 0,
        "failed": 0, "deferred": 0,
    }
    results = []
    for index, model in enumerate(rows):
        model_id = str(model["model_id"])
        try:
            response = client.get(
                f"https://huggingface.co/api/models/{quote(model_id, safe='/')}",
                headers=headers,
                proxies=proxies,
                timeout=timeout,
            )
            response.raise_for_status()
            api = response.json()
            if not isinstance(api, dict):
                raise ValueError("HuggingFace API 返回值不是对象")
            last_modified = str(api.get("lastModified") or "")
            needs_config = not model.get("config_json") or last_modified != str(model.get("last_modified") or "")
            config: dict[str, Any] = {}
            if model.get("config_json"):
                try:
                    config = json.loads(model["config_json"])
                except json.JSONDecodeError:
                    config = {}
            if needs_config:
                config_response = client.get(
                    f"https://huggingface.co/{quote(model_id, safe='/')}/raw/main/config.json",
                    headers=headers,
                    proxies=proxies,
                    timeout=timeout,
                )
                if config_response.status_code == 200:
                    config_value = config_response.json()
                    if isinstance(config_value, dict):
                        config = config_value
            fields = {
                "author": str(api.get("author") or (model_id.split("/", 1)[0] if "/" in model_id else "")),
                "pipeline_tag": str(api.get("pipeline_tag") or ""),
                "library_name": str(api.get("library_name") or ""),
                "tags": ",".join(str(value) for value in (api.get("tags") or [])[:50]),
                "downloads": str(api.get("downloads") or 0),
                "likes": str(api.get("likes") or 0),
                "last_modified": last_modified,
                "private": str(bool(api.get("private", False))).lower(),
                "gated": str(api.get("gated", False)).lower(),
                "architecture_family": _architecture(config, model_id, api),
                "total_params_b": _params_b(api, model_id),
                "config_json": json.dumps(config, ensure_ascii=False, separators=(",", ":")),
                "card_data_json": json.dumps(api.get("cardData") or {}, ensure_ascii=False, separators=(",", ":")),
                "api_response_json": json.dumps(api, ensure_ascii=False, separators=(",", ":")),
            }
            changed = {
                key: value for key, value in fields.items()
                if value != "" and str(model.get(key) or "") != value
            }
            if changed:
                source = {
                    "source_type": "official_api",
                    "source_url": f"https://huggingface.co/api/models/{model_id}",
                    "source_detail": "HuggingFace model API incremental sync",
                    "confidence": "high",
                    "is_official": "1",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                with get_db(db_path) as db:
                    update_model_fields(db, int(model["id"]), changed, source)
                    db.commit()
                counts["updated"] += 1
                outcome = "updated"
            else:
                counts["unchanged"] += 1
                outcome = "unchanged"
            results.append({"id": model["id"], "model_id": model_id, "outcome": outcome, "fields": sorted(changed)})
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            counts["failed"] += 1
            results.append({"id": model["id"], "model_id": model_id, "outcome": "failed", "error": str(exc)[:1000]})
            remaining = rows[index + 1 :]
            counts["deferred"] += len(remaining)
            results.extend(
                {
                    "id": item["id"],
                    "model_id": str(item["model_id"]),
                    "outcome": "deferred",
                    "error": "HuggingFace 当前不可达，本轮剩余模型已快速延期。",
                }
                for item in remaining
            )
            break
        except Exception as exc:
            counts["failed"] += 1
            results.append({"id": model["id"], "model_id": model_id, "outcome": "failed", "error": str(exc)[:1000]})
    return {"counts": counts, "results": results}
