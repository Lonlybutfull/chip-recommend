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


def test_prompt_only_passes_run_context_and_leaves_process_to_skill() -> None:
    prompt = build_hermes_prompt(
        run_id="20260929-120000-abcdef12",
        unit_id="chip-nvidia-h100",
        scope_type="chip",
        skill="chip-specs",
        target_chip="NVIDIA H100",
        target_fields=["vram_gb", "vram_bw_gb_s"],
    )
    assert "chip-specs" in prompt
    assert "NVIDIA H100" in prompt
    assert "UNIT_ID=chip-nvidia-h100" in prompt
    assert "按照已加载 Skill" in prompt
    assert "open_web_search" not in prompt
    assert "10 条" not in prompt


def test_orchestrator_passes_skill_and_toolset_to_hermes(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        prompt = command[-1]
        run_id = prompt.split("RUN_ID=", 1)[1].splitlines()[0].strip()
        unit_id = prompt.split("UNIT_ID=", 1)[1].splitlines()[0].strip()
        folder = formal.parent / "test_runs" / run_id
        unit_path = folder / "units" / unit_id / "manifest.json"
        manifest = json.loads(unit_path.read_text(encoding="utf-8"))
        manifest.update({
            "status": "success", "stage": "completed",
            "finished_at": "2026-09-29T12:00:00Z",
        })
        unit_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
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


def test_all_scope_runs_every_chip_and_discovery_as_one_parent(tmp_path: Path) -> None:
    formal = _database(tmp_path / "data.db")
    with sqlite3.connect(formal) as db:
        db.executemany(
            "INSERT INTO chips (chip_model,vendor) VALUES (?,?)",
            [("Chip A", "Vendor"), ("Chip B", "Vendor")],
        )
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        prompt = command[-1]
        run_id = prompt.split("RUN_ID=", 1)[1].splitlines()[0].strip()
        unit_id = prompt.split("UNIT_ID=", 1)[1].splitlines()[0].strip()
        unit_path = formal.parent / "test_runs" / run_id / "units" / unit_id / "manifest.json"
        manifest = json.loads(unit_path.read_text(encoding="utf-8"))
        manifest.update({"status": "success", "stage": "completed", "finished_at": "done"})
        unit_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": "done", "stderr": ""})()

    result = run_hermes_open_web_test(
        source_db=formal,
        skill="chip-specs",
        target_chip=None,
        runner=runner,
        hermes_command=["hermes"],
    )

    assert result["scope"] == "all"
    assert result["status"] == "success"
    assert result["counts"]["units_total"] == 3
    assert result["counts"]["units_completed"] == 3
    assert len(calls) == 3
    assert any("SCOPE_TYPE=discovery" in command[-1] for command in calls)
