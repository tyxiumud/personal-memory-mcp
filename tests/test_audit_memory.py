"""The audit script: hard read-only guarantee, no personal text, no drift from the store.

Every case runs on a synthetic database created by the test, so nothing here depends on
the real library or on counts that legitimately change over time.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_memory

from personal_memory.models import MemoryInput, Search, now
from personal_memory.store import MemoryStore

BODY = "用户偏好长期稳健的配置，关注红利与指数基金。"
TITLE = "投资背景与分析偏好"


@pytest.fixture
def populated(tmp_path):
    store = MemoryStore(tmp_path / "审计.sqlite3")
    store.store(
        MemoryInput(title=TITLE, content=BODY, scope="project", scope_id="demo", type="preference")
    )
    store.store(MemoryInput(title="职业背景", content="数字 IC 设计工程师", type="profile"))
    forgotten = store.store(MemoryInput(title="已遗忘", content="稳健配置"))
    store.forget(forgotten["id"], 1)
    store.store(
        MemoryInput(
            title="已过期",
            content="稳健配置",
            valid_from="2020-01-01T00:00:00Z",
            valid_to="2020-06-01T00:00:00Z",
        )
    )
    return store


def read_report(store, queries=("稳健",), selection=None, detail=False):
    db = audit_memory.open_read_only(store.path)
    try:
        return audit_memory.build_report(db, selection or Search(), list(queries), detail)
    finally:
        db.close()


def test_audit_connection_refuses_to_write(populated):
    db = audit_memory.open_read_only(populated.path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE memories SET title='changed'")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM memories")
    finally:
        db.close()
    assert populated.status()["total"] == 4


def test_audit_does_not_modify_the_database_file(populated):
    before = populated.path.read_bytes()
    report = read_report(populated)
    assert report["counts"]["total"] == 4
    assert populated.path.read_bytes() == before


def test_missing_database_fails_loudly_without_a_weaker_fallback(tmp_path):
    with pytest.raises(SystemExit, match="immutable"):
        audit_memory.open_read_only(tmp_path / "absent.sqlite3")


def test_default_output_never_contains_record_text(populated):
    report = read_report(populated)
    blob = json.dumps(report, ensure_ascii=False)
    assert BODY not in blob
    assert TITLE not in blob
    assert "数字 IC 设计工程师" not in blob
    rendered = audit_memory.render_text(report)
    assert BODY not in rendered and TITLE not in rendered
    # No per-record identity either, unless detail was requested.
    for record in populated.search(Search()):
        assert record["id"] not in blob


def test_machine_readable_report_records_audit_time_version_and_queries(populated):
    report = read_report(populated, queries=("稳健", "量子计算"))
    assert report["audit"]["read_only"] is True
    assert report["audit"]["generated_at"]
    assert "code_version" in report["audit"]
    assert report["audit"]["snapshot"]["pragma_query_only"] is True
    assert report["filters"]["scope"] == "global"
    assert [row["query"] for row in report["retrieval"]["queries"]] == ["稳健", "量子计算"]
    assert report["retrieval"]["summary"]["queries"] == 2


def test_counts_separate_active_forgotten_and_expired(populated):
    counters = read_report(populated)["counts"]
    assert counters["total"] == 4
    assert counters["active"] == 2
    assert counters["forgotten"] == 1
    assert counters["expired"] == 1
    assert counters["not_yet_valid"] == 0
    assert counters["as_of"]


def test_scope_distribution_keeps_scope_and_scope_id_separate(populated):
    scopes = read_report(populated)["scopes"]
    entries = {(entry["scope"], entry["scope_id"]): entry for entry in scopes}
    assert set(entries) == {("global", None), ("project", "demo")}
    for entry in scopes:
        assert set(entry) == {
            "scope",
            "scope_id",
            "total_count",
            "active_count",
            "newest_updated_at",
        }
        assert entry["scope"] in {"global", "project", "domain"}


def test_source_coverage_and_compact_retention_are_reported(populated):
    report = read_report(populated)
    coverage = report["source_coverage"]
    assert coverage["records"] == 4
    assert set(coverage["fields"]) == set(audit_memory.TRACKED_SOURCE_KEYS)
    assert coverage["fields"]["trigger"]["present"] == 0
    retention = {entry["field"]: entry for entry in report["compact_retention"]}
    assert retention["verification"]["kept_in_compact"] is True
    assert retention["verification"]["kept_verbatim"] is True
    assert retention["epistemic_status"]["kept_verbatim"] is True
    assert retention["trigger"]["kept_verbatim"] is False


def test_provenance_present_is_counted_per_field(populated):
    populated.store(
        MemoryInput(
            title="带来源",
            content="正文",
            source={"client": "codex", "trigger": "explicit", "verification": "未逐项外部核实"},
        )
    )
    coverage = read_report(populated)["source_coverage"]
    assert coverage["fields"]["client"]["present"] == 1
    assert coverage["fields"]["client"]["missing"] == 4
    assert coverage["fields"]["trigger"]["present"] == 1
    assert coverage["fields"]["verification"]["present"] == 1


def test_detail_reports_positions_not_identities(populated):
    report = read_report(populated, detail=True)
    detail = report["source_coverage"]["detail"]
    assert detail["records_without_trigger"] == ["record-001", "record-002", "record-003", "record-004"]
    for record in populated.search(Search()):
        assert record["id"] not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize(
    "selection",
    [
        Search(query="稳健"),
        Search(query="量子计算"),
        Search(query="投资", scope="project", scope_id="demo"),
        Search(query="稳健", scope="project", scope_id="absent"),
        Search(query="稳健", type="preference"),
        Search(query="稳健", limit=1, offset=1),
        Search(query="***"),
        Search(),
    ],
)
def test_audit_diagnostics_match_the_store_rule(populated, selection):
    """The audit mirrors retrieval on a read-only connection; this keeps the two in step."""
    instant = selection.as_of or now()
    db = audit_memory.open_read_only(populated.path)
    try:
        mine = audit_memory._query_counts(db, selection, selection.query, instant)
    finally:
        db.close()
    theirs = populated.search_detailed(selection)["retrieval"]
    assert mine["scoped_active"] == theirs["scoped_active"]
    assert mine["candidate_pool"] == theirs["candidate_pool"]
    assert mine["reason"] == theirs["reason"]
    assert mine["returned"] == theirs["returned"]


def test_audit_reports_a_scope_whose_records_are_all_forgotten(populated):
    entries = {(entry["scope"], entry["scope_id"]): entry for entry in read_report(populated)["scopes"]}
    assert entries[("global", None)]["total_count"] == 3
    assert entries[("global", None)]["active_count"] == 1


def test_zero_hit_summary_is_ratio_only_and_never_called_recall(populated):
    # "数字" hits the global profile record; the other two match nothing in global scope.
    report = read_report(populated, queries=("数字", "量子计算", "投资偏好"))
    summary = report["retrieval"]["summary"]
    assert summary["queries"] == 3
    assert summary["strict_zero_hits"] == 2
    assert summary["strict_zero_hit_ratio"] == pytest.approx(2 / 3, abs=0.001)
    assert "not a recall figure" in summary["notice"]


def test_zero_hits_distinguish_empty_scope_from_lexical_miss(populated):
    empty_report = read_report(
        populated,
        queries=("稳健",),
        selection=Search(query="稳健", scope="project", scope_id="absent", include_global=False),
    )
    empty = empty_report["retrieval"]["queries"][0]["strict"]
    assert empty["reason"] == "empty_scope"
    assert empty["scoped_active"] == 0

    lexical_report = read_report(populated, queries=("稳健",))
    lexical = lexical_report["retrieval"]["queries"][0]["strict"]
    # Global scope holds one active record, so this is a keyword miss, not an empty scope.
    assert lexical["reason"] == "no_lexical_match"
    assert lexical["scoped_active"] == 1
    assert lexical["candidate_pool"] == 0


def test_cli_json_end_to_end(populated):
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "audit_memory.py"),
            "--db",
            str(populated.path),
            "--format",
            "json",
            "--query",
            "稳健",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["audit"]["read_only"] is True
    assert report["retrieval"]["summary"]["queries"] == 1
    assert report["counts"]["total"] == 4


def test_cli_text_output_is_utf8_and_readable(populated):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_memory.py"), "--db", str(populated.path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "records      : total 4" in completed.stdout
    assert "zero-hit queries" in completed.stdout
