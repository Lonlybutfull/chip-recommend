"""Tests for cli.py — command invocation and output format."""

import subprocess
import sys
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
CLI_PATH = PROJECT_DIR / "scripts" / "run_cli.py"
DB_PATH = PROJECT_DIR / "data" / "parse1.db"


def _run(*args):
    """Run CLI and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(CLI_PATH), "--db-path", str(DB_PATH)] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_json(stdout):
    """Parse JSON from CLI output (strip any non-JSON prefix/suffix)."""
    text = stdout.strip()
    # Find the first { or [
    start = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            start = i
            break
    if start < 0:
        raise ValueError(f"No JSON found in output: {text[:200]}")
    return json.loads(text[start:])


def test_db_status():
    """cli.py db status should return JSON with table counts."""
    code, out, err = _run("db", "status")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert "tables" in data
    assert int(data["tables"]["chips"]) > 0


def test_chip_search():
    """cli.py chip search -s H100 should find results."""
    code, out, err = _run("chip", "search", "-s", "H100", "--limit", "3")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert data["count"] > 0


def test_chip_profile_by_id():
    """cli.py chip profile 1 should return full profile."""
    code, out, err = _run("chip", "profile", "1")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert "chip" in data
    assert "benchmarks" in data


def test_chip_recommend():
    """cli.py chip recommend -m Qwen2.5-7B should return candidates."""
    code, out, err = _run("chip", "recommend", "-m", "Qwen2.5-7B",
                           "-s", "inference", "--domestic", "--limit", "3")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert "candidates" in data


def test_model_search():
    """cli.py model search -s Llama should find models."""
    code, out, err = _run("model", "search", "-s", "Llama", "--limit", "3")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert data["count"] > 0


def test_provenance_stats():
    """cli.py provenance stats should return aggregation."""
    code, out, err = _run("provenance", "stats")
    assert code == 0, f"Exit {code}: {err}"
    data = _parse_json(out)
    assert data["total"] > 0


def test_help_output():
    """cli.py --help should succeed."""
    code, out, err = _run("--help")
    assert code == 0


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  PASS: {name}")
            except AssertionError as e:
                print(f"  FAIL: {name} — {e}")
            except Exception as e:
                print(f"  ERROR: {name} — {type(e).__name__}: {e}")
    print("\nCLI tests complete!")
