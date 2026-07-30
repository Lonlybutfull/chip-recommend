#!/usr/bin/env python3
"""Auto-enrich chip data by searching web for missing hardware specs.

Usage:
    python scripts/run_enrich.py
    python scripts/run_enrich.py --dry-run
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    from chip_model.pipeline.enrich import main  # type: ignore[attr-defined]
    main()
