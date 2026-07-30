#!/usr/bin/env python3
"""CLI entry point for AISHPerf.

Usage:
    python scripts/run_cli.py db status
    python scripts/run_cli.py chip search nvidia
    python scripts/run_cli.py --help
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    from chip_model.cli_app import app
    app()
