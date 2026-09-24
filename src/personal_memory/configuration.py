"""Portable rendering helpers for checked-in client configuration templates."""

import os
import sys
from pathlib import Path

PYTHON_TOKEN = "__PERSONAL_MEMORY_PYTHON__"
DATABASE_TOKEN = "__PERSONAL_MEMORY_DATABASE__"
PROJECT_TOKEN = "__PERSONAL_MEMORY_PROJECT_ROOT__"
TOKENS = (PYTHON_TOKEN, DATABASE_TOKEN, PROJECT_TOKEN)


def portable_path(path: Path) -> str:
    """Return an absolute path that is safe in JSON, TOML and YAML string templates."""
    return path.resolve().as_posix()


def project_python(root: Path) -> Path:
    relative = Path(".venv/Scripts/python.exe") if os.name == "nt" else Path(".venv/bin/python")
    return (root / relative).resolve()


def default_database() -> Path:
    configured = os.environ.get("PERSONAL_MEMORY_DB")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("PERSONAL_MEMORY_DB must be an absolute path shared by all clients")
        return path.resolve()
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return (base / "personal-memory-mcp" / "memory.sqlite3").resolve()


def replacements(root: Path, database: Path, python: Path | None = None) -> dict[str, str]:
    values = {"project root": root, "database": database, "python": python or project_python(root)}
    for label, path in values.items():
        if not path.is_absolute():
            raise ValueError(f"{label} must be an absolute path: {path}")
    return {
        PROJECT_TOKEN: portable_path(values["project root"]),
        DATABASE_TOKEN: portable_path(values["database"]),
        PYTHON_TOKEN: portable_path(values["python"]),
    }


def render_template(text: str, values: dict[str, str]) -> str:
    rendered = text
    for token in TOKENS:
        if token not in values:
            raise ValueError(f"missing template replacement: {token}")
        rendered = rendered.replace(token, values[token])
    unresolved = [token for token in TOKENS if token in rendered]
    if unresolved:
        raise ValueError(f"unresolved template tokens: {', '.join(unresolved)}")
    return rendered


def running_python() -> Path:
    return Path(sys.executable).resolve()
