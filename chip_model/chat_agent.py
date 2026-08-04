"""
AISHPerf Chat Agent — DeepSeek-powered chip selection advisor.

Streaming chat endpoint with tool calling support.
System prompt built dynamically from .claude/skills/ directory.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ═══════════════════════════════════
# Skill loading — read .claude/skills/ directory
# ═══════════════════════════════════

def load_skills() -> list[dict]:
    """Scan .claude/skills/ for SKILL.md files and extract metadata."""
    skills = []
    skills_dir = _project_root / ".claude" / "skills"
    if not skills_dir.exists():
        return skills

    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir():
            continue
        md_path = skill_path / "SKILL.md"
        if not md_path.exists():
            continue

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            continue

        name = skill_path.name
        description = ""
        body = content

        # Parse YAML frontmatter if present
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].strip().split("\n"):
                    line = line.strip()
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k, v = k.strip(), v.strip()
                        if k == "name":
                            name = v
                        elif k == "description":
                            description = v
                body = content[end + 3:]

        # If no frontmatter description, take first meaningful line
        if not description:
            for line in body.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line[:200]
                    break

        skills.append({"name": name, "description": description, "path": str(md_path)})

    return skills


_skills_cache: list[dict] | None = None  # cached skill list
_skills_cache_mtime: float = 0.0        # last mtime of skills dir


def load_skills_cached() -> list[dict]:
    """Load skills with caching — only re-reads when .claude/skills/ changes."""
    global _skills_cache, _skills_cache_mtime
    skills_dir = _project_root / ".claude" / "skills"
    if not skills_dir.exists():
        _skills_cache = []
        _skills_cache_mtime = 0.0
        return []
    try:
        mtime = skills_dir.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _skills_cache is not None and mtime == _skills_cache_mtime:
        return _skills_cache
    _skills_cache = load_skills()
    _skills_cache_mtime = mtime
    return _skills_cache


# ═══════════════════════════════════
# Tool definitions for the LLM
# ═══════════════════════════════════

def _build_tools() -> list[dict]:
    """Build the tool list with current skill data baked in."""
    skills = load_skills_cached()
    skill_names = [s["name"] for s in skills]
    skill_desc_lines = []
    for s in skills:
        skill_desc_lines.append(f"- **{s['name']}**: {s.get('description', 'no description')}")

    return [
        {
            "type": "function",
            "function": {
                "name": "run_cli_command",
                "description": "Run an AISHPerf CLI command to query chip/model/benchmark data. "
                               "Available groups: chip (search/profile/recommend), model (search/profile), "
                               "benchmark (search), compat (search), provenance (show/stats), db (status). "
                               "All output is JSON. Use this to get real data about chips, models, benchmarks, "
                               "compatibility, and to run the recommendation engine.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The CLI command to run, WITHOUT the 'python scripts/run_cli.py' prefix. "
                                           "Examples: 'chip search --search H100 --tier datacenter --limit 5', "
                                           "'chip recommend --model Qwen2.5-7B --scenario train --training-days 7 --domestic --limit 5', "
                                           "'chip profile Ascend 910C', "
                                           "'model search --search Qwen2.5 --limit 10', "
                                           "'benchmark search --chip H100 --workload training', "
                                           "'db status'"
                        }
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "List ALL skills installed in this project. "
                               "MUST call this tool when the user asks what skills/技能/能力 you have, "
                               "or asks about available tools/capabilities. "
                               "NEVER answer skill questions from memory — always call this tool first.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "Load the full content of a specific skill by name. "
                               "Use this when the user wants to know what a specific skill does in detail, "
                               "or when you need to follow a skill's instructions precisely.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": f"The skill name to load. Available skills: {', '.join(skill_names)}"
                        }
                    },
                    "required": ["name"]
                }
            }
        },
    ]

# ── Build system prompt dynamically ──

def _build_system_prompt() -> str:
    skills = load_skills_cached()
    return f"""You are a chip selection assistant. Use run_cli_command to query data from the chip-model database.
- When user says "训练 Qwen2.5-7B" → call: chip recommend --model Qwen2.5-7B --scenario train [--training-days N] [--domestic] [--limit N]
- When user says "推荐芯片" or "对比" → call run_cli_command first
- Always call the tool BEFORE writing any text response
- Respond in Chinese with tables"""


# ═══════════════════════════════════
# CLI execution
# ═══════════════════════════════════

def execute_cli(command: str) -> str:
    """Execute a CLI command and return the output."""
    cli_script = _project_root / "scripts" / "run_cli.py"
    full_cmd = [sys.executable, str(cli_script)] + shlex.split(command)
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_project_root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return json.dumps({
                "error": f"CLI exited with code {result.returncode}",
                "stderr": result.stderr[:500],
                "stdout": result.stdout[:500] if result.stdout else "",
            }, ensure_ascii=False)
        return result.stdout if result.stdout else "{}"
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "CLI command timed out after 30 seconds"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def execute_list_skills() -> str:
    """Return the list of installed skills as JSON."""
    skills = load_skills_cached()
    return json.dumps({
        "count": len(skills),
        "skills": [{"name": s["name"], "description": s["description"]} for s in skills],
    }, ensure_ascii=False)


def execute_load_skill(name: str) -> str:
    """Load and return the full content of a skill's SKILL.md."""
    skills = load_skills_cached()
    for s in skills:
        if s["name"].lower() == name.lower():
            try:
                content = Path(s["path"]).read_text(encoding="utf-8")
                # Limit to avoid blowing context
                if len(content) > 8000:
                    content = content[:8000] + "\n\n...(truncated)"
                return json.dumps({
                    "name": s["name"],
                    "description": s["description"],
                    "content": content,
                }, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": f"Failed to read skill: {e}"}, ensure_ascii=False)
    # Use cached skill names for the error message
    skill_names = [s["name"] for s in skills]
    return json.dumps({
        "error": f"Skill '{name}' not found. Available: {', '.join(skill_names)}"
    }, ensure_ascii=False)


# ═══════════════════════════════════
# Sanitization — strip DSML/XML tool-call artifacts from LLM output
# ═══════════════════════════════════

_SANITIZE_PATTERNS = [
    # Full block tags with content: <function_calls>...</function_calls>, <invoke>...</invoke>, etc.
    re.compile(
        r'<\s*/?\s*(?:function_calls|tool_calls|invoke|parameter)\b[^>]*>[\s\S]*?<\s*/?\s*(?:function_calls|tool_calls|invoke|parameter)\b[^>]*>',
        re.IGNORECASE,
    ),
    # Self-closing / single-line tags: <invoke name="...">, <parameter ... />
    re.compile(
        r'<\s*/?\s*(?:function_calls|tool_calls|invoke|parameter)\b[^>]*/?\s*>',
        re.IGNORECASE,
    ),
    # Hallucinated OpenAI-style markers like <__SYS_...>, <__TOOL_...>
    re.compile(r'<\s*__+[a-zA-Z_][^>]*/?\s*>'),
]


def _sanitize_text(text: str) -> str:
    """Strip DSML/XML tool-call artifacts the LLM sometimes hallucinates into output."""
    for pat in _SANITIZE_PATTERNS:
        text = pat.sub('', text)
    return text


# ═══════════════════════════════════
# DeepSeek Chat (streaming)
# ═══════════════════════════════════

async def chat_stream(
    messages: list[dict],
    model: str = DEEPSEEK_MODEL,
) -> str:
    """Stream chat completion from DeepSeek, yielding SSE events.

    Returns the full concatenated response text.
    """
    # Prepend system prompt if not already present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": _build_system_prompt()}] + messages

    url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload = {
        "model": model,
        "messages": messages,
        "tools": _build_tools(),
        "stream": True,
        "temperature": 0.7,
    }

    full_text = ""
    tool_calls_buffer: list[dict] = []
    current_tool_call = {"id": "", "function": {"name": "", "arguments": ""}}

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    # Handle text content
                    content = delta.get("content", "")
                    if content:
                        content = _sanitize_text(content)
                        full_text += content
                        yield f"data: {json.dumps({'type': 'text', 'content': content}, ensure_ascii=False)}\n\n"

                    # Handle tool calls
                    if "tool_calls" in delta:
                        for tc in delta["tool_calls"]:
                            if "id" in tc:
                                if current_tool_call["id"]:
                                    tool_calls_buffer.append(current_tool_call)
                                current_tool_call = {
                                    "id": tc.get("id", ""),
                                    "function": {
                                        "name": tc.get("function", {}).get("name", ""),
                                        "arguments": tc.get("function", {}).get("arguments", ""),
                                    }
                                }
                            elif "function" in tc:
                                if "name" in tc.get("function", {}):
                                    current_tool_call["function"]["name"] += tc["function"]["name"]
                                if "arguments" in tc.get("function", {}):
                                    current_tool_call["function"]["arguments"] += tc["function"]["arguments"]

                except json.JSONDecodeError:
                    continue

    # Flush last tool call
    if current_tool_call["id"]:
        tool_calls_buffer.append(current_tool_call)

    # Execute tool calls if any
    if tool_calls_buffer:
        for tc in tool_calls_buffer:
            fn_name = tc["function"]["name"]
            fn_args_str = tc["function"]["arguments"]

            if fn_name == "run_cli_command":
                try:    args = json.loads(fn_args_str); cmd = args.get("command", "")
                except json.JSONDecodeError: cmd = fn_args_str
                result = execute_cli(cmd)
                if len(result) > 8000: result = result[:8000] + f"...(truncated)"
            elif fn_name == "list_skills":
                result = execute_list_skills()
            elif fn_name == "load_skill":
                try:    args = json.loads(fn_args_str); name = args.get("name", "")
                except json.JSONDecodeError: name = fn_args_str
                result = execute_load_skill(name)
            else:
                result = json.dumps({"error": f"Unknown: {fn_name}"}, ensure_ascii=False)

            # Build messages for follow-up, don't emit tool UI events
            messages.append({"role":"assistant","content":None,"tool_calls":[{"id":tc["id"],"type":"function","function":{"name":fn_name,"arguments":fn_args_str}}]})
            messages.append({"role":"tool","tool_call_id":tc["id"],"content":result})

        # Continue conversation with tool results
        yield f"data: {json.dumps({'type': 'thinking'}, ensure_ascii=False)}\n\n"

        async for chunk in _continue_chat(messages, model):
            yield chunk


async def _continue_chat(messages: list[dict], model: str):
    """Continue chat after tool results, streaming."""
    url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        content = _sanitize_text(content)
                        yield f"data: {json.dumps({'type': 'text', 'content': content}, ensure_ascii=False)}\n\n"
                except json.JSONDecodeError:
                    continue
