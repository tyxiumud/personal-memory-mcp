import json
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

from personal_memory.configuration import render_template, replacements

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "examples"


def test_public_templates_contain_only_portable_markers():
    names = ("codex.config.toml", "workbuddy.mcp.json", "deepseek-harness.cordis.yml")
    text = "\n".join((TEMPLATES / name).read_text(encoding="utf-8") for name in names)
    assert "__PERSONAL_MEMORY_PYTHON__" in text
    assert "__PERSONAL_MEMORY_DATABASE__" in text
    assert "__PERSONAL_MEMORY_PROJECT_ROOT__" in text
    assert "C:\\Users\\" not in text
    assert "F:\\" not in text


def test_rendered_templates_are_valid_and_share_one_database(tmp_path):
    database = (tmp_path / "memory.sqlite3").resolve()
    python = Path(sys.executable).resolve()
    values = replacements(ROOT, database, python)
    codex = tomllib.loads(render_template((TEMPLATES / "codex.config.toml").read_text(), values))
    workbuddy = json.loads(render_template((TEMPLATES / "workbuddy.mcp.json").read_text(), values))
    dsh = yaml.safe_load(render_template((TEMPLATES / "deepseek-harness.cordis.yml").read_text(), values))
    entries = (
        codex["mcp_servers"]["personal_memory"],
        workbuddy["mcpServers"]["personal_memory"],
        dsh[0]["insert"][0]["config"],
    )
    assert {entry["command"] for entry in entries} == {python.as_posix()}
    assert {entry["env"]["PERSONAL_MEMORY_DB"] for entry in entries} == {database.as_posix()}


def test_render_script_refuses_to_overwrite_nonempty_directory(tmp_path):
    output = tmp_path / "configs"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/render_client_configs.py"), str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_render_script_creates_three_local_configs(tmp_path):
    output = tmp_path / "configs"
    database = (tmp_path / "db" / "memory.sqlite3").resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/render_client_configs.py"),
            str(output),
            "--database",
            str(database),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        "codex.config.toml",
        "workbuddy.mcp.json",
        "deepseek-harness.cordis.yml",
    }
    assert "__PERSONAL_MEMORY_" not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.iterdir()
    )
