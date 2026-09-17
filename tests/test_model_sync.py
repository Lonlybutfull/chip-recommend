"""Tests for incremental HuggingFace model synchronization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import requests

from chip_model.database import init_db
from chip_model.pipeline.model_sync import sync_known_models


class JsonResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class JsonSession:
    def __init__(self, responses: list[JsonResponse]):
        self.responses = list(responses)
        self.calls = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class OfflineSession:
    def __init__(self):
        self.calls = 0

    def get(self, url: str, **kwargs):
        self.calls += 1
        raise requests.exceptions.ConnectionError("network unreachable")


def test_known_model_metadata_and_config_are_incrementally_updated(tmp_path: Path):
    db_path = tmp_path / "models.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO models (model_id,downloads,likes,last_modified,config_json,updated_at) "
            "VALUES ('Org/Test-8B','1','2','old','','2025-01-01T00:00:00Z')"
        )
        db.commit()
    session = JsonSession(
        [
            JsonResponse(
                {
                    "author": "Org", "pipeline_tag": "text-generation",
                    "library_name": "transformers", "tags": ["llm"],
                    "downloads": 100, "likes": 12, "lastModified": "2026-09-11T00:00:00Z",
                    "private": False, "gated": False, "num_parameters": 8_000_000_000,
                    "cardData": {"license": "apache-2.0"},
                }
            ),
            JsonResponse({"architectures": ["TestForCausalLM"], "hidden_size": 4096}),
        ]
    )

    result = sync_known_models(db_path=db_path, session=session, limit=1)

    assert result["counts"]["updated"] == 1
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT downloads,likes,total_params_b,architecture_family FROM models"
        ).fetchone()
        assert row == ("100", "12", "8", "Dense")
        fields = {
            item[0] for item in db.execute(
                "SELECT field_name FROM field_provenance WHERE table_name='models'"
            ).fetchall()
        }
        assert {"downloads", "likes", "total_params_b", "config_json"}.issubset(fields)
    assert session.calls[0][0].endswith("/api/models/Org/Test-8B")


def test_provider_outage_defers_remaining_models_after_first_network_failure(tmp_path: Path):
    db_path = tmp_path / "models-offline.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as db:
        db.executemany(
            "INSERT INTO models (model_id,updated_at) VALUES (?,?)",
            [
                ("Org/First-8B", "2025-01-01T00:00:00Z"),
                ("Org/Second-8B", "2025-01-02T00:00:00Z"),
            ],
        )
        db.commit()
    session = OfflineSession()

    result = sync_known_models(db_path=db_path, session=session, limit=2)

    assert session.calls == 1
    assert result["counts"]["failed"] == 1
    assert result["counts"]["deferred"] == 1
    assert [item["outcome"] for item in result["results"]] == ["failed", "deferred"]
