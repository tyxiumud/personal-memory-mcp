import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "personal_memory", *map(str, args)],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=20,
        env=os.environ | {"PYTHONUTF8": "1"},
    )


@pytest.mark.parametrize("format,suffix", [("json", "json"), ("markdown", "md")])
def test_cli_note_export_restore_and_no_overwrite(tmp_path, format, suffix):
    db = tmp_path / "source.sqlite3"
    result = run_cli(
        "--db",
        db,
        "import-note",
        ROOT / "examples/sample-note.md",
        "--scope",
        "project",
        "--scope-id",
        "demo",
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    target = tmp_path / ("backup." + suffix)
    result = run_cli("--db", db, "export", target, "--format", format)
    assert result.returncode == 0, result.stderr
    original = target.read_bytes()
    assert run_cli("--db", db, "export", target, "--format", format).returncode == 1
    assert target.read_bytes() == original
    restored = tmp_path / "restored.sqlite3"
    result = run_cli("--db", restored, "import", target, "--format", format)
    assert result.returncode == 0, result.stderr
    result = run_cli("--db", restored, "search", "SQLite", "--scope", "project", "--scope-id", "demo")
    assert json.loads(result.stdout)[0]["id"] == record["id"]
