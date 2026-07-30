#!/usr/bin/env python3
"""Seed the database with chips, models, benchmarks, and compatibility data.

Usage:
    python scripts/run_seed.py
    python scripts/run_seed.py --reset
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    from chip_model.pipeline.seed import main  # type: ignore[attr-defined]
    main()
