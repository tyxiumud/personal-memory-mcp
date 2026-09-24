"""Verify installation against temporary configs, never the user's real hooks."""

import copy
import importlib.util
import json
from pathlib import Path, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hook_installer", ROOT / "scripts/install_codex_hooks.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def template():
    return json.loads((ROOT / "examples/codex.hooks.json").read_text(encoding="utf-8"))


def test_public_template_has_no_user_specific_hook_path():
    text = (ROOT / "examples/codex.hooks.json").read_text(encoding="utf-8")
    assert installer.COMMAND_TOKEN in text
    assert "C:\\Users\\" not in text
    assert "F:\\" not in text


def test_windows_command_is_generated_from_local_codex_home():
    command = installer.build_hook_command(
        platform="win32",
        hook_target=PureWindowsPath("C:/Users/example/.codex/hooks/codex_memory_review_hook.py"),
        windows_dir=PureWindowsPath("C:/Windows"),
    )
    assert command == (
        "C:\\Windows\\py.exe -3 "
        "C:\\Users\\example\\.codex\\hooks\\codex_memory_review_hook.py"
    )


def test_merge_preserves_unrelated_events_mixed_groups_and_settings(template):
    template = installer.render_hooks(template, "python C:/hooks/codex_memory_review_hook.py")
    other = {"type": "command", "command": "python other_plugin.py", "timeout": 9}
    similar = {"type": "command", "command": "python other_codex_memory_review_hook.py"}
    old = {"type": "command", "commandWindows": 'python "C:/old/codex_memory_review_hook.py"'}
    installed = {
        "description": "User-owned description",
        "customSetting": True,
        "hooks": {
            "SessionStart": [{"hooks": [other]}],
            "SessionEnd": [],
            "UserPromptSubmit": [{"matcher": "*", "custom": 3, "hooks": [old, other, similar]}],
            "Stop": [{"hooks": [old]}],
        },
    }
    before = copy.deepcopy(installed)
    merged = installer.merge_hooks(installed, template)
    assert installed == before
    assert merged["description"] == installed["description"]
    assert merged["customSetting"] is True
    assert merged["hooks"]["SessionStart"] == installed["hooks"]["SessionStart"]
    assert merged["hooks"]["SessionEnd"] == []
    assert merged["hooks"]["UserPromptSubmit"][0] == {
        "matcher": "*", "custom": 3, "hooks": [other, similar]
    }
    assert merged["hooks"]["UserPromptSubmit"][1:] == template["hooks"]["UserPromptSubmit"]
    assert merged["hooks"]["Stop"] == template["hooks"]["Stop"]
    assert installer.merge_hooks(merged, template) == merged


@pytest.fixture
def config_paths(tmp_path, monkeypatch, template):
    target = tmp_path / "hooks.json"
    source = tmp_path / "template.json"
    backup_dir = tmp_path / "new" / "backups"
    source.write_text(json.dumps(template), encoding="utf-8")
    hook_source = tmp_path / "source_hook.py"
    hook_source.write_text("print('{}')\n", encoding="utf-8")
    hook_target = tmp_path / "codex" / "hooks" / "codex_memory_review_hook.py"
    monkeypatch.setattr(installer, "TARGET", target)
    monkeypatch.setattr(installer, "SOURCE", source)
    monkeypatch.setattr(installer, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(installer, "HOOK_SOURCE", hook_source)
    monkeypatch.setattr(installer, "HOOK_TARGET", hook_target)
    monkeypatch.setattr(installer, "build_hook_command", lambda: "python codex_memory_review_hook.py")
    return target, source, backup_dir


def test_installer_creates_backup_and_is_idempotent(config_paths):
    target, _, backups = config_paths
    original = '{"description": "用户说明", "hooks": {}}'
    target.write_text(original, encoding="utf-8")
    assert installer.main() == 0
    archives = list(backups.glob("*.json"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == original
    first = target.read_bytes()
    assert json.loads(first)["description"] == "用户说明"
    assert installer.main() == 0
    assert target.read_bytes() == first
    assert list(backups.glob("*.json")) == archives


def test_later_install_keeps_previous_backup(config_paths):
    target, source, backups = config_paths
    target.write_text('{"hooks": {}}', encoding="utf-8")
    installer.main()
    first_config = target.read_bytes()
    updated = json.loads(source.read_text(encoding="utf-8"))
    updated["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 12
    source.write_text(json.dumps(updated), encoding="utf-8")
    assert installer.main() == 0
    snapshots = [path.read_bytes() for path in backups.glob("*.json")]
    assert len(snapshots) == 2
    assert first_config in snapshots


def test_failed_replace_preserves_original_and_cleans_temp(config_paths, monkeypatch):
    target, _, backups = config_paths
    original = b'{"hooks": {}}'
    target.write_bytes(original)
    installer.HOOK_TARGET.parent.mkdir(parents=True, exist_ok=True)
    installer.HOOK_TARGET.write_bytes(installer.HOOK_SOURCE.read_bytes())

    def fail_replace(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(installer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        installer.main()
    assert target.read_bytes() == original
    assert next(backups.glob("*.json")).read_bytes() == original
    assert list(target.parent.glob(".hooks-*.tmp")) == []


def test_invalid_template_does_not_modify_installed_config(config_paths):
    target, source, backups = config_paths
    target.write_text('{"hooks": {}}', encoding="utf-8")
    source.write_text("invalid JSON", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        installer.main()
    assert target.read_text(encoding="utf-8") == '{"hooks": {}}'
    assert not backups.exists()
