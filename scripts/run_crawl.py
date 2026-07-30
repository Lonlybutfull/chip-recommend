#!/usr/bin/env python3
"""Crawl all source URLs to download chip/model/benchmark/price pages.

Usage:
    python scripts/run_crawl.py
    python scripts/run_crawl.py --pipe chips --max-chips 10
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    from chip_model.pipeline.crawl import main  # type: ignore[attr-defined]
    main()
