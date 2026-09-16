"""Curated chip-only source families from the parent-URL link library.

The CSV is a selection catalogue, not a publication feed.  Family selections
are applied only to an isolated test database by test_workspace.py.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urlparse

from chip_model.database import get_db_path, get_project_root
from chip_model.pipeline.data_agent import canonicalize_source_url


LINK_CSV = get_project_root() / "data" / "信息来源链接库_final.csv"


def is_chip_source(url: str, category: str) -> bool:
    """Keep hardware sources; model, benchmark and price sources are separate."""
    host = (urlparse(url).hostname or "").casefold()
    label = category.strip()
    return (
        urlparse(url).scheme.casefold() == "https"
        and host != "huggingface.co"
        and "芯片" in label
        and "模型" not in label
    )


def _is_child(parent: str, child: str) -> bool:
    root, item = urlparse(parent), urlparse(child)
    prefix = root.path.rstrip("/") + "/"
    return (
        root.scheme == item.scheme == "https"
        and root.hostname == item.hostname
        and child != parent
        and (root.path in {"", "/"} or item.path.startswith(prefix))
    )


def read_chip_families(csv_path: str | Path = LINK_CSV) -> list[dict]:
    """Return listed parent pages with chip-detail children, in CSV order."""
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    by_url = {str(row.get("URL") or "").strip(): row for row in rows}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        url = str(row.get("URL") or "").strip()
        parent = str(row.get("父URL") or "").strip()
        if not parent or not is_chip_source(url, str(row.get("分类") or "")):
            continue
        if parent not in by_url or not _is_child(parent, url):
            continue
        groups.setdefault(parent, []).append({
            "url": url,
            "description": str(row.get("描述") or "").strip(),
            "category": str(row.get("分类") or "").strip(),
            "vendor": str(row.get("涉及厂商") or "").strip(),
        })
    return [
        {
            "parent_url": parent,
            "description": str(by_url[parent].get("描述") or "").strip(),
            "vendor": str(by_url[parent].get("涉及厂商") or "").strip(),
            "children": children,
            "child_count": len(children),
        }
        for parent, children in groups.items()
    ]


def selection_path(source_db: str | Path | None = None) -> Path:
    source = Path(source_db or get_db_path()).resolve()
    return source.parent / "test_runs" / "chip_family_selection.json"


def read_family_selection(source_db: str | Path | None = None) -> dict | None:
    path = selection_path(source_db)
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("芯片 URL 族测试配置无效。")
    return value


def save_family_selection(
    parent_url: str, child_urls: list[str], *,
    source_db: str | Path | None = None,
    csv_path: str | Path = LINK_CSV,
) -> dict:
    """Pin 1 parent and 1-9 listed children without altering formal data."""
    families = {item["parent_url"]: item for item in read_chip_families(csv_path)}
    parent = canonicalize_source_url(parent_url)
    family = families.get(parent_url) or families.get(parent)
    if not family:
        raise ValueError("父URL不在芯片链接库中，或没有合格的芯片目标页。")
    allowed = {item["url"] for item in family["children"]}
    chosen = list(dict.fromkeys(child_urls))
    if not 1 <= len(chosen) <= 9 or any(url not in allowed for url in chosen):
        raise ValueError("请选择该父URL下 1–9 个库内芯片目标页。")
    value = {"parent_url": family["parent_url"], "child_urls": chosen,
             "catalogue": str(Path(csv_path).resolve())}
    path = selection_path(source_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value


def selected_family_rows(
    selection: dict, *, csv_path: str | Path = LINK_CSV,
) -> list[dict]:
    """Revalidate a stored selection against the catalogue before every run."""
    families = {item["parent_url"]: item for item in read_chip_families(csv_path)}
    family = families.get(selection.get("parent_url"))
    if not family:
        raise ValueError("选定的父URL已不在芯片链接库中。")
    children = {item["url"]: item for item in family["children"]}
    chosen = list(dict.fromkeys(selection.get("child_urls") or []))
    if not 1 <= len(chosen) <= 9 or any(url not in children for url in chosen):
        raise ValueError("选定的芯片目标页已不在该父URL族中。")
    root = {"url": family["parent_url"], "description": family["description"],
            "category": "芯片产品列表", "vendor": family["vendor"],
            "role": "listing", "parent_url": ""}
    return [root] + [
        {**children[url], "role": "detail", "parent_url": family["parent_url"]}
        for url in chosen
    ]
