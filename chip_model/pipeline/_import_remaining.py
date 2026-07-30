#!/usr/bin/env python3
"""Import remaining discovered chips (series_search + link_library_crawl) via CLI.

Deduplicates against existing DB chips. Uses `parse1 chip add` CLI.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent

CMDS = [sys.executable, str(HERE / "scripts" / "run_cli.py")]

def chip_exists(name):
    r = subprocess.run(CMDS + ["chip", "search", "-s", name, "--limit", "1"],
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        return False
    try:
        d = json.loads(r.stdout)
        # match chip_model exactly
        for c in d.get("chips", []):
            if c.get("chip_model", "").lower() == name.lower():
                return True
    except:
        pass
    return False

def chip_add(name, vendor, vendor_display, series, chip_type, tier, source_urls, method, notes):
    """Add chip via CLI if not exists."""
    if chip_exists(name):
        return "skip"

    fields = {
        "chip_model": name,
        "vendor": vendor or "",
        "vendor_display": vendor_display or vendor or "",
        "chip_series": series or name,
        "chip_type": chip_type or "GPU",
        "tier": tier or "datacenter",
    }
    # Clean empty vendor
    if not fields["vendor"]:
        fields.pop("vendor")

    url = (source_urls or f"https://www.google.com/search?q={name}+AI+accelerator").split("|")[0].strip()
    if not url or len(url) < 10:
        url = f"https://www.google.com/search?q={name}+AI+accelerator"

    source = {
        "source_type": "web_crawl",
        "source_url": url,
        "source_detail": f"Discovered via {method}: {notes[:80] if notes else ''}",
        "confidence": "medium",
        "is_official": False,
        "notes": f"Discovery method: {method}. {notes or ''}"[:200],
    }

    d_json = json.dumps(fields, ensure_ascii=False)
    s_json = json.dumps(source, ensure_ascii=False)

    r = subprocess.run(CMDS + ["chip", "add", "-d", d_json, "-s", s_json],
                       capture_output=True, text=True, encoding='utf-8')

    if r.returncode == 0:
        try:
            result = json.loads(r.stdout)
            return ("insert", result.get("chip_id"))
        except:
            return ("error", r.stdout[:100])
    # Check if "already exists" in stderr
    if "already exists" in r.stderr.lower():
        return "skip"
    return ("error", r.stderr[:150])

def main():
    with open(HERE / "data" / "discovered_chips.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # Deduplicate by chip_model (case-insensitive) within CSV
    seen = {}
    for r in rows:
        key = r["chip_model"].strip().lower()
        if key in seen:
            # merge source URLs
            old = seen[key]
            old_urls = set((old.get("source_urls", "") or "").split("|"))
            new_urls = set((r.get("source_urls", "") or "").split("|"))
            old["source_urls"] = "|".join(old_urls | new_urls)
        else:
            seen[key] = dict(r)
    chips = list(seen.values())

    # Only process non-seed chips
    remaining = [c for c in chips if c["discovery_method"] != "seed"]

    inserted = 0
    skipped = 0
    errors = 0

    for i, chip in enumerate(remaining):
        name = chip["chip_model"].strip()
        method = chip["discovery_method"]
        vendor = (chip.get("vendor") or "").strip()
        vendor_dsp = (chip.get("vendor_display") or "").strip()
        series = (chip.get("chip_series") or "").strip()
        chip_type = (chip.get("chip_type") or "GPU").strip()
        tier = (chip.get("tier") or "datacenter").strip()
        urls = (chip.get("source_urls") or "").strip()
        notes = (chip.get("notes") or "").strip()

        # Skip obviously bad names
        if len(name) < 3:
            skipped += 1
            continue

        result = chip_add(name, vendor, vendor_dsp, series, chip_type, tier, urls, method, notes)

        if result == "skip":
            skipped += 1
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(remaining)}] skip {name}")
        elif isinstance(result, tuple) and result[0] == "insert":
            inserted += 1
            print(f"  [{i+1}/{len(remaining)}] INSERT [{result[1]}] {name} ({method})")
        else:
            errors += 1
            print(f"  [{i+1}/{len(remaining)}] ERROR {name}: {result}")

    print(f"\n=== Results ===")
    print(f"  Inserted: {inserted}")
    print(f"  Skipped:  {skipped}")
    print(f"  Errors:   {errors}")

if __name__ == "__main__":
    main()
