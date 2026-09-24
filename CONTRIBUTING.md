# Contributing

Thank you for helping improve Personal Memory MCP. The project deliberately favors a small, deterministic and
auditable core over a broad autonomous memory platform.

## Before opening a change

- Discuss changes that alter the data model, MCP tool contract, retrieval semantics, supported clients, or default
  privacy behavior before implementing them.
- Keep runtime data, exports, local configuration, logs and personal evaluation material out of Git.
- Use fictional test records. Do not turn real personal memories into fixtures or bug reports.
- Preserve strict scope, type, validity and soft-delete filtering on every retrieval path.
- Treat relaxed retrieval as candidate discovery and expose it clearly in diagnostics.

## Development setup

```powershell
uv sync --locked
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests scripts
```

On macOS or Linux, use `.venv/bin/python` and `.venv/bin/ruff`.

## Pull requests

Keep changes focused. Explain the user-visible behavior, compatibility impact, privacy implications, and how the
change was verified. Retrieval changes should include before/after results from the fixed synthetic fixture and tests
for false recalls, filtering and diagnostics. Schema changes need an explicit migration and rollback plan.

Do not update generated reports with a different fixture or scoring definition without documenting the change.
