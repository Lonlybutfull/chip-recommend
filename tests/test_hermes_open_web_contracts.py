import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from chip_model.pipeline.hermes_open_web_contracts import (
    HERMES_OPEN_WEB_SKILLS,
    SearchRequest,
    SelectionRequest,
)
from chip_model.pipeline.hermes_open_web_state import (
    create_hermes_open_web_run,
    read_artifact,
    resolve_run_dir,
    stable_candidate_id,
)
from chip_model.pipeline.open_web_test import database_fingerprints


SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO chips (id,chip_model,vendor) VALUES (1,'TestChip X1','Vendor')"
        )
    return path


def _queries(count: int = 10) -> list[dict[str, str]]:
    return [
        {"query": f"TestChip X1 official specification {index}", "reason": "覆盖官方资料"}
        for index in range(count)
    ]


def test_search_request_requires_exactly_ten_unique_queries() -> None:
    valid = SearchRequest(
        run_id="20260929-120000-abcdef12",
        skill="chip-specs",
        target_chip="TestChip X1",
        queries=_queries(),
    )
    assert len(valid.queries) == 10

    with pytest.raises(ValidationError):
        SearchRequest(
            run_id="20260929-120000-abcdef12",
            skill="chip-specs",
            target_chip="TestChip X1",
            queries=_queries(9),
        )

    duplicates = _queries()
    duplicates[-1]["query"] = "  TESTCHIP   X1 OFFICIAL SPECIFICATION 0 "
    with pytest.raises(ValidationError):
        SearchRequest(
            run_id="20260929-120000-abcdef12",
            skill="chip-specs",
            target_chip="TestChip X1",
            queries=duplicates,
        )


def test_selection_contract_rejects_unknown_categories_and_skills() -> None:
    assert set(HERMES_OPEN_WEB_SKILLS) == {
        "chip-identity", "chip-specs", "chip-compute", "chip-compatibility",
        "chip-benchmark", "chip-deployment",
    }
    with pytest.raises(ValidationError):
        SelectionRequest.model_validate({
            "run_id": "20260929-120000-abcdef12",
            "skill": "chip-specs",
            "decisions": [{
                "candidate_id": "candidate-abcdef123456",
                "selected": True,
                "reason": "页面有明确规格",
                "matched_categories": ["模型信息"],
                "suggested_skills": ["model-catalog"],
            }],
        })


def test_run_state_is_confined_and_formal_database_is_unchanged(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    before = database_fingerprints(formal)
    run = create_hermes_open_web_run(
        source_db=formal,
        skill="chip-specs",
        target_chip="TestChip X1",
        target_fields=["vram_gb"],
    )

    folder = resolve_run_dir(formal, run["session_id"])
    marker = json.loads((folder / "test_mode.json").read_text(encoding="utf-8"))
    assert marker["mode"] == "test"
    assert marker["pipeline"] == "hermes-open-web"
    assert read_artifact(folder, "hermes_search_plan.json", default={}) == {}
    assert stable_candidate_id("https://example.com/a") == stable_candidate_id(
        "https://example.com/a"
    )
    with pytest.raises(ValueError):
        resolve_run_dir(formal, "../escape")
    assert database_fingerprints(formal)["db"] == before["db"]
