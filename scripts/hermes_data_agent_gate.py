#!/usr/bin/env python3
"""Quiet Hermes gate for the complete data-agent cycle."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AISHPerf 数据抓取智能体静默唤醒门控")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("data_agent_gate.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        transport = config.get("transport", "local")
        if transport == "ssh":
            ssh = config["ssh"]
            command = [
                "ssh", "-i", ssh["identity_file"], "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={ssh['known_hosts_file']}",
                "-p", str(ssh.get("port", 22)), f"{ssh['user']}@{ssh['host']}", "data-agent",
            ]
            cwd = args.config.parent
        elif transport == "local_docker":
            container = str(config.get("container") or "")
            if container != "chip-recommend":
                raise ValueError("本机 Docker 门控只允许 chip-recommend 容器。")
            source_limit = int(config.get("source_limit", 50))
            if not 1 <= source_limit <= 50:
                raise ValueError("source_limit 必须在 1 到 50 之间。")
            command = [
                "/usr/bin/docker", "exec",
            ]
            mode = config.get("mode", "formal")
            if mode == "paused":
                print(json.dumps({"wakeAgent": False, "status": "paused",
                                  "reason": "正式数据巡检已暂停，仅手动芯片隔离测试"},
                                 ensure_ascii=False))
                return 0
            if mode == "test":
                db_path = str(config.get("db_path") or "")
                if not re.fullmatch(r"/app/data/test_runs/[A-Za-z0-9-]+/data\.db", db_path):
                    raise ValueError("测试门控仅允许隔离副本中的 data.db。")
                command.extend(["-e", f"DATA_DB_PATH={db_path}"])
            elif mode != "formal":
                raise ValueError("不支持的门控模式。")
            command.extend([
                container,
                "python", "scripts/run_data_agent.py", "run",
                "--limit", str(source_limit),
                "--resume-stale-targets",
            ])
            if mode == "test":
                command.extend(["--resume-only"])
            else:
                command.extend(["--daily-start-guard"])
            cwd = args.config.parent
        elif transport == "local":
            root = Path(config["project_root"]).expanduser().resolve()
            command = [
                config.get("python_executable", sys.executable),
                str(root / "scripts" / "run_data_agent.py"),
                "--db", str(Path(config.get("db_path", root / "data" / "data.db"))),
                "run", "--limit", str(int(config.get("source_limit", 50))),
            ]
            if config.get("proxy"):
                command.extend(["--proxy", config["proxy"]])
            cwd = root
        else:
            raise ValueError(f"不支持的数据智能体传输方式：{transport}")
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=int(config.get("timeout_seconds", 1800)), check=False,
        )
        if completed.returncode not in {0, 2}:
            raise RuntimeError((completed.stderr or completed.stdout or "数据智能体执行失败")[:2000])
        summary = json.loads(completed.stdout)
        actionable = int(summary.get("awaiting_agent_jobs") or 0) > 0 or int(summary.get("failed_jobs") or 0) > 0
        result = {"wakeAgent": False}
        if actionable:
            result = {
                "wakeAgent": True,
                "context": {
                    "data_agent": {
                        **summary,
                        "claim_command": config.get(
                            "claim_command", "python3 ~/.hermes/scripts/hermes_data_agent_worker.py claim"
                        ),
                    }
                },
            }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
