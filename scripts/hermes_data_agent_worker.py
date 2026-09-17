#!/usr/bin/env python3
"""Hermes-side restricted SSH bridge for claim, snapshot, finish and publish actions."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _run(config_path: Path, remote_command: str, stdin: str | None = None) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") == "paused":
        print(json.dumps({"status": "paused", "jobs": [],
                          "reason": "正式数据抓取任务已暂停"}, ensure_ascii=False))
        return 0
    if config.get("transport") == "local_docker":
        container = str(config.get("container") or "chip-recommend")
        if container != "chip-recommend":
            raise ValueError("本机 Worker 只允许 chip-recommend 容器。")
        worker_id = str(config.get("worker_id") or "hermes-heartbeat")
        skills = config.get("skills") or [
            "url-discovery", "chip-catalog", "chip-basic", "chip-compute",
            "chip-interconnect", "chip-ecosystem", "chip-price", "model-catalog",
            "model-activity", "benchmark-ingest", "deployment-ingest",
            "data-quality-review",
        ]
        base = ["/usr/bin/docker", "exec"]
        if stdin is not None:
            base.append("-i")
        if config.get("mode") == "test":
            db_path = str(config.get("db_path") or "")
            if not re.fullmatch(r"/app/data/test_runs/[A-Za-z0-9-]+/data\.db", db_path):
                raise ValueError("测试 Worker 仅允许隔离副本中的 data.db。")
            base.extend(["-e", f"DATA_DB_PATH={db_path}"])
        elif config.get("mode", "formal") != "formal":
            raise ValueError("不支持的 Worker 模式。")
        if remote_command == "data-agent-claim":
            command = [
                *base, container, "python", "scripts/run_data_agent.py", "claim",
                "--worker", worker_id, "--skills", ",".join(skills),
                "--limit", str(int(config.get("job_limit") or 5)),
            ]
        else:
            script = {
                "data-agent-snapshot": "hermes_data_agent_snapshot_server.py",
                "data-agent-finish": "hermes_data_agent_finish_server.py",
                "candidate-publish": "hermes_candidate_publish_server.py",
            }.get(remote_command)
            if not script:
                raise ValueError(f"不支持的本机命令：{remote_command}")
            command = [
                *base, "-e", "PYTHONPATH=/app", container, "python", f"scripts/{script}"
            ]
    elif config.get("transport") == "ssh":
        ssh = config["ssh"]
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={Path(ssh['known_hosts_file']).resolve()}",
            "-i", str(Path(ssh["identity_file"]).resolve()),
            "-p", str(int(ssh.get("port", 22))), f"{ssh['user']}@{ssh['host']}",
            remote_command,
        ]
    else:
        raise ValueError("Worker 只支持本机 Docker 或受限 SSH 传输。")
    result = subprocess.run(
        command, input=stdin, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300, check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["claim", "snapshot", "finish", "publish"])
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            os.environ.get(
                "HERMES_DATA_AGENT_CONFIG",
                Path(__file__).with_name("data_agent_gate.json"),
            )
        ),
    )
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    if args.action == "claim":
        return _run(args.config, "data-agent-claim")
    raw = args.payload.read_text(encoding="utf-8") if args.payload else sys.stdin.read()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("payload 必须是 JSON 对象。")
    if args.action == "snapshot":
        return _run(args.config, "data-agent-snapshot", json.dumps(value, ensure_ascii=False))
    return _run(
        args.config,
        "data-agent-finish" if args.action == "finish" else "candidate-publish",
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
    )


if __name__ == "__main__":
    raise SystemExit(main())
