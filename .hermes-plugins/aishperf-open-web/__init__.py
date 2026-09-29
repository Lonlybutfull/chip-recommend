"""Hermes plugin for the loopback-only AISHPerf open-web tool service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


TOOLSET = "aishperf_open_web"


def _base_url() -> str:
    value = os.getenv("AISH_PERF_OPEN_WEB_TOOL_URL", "http://127.0.0.1:5341").rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("AISHPerf 工具服务必须使用本机 HTTP 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("AISHPerf 工具服务地址格式无效。")
    return value


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("AISH_PERF_OPEN_WEB_TOOL_TOKEN", "").strip()
    if not token:
        raise RuntimeError("AISH_PERF_OPEN_WEB_TOOL_TOKEN 未配置。")
    request = urllib.request.Request(
        _base_url() + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"AISHPerf 工具返回 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"AISHPerf 工具服务不可达：{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("AISHPerf 工具返回值不是 JSON 对象。")
    return value


def _tool_response(path: str, args: dict[str, Any]) -> str:
    try:
        return json.dumps(_post(path, args), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)[:1000]}, ensure_ascii=False)


def _handle_search(args: dict, **kwargs) -> str:
    return _tool_response("/v1/search", args)


def _handle_preview(args: dict, **kwargs) -> str:
    return _tool_response("/v1/preview", args)


def _handle_submit_selection(args: dict, **kwargs) -> str:
    return _tool_response("/v1/submit-selection", args)


SKILL_ENUM = [
    "chip-identity", "chip-specs", "chip-compute", "chip-compatibility",
    "chip-benchmark", "chip-deployment",
]
CATEGORY_ENUM = ["芯片型号", "基础参数", "算力指标", "兼容信息", "实测数据", "部署资料"]

SEARCH_SCHEMA = {
    "name": "open_web_search",
    "description": "执行已经规划好的 10 条芯片资料搜索词，完成 URL 安全检查、规范化与去重。",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "skill": {"type": "string", "enum": SKILL_ENUM},
            "target_chip": {"type": "string"},
            "queries": {
                "type": "array", "minItems": 10, "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["query", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["run_id", "skill", "target_chip", "queries"],
        "additionalProperties": False,
    },
}

PREVIEW_SCHEMA = {
    "name": "open_web_preview",
    "description": "访问本轮搜索产生的全部候选 URL，保存完整快照并返回每页不超过 500 字的核心文本。",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "candidate_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["run_id", "candidate_ids"],
        "additionalProperties": False,
    },
}

SUBMIT_SCHEMA = {
    "name": "open_web_submit_selection",
    "description": "提交对每个已预览候选的选择或拒绝决定，并对选中 URL 执行完整字段提取。",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "skill": {"type": "string", "enum": SKILL_ENUM},
            "decisions": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "selected": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "matched_categories": {"type": "array", "items": {"type": "string", "enum": CATEGORY_ENUM}},
                        "suggested_skills": {"type": "array", "items": {"type": "string", "enum": SKILL_ENUM}},
                    },
                    "required": ["candidate_id", "selected", "reason", "matched_categories", "suggested_skills"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["run_id", "skill", "decisions"],
        "additionalProperties": False,
    },
}


def register(ctx) -> None:
    for name, schema, handler, emoji in (
        ("open_web_search", SEARCH_SCHEMA, _handle_search, "🔎"),
        ("open_web_preview", PREVIEW_SCHEMA, _handle_preview, "📄"),
        ("open_web_submit_selection", SUBMIT_SCHEMA, _handle_submit_selection, "✅"),
    ):
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
