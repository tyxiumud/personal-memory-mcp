"""Merge Personal Memory hooks without replacing other clients' hook configuration."""

import copy
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "codex.hooks.json"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
TARGET = CODEX_HOME / "hooks.json"
HOOK_SOURCE = ROOT / "scripts" / "codex_memory_review_hook.py"
HOOK_TARGET = TARGET.parent / "hooks" / "codex_memory_review_hook.py"
BACKUP_DIR = ROOT / "outputs" / "backup"
OWN_SCRIPT = re.compile(r"(?<![\w.-])codex_memory_review_hook\.py(?![\w.-])")
COMMAND_TOKEN = "__PERSONAL_MEMORY_HOOK_COMMAND__"


def build_hook_command(
    *,
    platform: str = sys.platform,
    hook_target: Path | None = None,
    python_executable: Path | None = None,
    windows_dir: Path | None = None,
) -> str:
    """Build the command for the current host without embedding repository paths."""
    hook_target = hook_target or HOOK_TARGET
    if platform == "win32":
        base = windows_dir or Path(os.environ.get("WINDIR", r"C:\Windows"))
        launcher = base / "py.exe"
        if windows_dir is None:
            discovered = shutil.which("py")
            if discovered:
                launcher = Path(discovered)
            elif not launcher.exists():
                raise RuntimeError("Windows Python Launcher (py.exe) is required for Codex hooks")
        parts = (str(launcher), "-3", str(hook_target))
        if any(any(character.isspace() for character in part) for part in parts):
            raise RuntimeError(
                "Codex hook paths containing whitespace are not supported by this Windows installer"
            )
        return " ".join(parts)
    executable = python_executable or Path(sys.executable)
    return shlex.join((str(executable), str(hook_target)))


def render_hooks(template: dict, command: str) -> dict:
    """Replace the public template marker with one locally valid hook command."""
    rendered = copy.deepcopy(template)
    found = 0
    for groups in rendered.get("hooks", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                for key in ("command", "commandWindows"):
                    if hook.get(key) == COMMAND_TOKEN:
                        hook[key] = command
                        found += 1
    if found == 0:
        raise ValueError(f"hook template does not contain {COMMAND_TOKEN}")
    return rendered


def is_memory_hook(hook: dict) -> bool:
    return any(
        isinstance(hook.get(key), str) and OWN_SCRIPT.search(hook[key])
        for key in ("command", "commandWindows")
    )


def merge_hooks(installed: dict, template: dict) -> dict:
    """Replace only our script entries, including entries in mixed hook groups."""
    merged = copy.deepcopy(installed)
    events = merged.setdefault("hooks", {})
    for event, groups in list(events.items()):
        kept = []
        for group in groups:
            hooks = group.get("hooks", [])
            remaining = [hook for hook in hooks if not is_memory_hook(hook)]
            if len(remaining) == len(hooks):
                kept.append(group)
            elif remaining:
                kept.append(group | {"hooks": remaining})
        if kept or not groups:
            events[event] = kept
        else:
            del events[event]
    for event, groups in template["hooks"].items():
        events.setdefault(event, []).extend(copy.deepcopy(groups))
    # A user-level description and all other top-level settings belong to the user.
    return merged


def install_runtime_hook() -> bool:
    """Deploy the stdlib-only hook under an ASCII-safe Codex-owned path."""
    payload = HOOK_SOURCE.read_bytes()
    if HOOK_TARGET.exists() and HOOK_TARGET.read_bytes() == payload:
        return False
    HOOK_TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=HOOK_TARGET.parent, prefix=".memory-hook-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, HOOK_TARGET)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main() -> int:
    if not TARGET.exists():
        print(f"missing installed config: {TARGET}", file=sys.stderr)
        return 1
    installed = json.loads(TARGET.read_text(encoding="utf-8-sig"))
    template = render_hooks(
        json.loads(SOURCE.read_text(encoding="utf-8-sig")),
        build_hook_command(),
    )
    merged = merge_hooks(installed, template)
    script_changed = install_runtime_hook()
    if merged == installed:
        if script_changed:
            print(f"Personal Memory hook runtime updated: {HOOK_TARGET}")
            return 0
        print("Personal Memory hooks are already up to date; other hooks are unchanged.")
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    backup = BACKUP_DIR / f"codex.hooks.{stamp}.bak.json"
    shutil.copy2(TARGET, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=TARGET.parent, prefix=".hooks-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, TARGET)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print("Personal Memory hooks merged; other hooks and settings preserved.")
    print("runtime hook:", HOOK_TARGET)
    print("backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
