#!/usr/bin/env python3
"""Import link library CSV into database link_library table.

Reads data/信息来源链接库_final.csv and inserts all rows into
the link_library table via database.add_link(), with URL dedup.

Usage:
    python chip_model/pipeline/import_links.py
    python chip_model/pipeline/import_links.py --csv custom.csv
    python chip_model/pipeline/import_links.py --force  # re-import even if data exists
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from chip_model.database import (
    get_db,
    init_db,
    import_links_csv,
    get_db_path,
    get_link_library_stats,
)


def main():
    parser = argparse.ArgumentParser(
        description="Import link library CSV into database"
    )
    parser.add_argument(
        "--csv", default=str(HERE / "data" / "信息来源链接库_final.csv"),
        help="Path to link library CSV (default: data/信息来源链接库_final.csv)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-import even if link_library already has data"
    )
    parser.add_argument(
        "--init-db", action="store_true",
        help="Initialize database schema before importing"
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV file not found: {csv_path}")
        sys.exit(1)

    # Init DB if needed
    if args.init_db:
        db_path = get_db_path()
        if not db_path.exists():
            init_db(db_path)

    # Import
    print(f"[import_links] Reading from: {csv_path}")
    with get_db() as db:
        count = import_links_csv(db, csv_path, force=args.force)
        db.commit()

    # Show stats
    stats = get_link_library_stats()
    print(f"[import_links] Done. link_library now has {stats['total']} rows.")
    print(f"  Categories: {len(stats['by_category'])}")
    for cat, cnt in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
        print(f"    {cat}: {cnt}")

    return count


if __name__ == "__main__":
    main()
