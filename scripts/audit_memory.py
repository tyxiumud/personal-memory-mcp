"""Read-only health audit for a personal-memory database.

Safety contract, because this script is meant to run against a live database that other
clients are using:

* the database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``;
* every statement runs inside one deferred read transaction, so the report is a single
  consistent snapshot instead of several moments stitched together;
* a failed connection is a hard error. There is deliberately no ``immutable=1`` fallback:
  it can silently read a stale copy of a WAL database and report wrong numbers;
* ``MemoryStore`` is never instantiated, because its constructor may create the schema,
  run migrations or change PRAGMAs. Retrieval rules are reused from pure module-level
  functions instead of being copied.

The default output is a summary: counts, coverage and ratios only. Record bodies, titles,
tags, conversation identifiers and per-record ids are never printed, so the output can be
pasted into a report. Local detail (id prefixes) needs ``--detail``. Even so, an audit
output describes one personal database and should stay out of the repository.

No number produced here is a pass/fail fixture: a real database grows, so the audit reports
what it finds and never asserts that a count or a coverage gap stays constant.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from personal_memory.cli import default_db
from personal_memory.models import Search, now
from personal_memory.retrieval import SOURCE_SUMMARY_KEYS, VERBATIM_SOURCE_KEYS
from personal_memory.store import match_expression, scoped_filters

# Provenance fields worth tracking; anything else found in `source` is reported as an
# unknown key rather than being silently ignored.
TRACKED_SOURCE_KEYS = (
    "kind",
    "client",
    "trigger",
    "date",
    "reference",
    "conversation_id",
    "files",
    "evidence",
    "verification",
    "epistemic_status",
    "batch",
    "quote",
    "commit",
)

# Generic question and keyword forms used only to exercise the retrieval path. They probe
# how often a query returns nothing; a zero-hit ratio is NOT a recall figure, because
# no expectations are attached to these queries.
DEFAULT_QUERIES = (
    "职业",
    "投资偏好",
    "投资 偏好",
    "学习",
    "记忆",
    "工作安排",
    "技术背景",
    "我是做什么工作的",
    "我的投资偏好是什么",
    "我平时怎么学习",
)


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open one consistent read transaction; never fall back to a weaker mode."""
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=15)
        db.row_factory = sqlite3.Row
        db.isolation_level = None
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        db.execute("SELECT count(*) FROM memories").fetchone()
    except sqlite3.Error as exc:
        raise SystemExit(
            f"audit: cannot read {path} in read-only mode: {exc}\n"
            "audit: refusing to retry with immutable=1, which can read a stale WAL snapshot"
        ) from exc
    return db


def code_version() -> dict | None:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=15, check=False
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    try:
        commit = run("rev-parse", "--short", "HEAD")
        if not commit:
            return None
        return {"commit": commit, "dirty": bool(run("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return None


def counts(db: sqlite3.Connection, instant: str) -> dict:
    def scalar(sql: str, args: tuple = ()) -> int:
        return db.execute(sql, args).fetchone()[0]

    return {
        "as_of": instant,
        "total": scalar("SELECT count(*) FROM memories"),
        "active": scalar(
            "SELECT count(*) FROM memories WHERE forgotten_at IS NULL AND valid_from<=?"
            " AND (valid_to IS NULL OR valid_to>?)",
            (instant, instant),
        ),
        "forgotten": scalar("SELECT count(*) FROM memories WHERE forgotten_at IS NOT NULL"),
        "not_yet_valid": scalar("SELECT count(*) FROM memories WHERE valid_from>?", (instant,)),
        "expired": scalar(
            "SELECT count(*) FROM memories WHERE valid_to IS NOT NULL AND valid_to<=?", (instant,)
        ),
    }


def scope_distribution(db: sqlite3.Connection, instant: str) -> list[dict]:
    """One entry per (scope, scope_id) pair, with the two fields never concatenated."""
    return [
        {
            "scope": row["scope"],
            "scope_id": row["scope_id"],
            "total_count": row["total_count"],
            "active_count": row["active_count"],
            "newest_updated_at": row["newest_updated_at"],
        }
        for row in db.execute(
            """SELECT scope, scope_id, count(*) AS total_count,
                      sum(CASE WHEN forgotten_at IS NULL AND valid_from<=?
                                AND (valid_to IS NULL OR valid_to>?)
                               THEN 1 ELSE 0 END) AS active_count,
                      max(updated_at) AS newest_updated_at
               FROM memories GROUP BY scope, scope_id ORDER BY scope, scope_id""",
            (instant, instant),
        )
    ]


def source_coverage(db: sqlite3.Connection, detail: bool) -> dict:
    documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM memories ORDER BY rowid")]
    present = dict.fromkeys(TRACKED_SOURCE_KEYS, 0)
    unknown: dict[str, int] = {}
    absent_examples: list[str] = []
    for index, document in enumerate(documents, start=1):
        source = document.get("source") or {}
        for key in source:
            if key in present:
                present[key] += 1
            else:
                unknown[key] = unknown.get(key, 0) + 1
        if detail and not source.get("trigger"):
            absent_examples.append(f"record-{index:03d}")
    total = len(documents)
    return {
        "records": total,
        "fields": {
            key: {
                "present": count,
                "missing": total - count,
                "coverage": round(count / total, 3) if total else None,
            }
            for key, count in present.items()
        },
        # Free-form provenance keys seen in the wild but not tracked above.
        "unknown_keys": dict(sorted(unknown.items())),
        "detail": {"records_without_trigger": absent_examples} if detail else None,
    }


def compact_retention(db: sqlite3.Connection) -> list[dict]:
    """The compact projection contract plus every extra provenance key seen in the data.

    The whitelist is reported even when a field is unused, so the contract is visible
    without reading the code; observed keys outside it are listed as dropped.
    """
    seen: dict[str, int] = {}
    for row in db.execute("SELECT document FROM memories"):
        for key in json.loads(row[0]).get("source") or {}:
            seen[key] = seen.get(key, 0) + 1
    fields = set(SOURCE_SUMMARY_KEYS) | set(VERBATIM_SOURCE_KEYS) | set(seen)
    return [
        {
            "field": key,
            "present_in_source": seen.get(key, 0),
            "kept_in_compact": key in SOURCE_SUMMARY_KEYS,
            "kept_verbatim": key in VERBATIM_SOURCE_KEYS,
        }
        for key in sorted(fields, key=lambda name: (-seen.get(name, 0), name))
    ]


def _query_counts(db: sqlite3.Connection, selection: Search, query: str, instant: str) -> dict:
    """Strict-path diagnostics, mirroring MemoryStore's rules on a read-only connection."""
    clauses, args = scoped_filters(selection, instant)
    scoped_active = db.execute(
        f"SELECT count(*) FROM memories m WHERE {' AND '.join(clauses)}", args
    ).fetchone()[0]
    expression = match_expression(query)
    if query.strip() and not expression:
        candidate_pool = 0
    else:
        join = ""
        match_args = list(args)
        if expression:
            join = "JOIN memory_fts ON memory_fts.rowid=m.rowid"
            clauses = [*clauses, "memory_fts MATCH ?"]
            match_args.append(expression)
        candidate_pool = db.execute(
            f"SELECT count(*) FROM memories m {join} WHERE {' AND '.join(clauses)}", match_args
        ).fetchone()[0]
    if scoped_active == 0:
        reason = "empty_scope"
    elif candidate_pool == 0:
        reason = "no_lexical_match"
    elif selection.offset >= candidate_pool:
        reason = "offset_beyond_pool"
    else:
        reason = "matched"
    returned = max(0, min(selection.limit, candidate_pool - selection.offset))
    return {
        "scoped_active": scoped_active,
        "candidate_pool": candidate_pool,
        "returned": returned,
        "reason": reason,
    }


def query_diagnostics(
    db: sqlite3.Connection, queries: list[str], selection: Search, instant: str
) -> dict:
    rows = []
    for query in queries:
        variants = list(
            dict.fromkeys(
                item.strip() for item in [query, *selection.query_variants] if item.strip()
            )
        )
        entry = {"query": query, "strict": _query_counts(db, selection, query, instant)}
        if selection.query_variants:
            per_query = {
                item: _query_counts(db, selection, item, instant)["candidate_pool"] for item in variants
            }
            entry["with_variants"] = {
                "queries_run": len(variants),
                "per_query_pool": per_query,
                "any_pool": any(per_query.values()),
            }
        rows.append(entry)
    strict_zero = sum(1 for row in rows if row["strict"]["reason"] in ("empty_scope", "no_lexical_match"))
    summary = {
        "queries": len(rows),
        "strict_zero_hits": strict_zero,
        "strict_zero_hit_ratio": round(strict_zero / len(rows), 3) if rows else None,
        "notice": (
            "A zero-hit ratio is not a recall figure. These generic probe queries carry no "
            "expected records, so this measures how often the retrieval path returns nothing."
        ),
    }
    if selection.query_variants:
        with_variants_zero = sum(1 for row in rows if not row["with_variants"]["any_pool"])
        summary["with_variants_zero_hits"] = with_variants_zero
        summary["with_variants_zero_hit_ratio"] = (
            round(with_variants_zero / len(rows), 3) if rows else None
        )
    return {"queries": rows, "summary": summary}


def build_report(db: sqlite3.Connection, selection: Search, queries: list[str], detail: bool) -> dict:
    instant = selection.as_of or now()
    return {
        "audit": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code_version": code_version(),
            "database": str(db.execute("PRAGMA database_list").fetchone()[2]),
            "read_only": True,
            "snapshot": {"isolation": "BEGIN (deferred)", "pragma_query_only": True},
        },
        "filters": {
            "scope": selection.scope,
            "scope_id": selection.scope_id,
            "include_global": selection.include_global,
            "type": selection.type,
            "as_of": selection.as_of,
            "limit": selection.limit,
            "offset": selection.offset,
        },
        "counts": counts(db, instant),
        "scopes": scope_distribution(db, instant),
        "source_coverage": source_coverage(db, detail),
        "compact_retention": compact_retention(db),
        "retrieval": query_diagnostics(db, queries, selection, instant),
    }


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_text(report: dict) -> str:
    lines = ["Personal Memory read-only audit", ""]
    audit, counters = report["audit"], report["counts"]
    lines.append(f"generated_at : {audit['generated_at']}")
    version = audit["code_version"]
    lines.append(
        "code_version : " + ("unavailable" if not version else f"{version['commit']}"
                              f"{' (dirty)' if version['dirty'] else ''}")
    )
    lines.append(f"database     : {audit['database']}")
    lines.append(f"as_of        : {counters['as_of']}")
    lines.append("")
    lines.append(
        f"records      : total {counters['total']} | active {counters['active']} | "
        f"forgotten {counters['forgotten']} | not-yet-valid {counters['not_yet_valid']} | "
        f"expired {counters['expired']}"
    )
    lines.append("")
    lines.append("scopes (scope and scope_id are separate fields):")
    for entry in report["scopes"]:
        identifier = entry["scope_id"] if entry["scope_id"] is not None else "-"
        lines.append(
            f"  scope={entry['scope']:<8} scope_id={identifier:<28} "
            f"total={entry['total_count']:<4} active={entry['active_count']:<4} "
            f"newest={entry['newest_updated_at']}"
        )
    lines.append("")
    coverage = report["source_coverage"]
    lines.append(f"source coverage over {coverage['records']} records:")
    for key, stat in coverage["fields"].items():
        lines.append(
            f"  {key:<18} present {stat['present']:>4}  missing {stat['missing']:>4}  "
            f"coverage {percent(stat['coverage'])}"
        )
    if coverage["unknown_keys"]:
        lines.append(f"  untracked keys: {coverage['unknown_keys']}")
    lines.append("")
    lines.append("compact view provenance retention:")
    for entry in report["compact_retention"]:
        kept = "kept" if entry["kept_in_compact"] else "dropped"
        mode = " (verbatim)" if entry["kept_verbatim"] else ""
        lines.append(
            f"  {entry['field']:<18} present {entry['present_in_source']:>4}  {kept}{mode}"
        )
    lines.append("")
    retrieval = report["retrieval"]
    lines.append("query diagnostics (strict path, generic probe queries):")
    for row in retrieval["queries"]:
        strict = row["strict"]
        suffix = ""
        if "with_variants" in row:
            suffix = f" | variants pool any={row['with_variants']['any_pool']}"
        lines.append(
            f"  {row['query']:<20} scoped={strict['scoped_active']:<4} "
            f"pool={strict['candidate_pool']:<4} returned={strict['returned']:<3} "
            f"reason={strict['reason']}{suffix}"
        )
    summary = retrieval["summary"]
    lines.append("")
    lines.append(
        f"zero-hit queries: {summary['strict_zero_hits']}/{summary['queries']} "
        f"({percent(summary['strict_zero_hit_ratio'])}) on the strict path"
    )
    if "with_variants_zero_hits" in summary:
        lines.append(
            f"                  {summary['with_variants_zero_hits']}/{summary['queries']} "
            f"({percent(summary['with_variants_zero_hit_ratio'])}) with the supplied variants"
        )
    lines.append(f"note: {summary['notice']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--db",
        type=Path,
        help="SQLite database to audit; defaults to PERSONAL_MEMORY_DB or the user data directory",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--scope", choices=["global", "project", "domain"], default="global")
    parser.add_argument("--scope-id")
    parser.add_argument("--no-global", action="store_true", help="exclude global records from a scoped view")
    parser.add_argument("--type", choices=["profile", "preference", "fact", "episodic", "decision"])
    parser.add_argument("--as-of", help="evaluate validity at this instant instead of now")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="probe query, repeatable; defaults to a built-in generic list",
    )
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        help="variant applied to every probe query (max 5), repeatable",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="include per-record positions (never titles, bodies or conversation ids)",
    )
    args = parser.parse_args()

    database = args.db or Path(default_db())
    if not database.exists():
        raise SystemExit(f"audit: database not found: {database}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Fail loudly on invalid combinations instead of silently changing what is measured.
    selection = Search(
        query="",
        query_variants=args.variant,
        scope=args.scope,
        scope_id=args.scope_id,
        include_global=not args.no_global,
        type=args.type,
        as_of=args.as_of,
        limit=args.limit,
        offset=args.offset,
    )
    queries = args.query or list(DEFAULT_QUERIES)

    db = open_read_only(database)
    try:
        report = build_report(db, selection, queries, args.detail)
    finally:
        db.close()

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
