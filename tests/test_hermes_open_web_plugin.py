import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / ".hermes-plugins" / "aishperf-open-web" / "__init__.py"


def _module():
    spec = importlib.util.spec_from_file_location("aishperf_open_web_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs


def test_plugin_registers_exactly_three_tools_in_dedicated_toolset() -> None:
    module = _module()
    context = FakeContext()
    module.register(context)

    assert set(context.tools) == {
        "open_web_search", "open_web_preview", "open_web_submit_selection"
    }
    assert {item["toolset"] for item in context.tools.values()} == {
        "aishperf_open_web"
    }


def test_plugin_handler_calls_only_configured_loopback_service(monkeypatch) -> None:
    module = _module()
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(module, "_post", fake_post)
    result = json.loads(module._handle_search({
        "run_id": "20260929-120000-abcdef12",
        "skill": "chip-specs",
        "target_chip": "TestChip X1",
        "queries": [{"query": str(index), "reason": "r"} for index in range(10)],
    }))
    assert result["ok"] is True
    assert calls[0][0] == "/v1/search"
