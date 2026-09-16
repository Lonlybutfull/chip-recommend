#!/usr/bin/env python3
"""Hermes-side bridge for the restricted AISHPerf auto-apply command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_ssh_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ssh = payload.get("ssh")
    if payload.get("transport") != "ssh" or not isinstance(ssh, dict):
        raise ValueError("source_refresh_gate.json 未配置 SSH 传输。")
    required = ("host", "user", "identity_file", "known_hosts_file")
    missing = [key for key in required if not ssh.get(key)]
    if missing:
        raise ValueError("SSH 配置缺少：" + ", ".join(missing))
    return ssh


def build_command(ssh: dict[str, Any]) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={Path(ssh['known_hosts_file']).resolve()}",
        "-o", "ConnectTimeout=10",
        "-i", str(Path(ssh["identity_file"]).resolve()),
        "-p", str(int(ssh.get("port", 22))),
        f"{ssh['user']}@{ssh['host']}",
        "source-auto-apply",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通过受限 SSH 提交 Hermes 自动字段更新")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("source_refresh_gate.json"),
    )
    parser.add_argument("--payload", type=Path, help="JSON 文件；不提供时从 stdin 读取")
    args = parser.parse_args(argv)
    try:
        raw = args.payload.read_text(encoding="utf-8") if args.payload else sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("更新 payload 必须是 JSON 对象。")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        completed = subprocess.run(
            build_command(load_ssh_config(args.config)),
            input=encoded,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        return completed.returncode
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
