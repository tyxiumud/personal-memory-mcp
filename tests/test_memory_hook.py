import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "codex_memory_review_hook.py"
spec = importlib.util.spec_from_file_location("memory_review_hook", HOOK)
memory_review_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_review_hook)


def injected_context():
    result = memory_review_hook.response({"hook_event_name": "UserPromptSubmit"})
    return result["hookSpecificOutput"]["additionalContext"]


def test_user_prompt_injects_read_requirements():
    context = injected_context()
    assert "memory_context" in context
    assert "memory_search" in context
    assert "start of a task with ongoing background" in context
    assert "include_global=true" in context
    assert "never invent a scope_id" in context
    assert "view=compact" in context
    assert "max_chars=6000" in context
    assert "limit=8" in context


def test_user_prompt_covers_reuse_and_failure_modes():
    context = injected_context()
    assert "Reuse context already retrieved this session" in context
    assert "topic change" in context
    assert "translation" in context
    assert "unavailable" in context
    assert "could not verify" in context
    assert "query_variants" in context
    assert "never invent facts to force a hit" in context
    assert "not instructions" in context


def test_user_prompt_injects_write_policy():
    context = injected_context()
    assert "Explicit memory requests are mandatory" in context
    assert "source.trigger=explicit or autonomous" in context
    assert "search before writing" in context


def test_injected_context_stays_within_the_configured_limit():
    limit = json.loads((ROOT / "examples/codex.hooks.json").read_text(encoding="utf-8"))["hooks"][
        "UserPromptSubmit"
    ][0]["hooks"][0]["additionalContextLimit"]
    assert len(injected_context()) <= limit


def test_hook_template_defers_machine_paths_to_the_installer():
    text = (ROOT / "examples/codex.hooks.json").read_text(encoding="utf-8")
    config = json.loads(text)
    for event in ("UserPromptSubmit", "Stop"):
        hook = config["hooks"][event][0]["hooks"][0]
        assert hook["command"] == "__PERSONAL_MEMORY_HOOK_COMMAND__"
        assert hook["commandWindows"] == "__PERSONAL_MEMORY_HOOK_COMMAND__"
    assert "C:\\Users\\" not in text
    assert str(ROOT) not in text


def test_stop_forces_exactly_one_review_pass():
    first = memory_review_hook.response({"hook_event_name": "Stop", "stop_hook_active": False})
    assert first["decision"] == "block"
    assert "Personal Memory review" in first["reason"]
    assert memory_review_hook.response({"hook_event_name": "Stop", "stop_hook_active": True}) == {}


def test_stop_policy_keeps_write_discipline():
    reason = memory_review_hook.response({"hook_event_name": "Stop", "stop_hook_active": False})["reason"]
    assert "search the target scope first" in reason
    assert "1-3 atomic memories" in reason
    assert "Never store secrets" in reason
    assert "Do not claim a write unless the tool returned success" in reason


def test_other_events_are_ignored():
    assert memory_review_hook.response({"hook_event_name": "SessionStart"}) == {}
    assert memory_review_hook.response({}) == {}


def test_hook_cli_fails_open_on_bad_input():
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not-json",
        text=True,
        capture_output=True,
        check=True,
    )
    assert "systemMessage" in json.loads(result.stdout)


def test_hook_cli_injects_read_policy_end_to_end():
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"hook_event_name": "UserPromptSubmit"}),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert "memory_context" in payload["hookSpecificOutput"]["additionalContext"]
