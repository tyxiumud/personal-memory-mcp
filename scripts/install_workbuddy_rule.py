"""Install the WorkBuddy user-level always-apply Personal Memory rule.

Copies examples/workbuddy.RULE.mdc to %USERPROFILE%\\.codebuddy\\rules\\personal-memory\\RULE.mdc
and keeps a timestamped backup of the previous file. No other WorkBuddy file is touched.
"""

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "workbuddy.RULE.mdc"
TARGET = Path.home() / ".codebuddy" / "rules" / "personal-memory" / "RULE.mdc"
BACKUP_DIR = ROOT / "outputs" / "backup"


def main() -> int:
    if not SOURCE.exists():
        print(f"missing template: {SOURCE}", file=sys.stderr)
        return 1
    if TARGET.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup = BACKUP_DIR / f"workbuddy.RULE.{stamp}.bak.mdc"
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TARGET, backup)
        print(f"backup: {backup}")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, TARGET)
    text = TARGET.read_text(encoding="utf-8")
    print(f"installed: {TARGET} ({len(text)} chars)")
    for needle in ("alwaysApply: true", "memory_context", "query_variants", "view=\"compact\"", "source.trigger"):
        print(f"  {needle}: {needle in text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
