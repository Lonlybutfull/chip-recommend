#!/usr/bin/env python3
"""Hermes cron pre-check for the AISHPerf source refresh pipeline.

The script is copied to ``~/.hermes/scripts`` during installation. It runs the
deterministic source refresh command and emits a Hermes ``wakeAgent`` decision:
quiet ticks cost no model call, while meaningful changes wake the attached
``aishperf-data-heartbeat`` skill with compact structured context. The same
gate supports a local runner or a pinned, restricted SSH transport.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(__file__).with_name("source_refresh_gate.json")
ACTIONABLE_OUTCOMES = {"changed"}
FAILURE_OUTCOMES = {"failed", "retry_wait", "unsupported"}


class GateConfigurationError(RuntimeError):
    """The local Hermes gate configuration is missing or invalid."""


def _resolve_path(project_root: Path, value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _common_config(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        threshold = int(raw.get("failure_notify_threshold", 3))
        timeout_seconds = int(raw.get("timeout_seconds", 900))
        max_attempts = int(raw.get("max_attempts", 2))
    except (TypeError, ValueError) as exc:
        raise GateConfigurationError("阈值、超时和重试次数必须是整数。") from exc
    if threshold <= 0 or timeout_seconds <= 0 or max_attempts <= 0:
        raise GateConfigurationError(
            "failure_notify_threshold、timeout_seconds 和 max_attempts 必须大于 0。"
        )
    notify_on_new = raw.get("notify_on_new", False)
    if not isinstance(notify_on_new, bool):
        raise GateConfigurationError("notify_on_new 必须是 JSON 布尔值。")
    return {
        "failure_notify_threshold": threshold,
        "notify_on_new": notify_on_new,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
    }


def _required_file(base: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise GateConfigurationError(f"{label} 必须是非空路径。")
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = base / target
    target = target.resolve()
    if not target.is_file():
        raise GateConfigurationError(f"{label} 不存在：{target}")
    return target


def load_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateConfigurationError(f"配置文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise GateConfigurationError(f"配置文件不是有效 JSON：{exc}") from exc

    common = _common_config(raw)
    transport = str(raw.get("transport") or "local").strip().lower()
    if transport == "ssh":
        ssh_config = raw.get("ssh")
        if not isinstance(ssh_config, dict):
            raise GateConfigurationError("SSH 模式必须提供 ssh 配置对象。")
        host = str(ssh_config.get("host") or "").strip()
        user = str(ssh_config.get("user") or "").strip()
        try:
            port = int(ssh_config.get("port", 22))
        except (TypeError, ValueError) as exc:
            raise GateConfigurationError("ssh.port 必须是整数。") from exc
        if not host or not user or not 1 <= port <= 65535:
            raise GateConfigurationError("ssh.host、ssh.user 和有效端口均为必填。")
        base = path.parent.resolve()
        return {
            **common,
            "transport": "ssh",
            "working_directory": base,
            "ssh_host": host,
            "ssh_user": user,
            "ssh_port": port,
            "identity_file": _required_file(
                base, ssh_config.get("identity_file"), "ssh.identity_file"
            ),
            "known_hosts_file": _required_file(
                base, ssh_config.get("known_hosts_file"), "ssh.known_hosts_file"
            ),
        }
    if transport != "local":
        raise GateConfigurationError("transport 仅支持 local 或 ssh。")

    root_value = raw.get("project_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise GateConfigurationError("project_root 必须是非空路径。")
    project_root = Path(root_value).expanduser().resolve()
    if not project_root.is_dir():
        raise GateConfigurationError(f"项目目录不存在：{project_root}")
    runner = project_root / "scripts" / "run_source_refresh.py"
    if not runner.is_file():
        raise GateConfigurationError(f"来源复查入口不存在：{runner}")

    try:
        link_ids = [int(value) for value in raw.get("link_ids", [])]
    except (TypeError, ValueError) as exc:
        raise GateConfigurationError("link_ids 必须是正整数数组。") from exc
    if not link_ids or any(value <= 0 for value in link_ids):
        raise GateConfigurationError("link_ids 至少包含一个正整数。")

    return {
        **common,
        "transport": "local",
        "working_directory": project_root,
        "project_root": project_root,
        "runner": runner,
        "python_executable": str(raw.get("python_executable") or sys.executable),
        "link_ids": list(dict.fromkeys(link_ids)),
        "db_path": _resolve_path(
            project_root,
            raw.get("db_path"),
            project_root / "data" / "data.db",
        ),
        "snapshot_dir": _resolve_path(
            project_root,
            raw.get("snapshot_dir"),
            project_root / "data" / "source_snapshots",
        ),
        "proxy": str(raw.get("proxy") or "").strip(),
    }


def build_command(config: dict[str, Any]) -> list[str]:
    if config.get("transport") == "ssh":
        return [
            "ssh",
            "-i",
            str(config["identity_file"]),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={config['known_hosts_file']}",
            "-p",
            str(config["ssh_port"]),
            f"{config['ssh_user']}@{config['ssh_host']}",
            "source-refresh",
        ]
    command = [
        config["python_executable"],
        str(config["runner"]),
        "--link-ids",
        ",".join(str(value) for value in config["link_ids"]),
        "--apply-state",
        "--db",
        str(config["db_path"]),
        "--snapshot-dir",
        str(config["snapshot_dir"]),
        "--max-attempts",
        str(config["max_attempts"]),
    ]
    if config["proxy"]:
        command.extend(["--proxy", config["proxy"]])
    return command


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    error_message = result.get("error_message")
    if isinstance(error_message, str):
        error_message = error_message[:1000]
    diff_summary = result.get("diff_summary")
    if isinstance(diff_summary, str):
        diff_summary = diff_summary[:1200]
    candidate_fields = result.get("candidate_fields")
    if not isinstance(candidate_fields, list):
        candidate_fields = []
    return {
        "link_id": result.get("link_id"),
        "description": result.get("description"),
        "vendor": result.get("vendor"),
        "category": result.get("category"),
        "outcome": result.get("outcome"),
        "requested_url": result.get("requested_url"),
        "final_url": result.get("final_url"),
        "http_status": result.get("http_status"),
        "checked_at": result.get("checked_at"),
        "duration_ms": result.get("duration_ms", 0),
        "attempt_count": result.get("attempt_count", 0),
        "response_bytes": result.get("response_bytes", 0),
        "previous_content_hash": result.get("previous_content_hash"),
        "content_hash": result.get("content_hash"),
        "snapshot_path": result.get("snapshot_path"),
        "normalized_snapshot_path": result.get("normalized_snapshot_path"),
        "diff_path": result.get("diff_path"),
        "diff_summary": diff_summary,
        "diff_added_lines": result.get("diff_added_lines", 0),
        "diff_removed_lines": result.get("diff_removed_lines", 0),
        "diff_truncated": bool(result.get("diff_truncated", False)),
        "candidate_fields": candidate_fields[:8],
        "failure_count": result.get("failure_count", 0),
        "error_code": result.get("error_code"),
        "error_message": error_message,
        "next_check_at": result.get("next_check_at"),
    }


def decide(summary: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Convert a refresh summary into the Hermes script-gate protocol."""
    if summary.get("business_tables_modified") is not False:
        raise RuntimeError("安全检查失败：来源巡检报告未确认业务表保持不变。")

    threshold = config["failure_notify_threshold"]
    changed = []
    failures = []
    new_sources = []
    checked_sources = []
    for result in summary.get("results", []):
        compact = _compact_result(result)
        checked_sources.append(compact)
        outcome = result.get("outcome")
        if outcome in ACTIONABLE_OUTCOMES:
            changed.append(compact)
        elif outcome == "new" and config["notify_on_new"]:
            new_sources.append(compact)
        elif outcome in FAILURE_OUTCOMES and int(result.get("failure_count") or 0) >= threshold:
            failures.append(compact)

    if not changed and not failures and not new_sources:
        return {"wakeAgent": False}

    return {
        "wakeAgent": True,
        "context": {
            "source_refresh": {
                "trigger": "hermes_ticker",
                "run_id": summary.get("run_id"),
                "status": summary.get("status"),
                "started_at": summary.get("started_at"),
                "finished_at": summary.get("finished_at"),
                "duration_ms": summary.get("duration_ms", 0),
                "counts": summary.get("counts", {}),
                # Include every checked source when the Agent is awakened so its
                # notification can audit the complete crawl scope, not only the
                # changed or failing subset. The gate remains silent for normal
                # no-change runs, so this does not add model calls.
                "checked_sources": checked_sources,
                "changed_sources": changed,
                "new_sources": new_sources,
                "failing_sources": failures,
                "business_tables_modified": False,
            }
        },
    }


def run(config_path: Path) -> int:
    try:
        config = load_config(config_path)
        completed = subprocess.run(
            build_command(config),
            cwd=config["working_directory"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config["timeout_seconds"],
            check=False,
        )
        if completed.returncode not in {0, 2}:
            message = (completed.stderr or completed.stdout or "无错误详情").strip()
            raise RuntimeError(
                f"来源巡检命令退出码为 {completed.returncode}：{message[:2000]}"
            )
        try:
            summary = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("来源巡检没有返回有效 JSON。") from exc
        decision = decide(summary, config)
    except (GateConfigurationError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(decision, ensure_ascii=False, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes 来源巡检静默唤醒门控")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    return run(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
