import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("connection_check", ROOT / "scripts/check_connections.py")
connection_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection_check)


def test_supported_agent_configs_use_one_store():
    launches = [connection_check.read_launch(name, path) for name, path in connection_check.EXAMPLES.items()]
    assert set(connection_check.EXAMPLES) == {"codex", "workbuddy", "deepseek-harness"}
    assert len({item["command"] for item in launches}) == 1
    assert len({tuple(item["args"]) for item in launches}) == 1
    assert len({item["env"]["PERSONAL_MEMORY_DB"] for item in launches}) == 1
    assert {item["command"] for item in launches} == {"__PERSONAL_MEMORY_PYTHON__"}
    assert {item["env"]["PERSONAL_MEMORY_DB"] for item in launches} == {
        "__PERSONAL_MEMORY_DATABASE__"
    }


def test_codex_hooks_cover_prompt_and_stop_review():
    hooks = json.loads((ROOT / "examples/codex.hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "Stop"}
    assert all(group["hooks"][0]["commandWindows"] for groups in hooks.values() for group in groups)


def test_workbuddy_rule_is_always_applied():
    rule = (ROOT / "examples/workbuddy.RULE.mdc").read_text(encoding="utf-8")
    assert "alwaysApply: true" in rule
    assert "source.trigger" in rule
