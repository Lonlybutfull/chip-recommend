#!/usr/bin/env python3
"""Auto-enrich chip data + upgrade non-official sources.

Usage:
    python scripts/run_enrich.py                          # fill missing critical specs
    python scripts/run_enrich.py --upgrade-official       # find non-official → official sources
"""
import sys
import os
import argparse

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Chip spec enrichment")
    ap.add_argument("--upgrade-official", action="store_true",
                    help="Search official sources for fields currently non-official")
    args = ap.parse_args()

    if args.upgrade_official:
        from chip_model.pipeline.enrich import upgrade_non_official_sources
        upgrade_non_official_sources()
    else:
        from chip_model.pipeline.enrich import main
        main()
