import json
import sqlite3
from pathlib import Path

from chip_model.pipeline.hermes_open_web_orchestrator import (
    build_hermes_prompt,
    run_hermes_open_web_test,
)


SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
    return path


def test_prompt_names_one_skill_and_requires_tool_sequence() -> None:
    prompt = build_hermes_prompt(
        run_id="20260929-120000-abcdef12",
        skill="chip-specs",
        target_chip="NVIDIA H100",
        target_fields=["vram_gb", "vram_bw_gb_s"],
    )
    assert "chip-specs" in prompt
    assert "NVIDIA H100" in prompt
    assert "10" in prompt
    assert prompt.index("open_web_search") < prompt.index("open_web_preview")
    assert prompt.index("open_web_preview") < prompt.index("open_web_submit_selection")


def test_orchestrator_passes_skill_and_toolset_to_hermes(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        run_id = command[-1].split("RUN_ID=", 1)[1].splitlines()[0].strip()
        folder = formal.parent / "test_runs" / run_id
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest.update({"status": "success", "finished_at": "2026-09-29T12:00:00Z"})
        (folder / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        return type("Completed", (), {"returncode": 0, "stdout": "done", "stderr": ""})()

    result = run_hermes_open_web_test(
        source_db=formal,
        skill="chip-specs",
        target_chip="NVIDIA H100",
        target_fields=["vram_gb"],
        runner=runner,
        hermes_command=["hermes"],
    )

    assert result["status"] == "success"
    assert "--skills" in captured["command"]
    assert captured["command"][captured["command"].index("--skills") + 1] == "chip-specs"
    assert "--toolsets" in captured["command"]
    assert captured["command"][captured["command"].index("--toolsets") + 1] == "aishperf_open_web"
