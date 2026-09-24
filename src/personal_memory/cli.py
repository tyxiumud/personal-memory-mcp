"""CLI maintenance is separate from MCP tools to keep file access local and explicit."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import archive
from .models import MemoryInput, Search
from .store import MemoryStore


def default_db() -> str:
    configured = os.environ.get("PERSONAL_MEMORY_DB")
    if configured:
        if not Path(configured).is_absolute():
            raise ValueError("PERSONAL_MEMORY_DB must be an absolute path shared by all clients")
        return configured
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return str(base / "personal-memory-mcp" / "memory.sqlite3")


def main():
    parser = argparse.ArgumentParser(description="Local Personal Memory MCP")
    parser.add_argument("--db", help="SQLite database; defaults to PERSONAL_MEMORY_DB or user data directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run MCP stdio server")
    commands.add_parser("status")
    search = commands.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--scope", choices=["global", "project", "domain"], default="global")
    search.add_argument("--scope-id")
    search.add_argument("--no-global", action="store_true")
    search.add_argument("--as-of")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    search.add_argument(
        "--variant",
        action="append",
        default=[],
        help="Extra keyword query merged with RRF (repeat up to 5 times); lexical, not vector search",
    )
    search.add_argument("--type", choices=["profile", "preference", "fact", "episodic", "decision"])
    search.add_argument(
        "--view",
        choices=["full", "compact"],
        help="Return memory_context output (bounded by --max-chars) instead of a plain list",
    )
    search.add_argument("--max-chars", type=int, default=6000, help="Budget used with --view")
    export = commands.add_parser("export")
    export.add_argument("path", type=Path)
    export.add_argument("--format", choices=["json", "markdown"], default="json")
    export.add_argument("--include-forgotten", action="store_true")
    imp = commands.add_parser("import")
    imp.add_argument("path", type=Path)
    imp.add_argument("--format", choices=["json", "markdown"], default="json")
    note = commands.add_parser("import-note", help="Import an ordinary Markdown/text file as one memory")
    note.add_argument("path", type=Path)
    note.add_argument("--title")
    note.add_argument("--scope", choices=["global", "project", "domain"], default="global")
    note.add_argument("--scope-id")
    note.add_argument(
        "--type", choices=["profile", "preference", "fact", "episodic", "decision"], default="episodic"
    )
    args = parser.parse_args()
    try:
        store = MemoryStore(args.db or default_db())
        if args.command == "serve":
            from .server import create_server

            create_server(store).run(transport="stdio")
            return
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if args.command == "status":
            result = store.status()
        elif args.command == "search":
            selection = Search(
                query=args.query,
                query_variants=args.variant,
                scope=args.scope,
                scope_id=args.scope_id,
                include_global=not args.no_global,
                type=args.type,
                as_of=args.as_of,
                limit=args.limit,
                offset=args.offset,
            )
            result = (
                store.context(selection, args.max_chars, args.view)
                if args.view
                else store.search(selection)
            )
        elif args.command == "export":
            text = archive.encode(store.export_bundle(args.include_forgotten), args.format)
            # Exclusive create prevents accidental overwrite of a backup or database.
            with args.path.open("x", encoding="utf-8") as handle:
                handle.write(text)
            result = {"exported": str(args.path.resolve())}
        elif args.command == "import":
            result = store.import_bundle(
                archive.decode(args.path.read_text(encoding="utf-8-sig"), args.format)
            )
        else:
            result = store.store(
                MemoryInput(
                    title=args.title or args.path.stem,
                    content=args.path.read_text(encoding="utf-8-sig"),
                    scope=args.scope,
                    scope_id=args.scope_id,
                    type=args.type,
                    source={"kind": "file", "path": str(args.path.resolve())},
                )
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(f"personal-memory: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
