"""Render portable client config templates with local absolute paths."""

import argparse
from pathlib import Path

from personal_memory.configuration import default_database, render_template, replacements, running_python

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    "codex.config.toml",
    "workbuddy.mcp.json",
    "deepseek-harness.cordis.yml",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="New or empty directory for rendered configs")
    parser.add_argument("--database", type=Path, default=default_database())
    parser.add_argument("--python", type=Path, default=running_python())
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output_dir must be new or empty")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    values = replacements(args.project_root, args.database, args.python)
    for name in TEMPLATES:
        source = ROOT / "examples" / name
        target = args.output_dir / name
        target.write_text(render_template(source.read_text(encoding="utf-8-sig"), values), encoding="utf-8")
        print(target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
