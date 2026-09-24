"""Read-only probes of client config launch commands; never call a model or store a memory."""

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from personal_memory.configuration import project_python, render_template, replacements

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "memory_search",
    "memory_context",
    "memory_store",
    "memory_update",
    "memory_forget",
    "memory_history",
    "memory_status",
}
EXAMPLES = {
    "codex": ROOT / "examples/codex.config.toml",
    "workbuddy": ROOT / "examples/workbuddy.mcp.json",
    "deepseek-harness": ROOT / "examples/deepseek-harness.cordis.yml",
}


def read_launch(client, path, template_values=None):
    text = path.read_text(encoding="utf-8-sig")
    if template_values is not None:
        text = render_template(text, template_values)
    if client == "codex":
        entry = tomllib.loads(text)["mcp_servers"]["personal_memory"]
    elif client == "workbuddy":
        entry = json.loads(text)["mcpServers"]["personal_memory"]
    elif client == "deepseek-harness":
        matches = [
            item
            for patch in yaml.safe_load(text)
            for item in patch.get("insert", [])
            if item.get("name") == "@deepseek-ai/dsh-mcp-client"
            and item.get("config", {}).get("serverName") == "personal_memory"
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one personal_memory DSH entry")
        entry = matches[0]["config"]
        if entry["transport"] != "stdio":
            raise ValueError("This checker supports local stdio only")
    else:
        raise ValueError(f"Unsupported client: {client}")
    return {key: entry[key] for key in ("command", "args", "env", "cwd") if key in entry}


async def inspect_session(client, path, session, expected_database):
    await session.initialize()
    tools = {item.name: item for item in (await session.list_tools()).tools}
    names = set(tools)
    if names != EXPECTED_TOOLS:
        raise ValueError(f"{client}: unexpected tool set")
    search_schema = json.dumps(tools["memory_search"].inputSchema, ensure_ascii=False)
    context_schema = json.dumps(tools["memory_context"].inputSchema, ensure_ascii=False)
    for needle, schema, tool in (
        ("query_variants", search_schema, "memory_search"),
        ("query_variants", context_schema, "memory_context"),
        ("view", context_schema, "memory_context"),
        ("compact", context_schema, "memory_context"),
    ):
        if needle not in schema:
            raise ValueError(f"{client}: {tool} is missing parameter {needle}")
    status_response = await session.call_tool("memory_status", {})
    if status_response.isError:
        raise ValueError(f"{client}: status failed")
    status = json.loads(status_response.content[0].text)
    if Path(status["database"]).resolve() != expected_database.resolve():
        raise ValueError(f"{client}: wrong shared database: {status['database']}")
    if not status.get("query_variants") or status.get("fusion") != "rrf(k=60)":
        raise ValueError(f"{client}: server does not report the v0.2 retrieval features")
    context_response = await session.call_tool(
        "memory_context",
        {
            "selection": {
                "scope": "project",
                "scope_id": "project:personal-memory",
                "include_global": True,
            },
            "max_chars": 20000,
            "view": "compact",
        },
    )
    if context_response.isError:
        raise ValueError(f"{client}: context failed")
    payload = json.loads(context_response.content[0].text)
    memories = payload["memories"]
    if payload.get("view") != "compact":
        raise ValueError(f"{client}: compact view was not honoured")
    if memories and "source_summary" not in memories[0]:
        raise ValueError(f"{client}: compact records lack the source summary")
    return {
        "client": client,
        "config": str(path),
        "probe": "passed",
        "tools": len(names),
        "database": status["database"],
        "total": status["total"],
        "context_ids": sorted(record["id"] for record in memories),
        "v0_2_features": {
            "query_variants": status["query_variants"],
            "fusion": status["fusion"],
            "context_view": status["context_view"],
        },
        "verification_level": "MCP launch and read; not agent UI/model invocation",
    }


async def probe(client, path, expected_database, template_values=None):
    launch = read_launch(client, path, template_values)
    params = StdioServerParameters(**launch)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        return await inspect_session(client, path, session, expected_database)


async def check(paths, expected_database, template_values=None):
    results = []
    for client, path in paths.items():
        result = await asyncio.wait_for(
            probe(client, path, expected_database, template_values), timeout=35
        )
        results.append(result)
    if len({tuple(result["context_ids"]) for result in results}) != 1:
        raise ValueError("Clients did not retrieve the same project context")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Use installed client configs")
    parser.add_argument("--database", type=Path, default=ROOT / "data/memory.sqlite3")
    args = parser.parse_args()
    paths = dict(EXAMPLES)
    if args.installed:
        paths["codex"] = Path.home() / ".codex/config.toml"
        paths["workbuddy"] = Path.home() / ".workbuddy/mcp.json"
        paths["deepseek-harness"] = Path.home() / ".dsh/profiles/web/cordis.patch.yml"
    template_values = None
    if not args.installed:
        template_values = replacements(ROOT, args.database.resolve(), project_python(ROOT))
    try:
        result = asyncio.run(check(paths, args.database, template_values))
    except (ValueError, OSError, TimeoutError) as exc:
        print(f"Connection check failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
