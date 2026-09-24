"""Portable JSON and readable Markdown archives, with no YAML dependency."""

import json
import re


def encode(bundle: dict, format: str) -> str:
    payload = json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False)
    if format == "json":
        return payload + "\n"
    if format != "markdown":
        raise ValueError("format must be json or markdown")
    # A fence longer than any payload fence permits arbitrary Markdown in memory content.
    fence = "`" * max(3, 1 + max((len(x) for x in re.findall(r"`+", payload)), default=0))
    readable = "\n\n".join(
        "## " + m["title"].replace("\n", " ") + "\n\n" + m["content"] for m in bundle["memories"]
    )
    return (
        "# Personal Memory Archive\n\n"
        "Import uses only the canonical JSON block below; the readable view is informational.\n\n"
        + fence
        + "personal-memory-json\n"
        + payload
        + "\n"
        + fence
        + "\n\n"
        + readable
        + "\n"
    )


def decode(text: str, format: str) -> dict:
    if format == "json":
        return json.loads(text)
    match = re.search(r"(?m)^(`{3,})personal-memory-json\r?\n", text)
    if not match:
        raise ValueError("Not a memory Markdown archive; use import-note for ordinary Markdown")
    end = re.search(r"(?m)^" + re.escape(match[1]) + r"\s*$", text[match.end() :])
    if not end:
        raise ValueError("Unclosed memory archive fence")
    return json.loads(text[match.end() : match.end() + end.start()])
