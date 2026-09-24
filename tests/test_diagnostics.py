"""Retrieval diagnostics and the scope inventory.

The diagnostics must never let a caller read "no hit" as "nothing was ever recorded",
so every reason is exercised: empty scope, lexical miss, pagination past the pool.
"""

import pytest

from personal_memory.models import MemoryInput, Search, now
from personal_memory.retrieval import CANDIDATES_PER_QUERY
from personal_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "诊断.sqlite3")


def save(store, title, content, **kwargs):
    return store.store(MemoryInput(title=title, content=content, **kwargs))


def bulk(store, count, content="共同关键词 稳健配置", **overrides):
    """Load many records in one transaction so candidate-cap cases stay fast."""
    records = []
    for index in range(count):
        record = {
            "id": f"bulk-{index:04d}",
            "title": f"批量记录 {index}",
            "content": content,
            "scope": "global",
            "scope_id": None,
            "type": "fact",
            "valid_from": now(),
            "valid_to": None,
            "confidence": 0.5,
            "importance": 0.5,
            "source": {"kind": "synthetic_fixture"},
            "tags": [],
        }
        record.update(overrides)
        records.append(record)
    store.import_bundle(
        {
            "format": "personal-memory-mcp",
            "version": 1,
            "exported_at": now(),
            "memories": records,
            "history": [],
        }
    )


REQUIRED_KEYS = {
    "strategy",
    "variants_used",
    "scoped_active",
    "total_matches",
    "per_query_matches",
    "candidate_pool",
    "candidate_limit_reached",
    "returned",
    "offset",
    "limit",
    "has_more_in_pool",
    "reason",
}


def test_diagnostics_always_carry_the_documented_keys(store):
    save(store, "记录", "正文内容")
    result = store.search_detailed(Search(query="正文"))
    assert set(result) == {"memories", "retrieval"}
    assert REQUIRED_KEYS <= set(result["retrieval"])


def test_matched_reason(store):
    record = save(store, "投资偏好", "长期稳健、红利与指数")
    result = store.search_detailed(Search(query="稳健"))
    assert result["memories"][0]["id"] == record["id"]
    info = result["retrieval"]
    assert info["reason"] == "matched"
    assert info["strategy"] == "strict"
    assert info["variants_used"] == 0
    assert info["scoped_active"] == 1
    assert info["total_matches"] == 1
    assert info["candidate_pool"] == 1
    assert info["returned"] == 1
    assert info["has_more_in_pool"] is False
    assert info["candidate_limit_reached"] is False


def test_empty_database_reports_empty_scope(store):
    info = store.search_detailed(Search(query="稳健"))["retrieval"]
    assert info["reason"] == "empty_scope"
    assert info["scoped_active"] == 0
    assert info["candidate_pool"] == 0


def test_scope_isolation_reports_empty_scope_not_a_lexical_miss(store):
    """A record in another project must not look like a failed keyword match."""
    save(store, "其他项目", "长期稳健、红利与指数", scope="project", scope_id="other")
    global_view = store.search_detailed(Search(query="稳健"))["retrieval"]
    assert global_view["reason"] == "empty_scope"
    assert global_view["scoped_active"] == 0
    scoped = store.search_detailed(Search(query="稳健", scope="project", scope_id="other"))["retrieval"]
    assert scoped["reason"] == "matched"
    assert scoped["scoped_active"] == 1


def test_lexical_miss_is_distinct_from_an_empty_scope(store):
    save(store, "记录", "另一件完全无关的事情")
    info = store.search_detailed(Search(query="量子计算"))["retrieval"]
    assert info["reason"] == "no_lexical_match"
    assert info["scoped_active"] == 1
    assert info["candidate_pool"] == 0
    assert info["returned"] == 0


def test_validity_window_is_reported_as_empty_scope(store):
    save(store, "已过期", "长期稳健", valid_from="2020-01-01T00:00:00Z", valid_to="2020-06-01T00:00:00Z")
    save(store, "未来生效", "长期稳健", valid_from="2099-01-01T00:00:00Z")
    assert store.search_detailed(Search(query="稳健"))["retrieval"]["reason"] == "empty_scope"
    past = store.search_detailed(Search(query="稳健", as_of="2020-03-01T00:00:00Z"))["retrieval"]
    assert past["reason"] == "matched"


def test_forgotten_records_report_empty_scope(store):
    record = save(store, "记录", "长期稳健")
    store.forget(record["id"], 1)
    info = store.search_detailed(Search(query="稳健"))["retrieval"]
    assert info["reason"] == "empty_scope"
    assert info["scoped_active"] == 0


def test_type_filter_is_reported_as_empty_scope(store):
    save(store, "记录", "长期稳健", type="fact")
    info = store.search_detailed(Search(query="稳健", type="preference"))["retrieval"]
    assert info["reason"] == "empty_scope"


def test_empty_query_browse_reports_browse_strategy(store):
    save(store, "甲", "内容甲")
    save(store, "乙", "内容乙")
    result = store.search_detailed(Search())
    info = result["retrieval"]
    assert info["strategy"] == "browse"
    assert info["reason"] == "matched"
    assert len(result["memories"]) == len(info["scoped_active"] * [0]) == 2
    assert info["candidate_pool"] == 2
    assert info["total_matches"] == 2


def test_pagination_past_the_pool_is_reported(store):
    for index in range(3):
        save(store, f"记录{index}", "稳健配置")
    result = store.search_detailed(Search(query="稳健", limit=5, offset=3))
    info = result["retrieval"]
    assert result["memories"] == []
    assert info["reason"] == "offset_beyond_pool"
    assert info["scoped_active"] == 3
    assert info["candidate_pool"] == 3
    assert info["returned"] == 0
    assert info["has_more_in_pool"] is False
    assert info["total_matches"] == 3


def test_has_more_in_pool_reports_the_remaining_page(store):
    for index in range(5):
        save(store, f"记录{index}", "稳健配置")
    info = store.search_detailed(Search(query="稳健", limit=2))["retrieval"]
    assert info["returned"] == 2
    assert info["has_more_in_pool"] is True
    last = store.search_detailed(Search(query="稳健", limit=2, offset=4))["retrieval"]
    assert last["returned"] == 1
    assert last["has_more_in_pool"] is False


def test_strict_pool_is_exact_and_never_claims_a_cap(store):
    bulk(store, CANDIDATES_PER_QUERY + 50)
    info = store.search_detailed(Search(query="稳健配置"))["retrieval"]
    assert info["scoped_active"] == CANDIDATES_PER_QUERY + 50
    assert info["total_matches"] == CANDIDATES_PER_QUERY + 50
    assert info["candidate_pool"] == CANDIDATES_PER_QUERY + 50
    assert info["candidate_limit_reached"] is False
    assert info["returned"] == 20


def test_capped_pool_is_reported_as_a_floor_not_a_total(store):
    total = CANDIDATES_PER_QUERY + 50
    bulk(store, total)
    info = store.search_detailed(Search(query="稳健配置", query_variants=["配置"]))["retrieval"]
    assert info["strategy"] == "rrf_variants"
    assert info["candidate_limit_reached"] is True
    assert info["candidate_pool"] == CANDIDATES_PER_QUERY
    assert info["per_query_matches"] == [total, total]
    # The union of capped lists has no exact size, so no total is invented.
    assert info["total_matches"] is None
    assert info["variants_used"] == 2


def test_variants_deduplicate_and_report_how_many_ran(store):
    save(store, "记录", "稳健配置")
    info = store.search_detailed(Search(query="稳健", query_variants=[" 稳健 ", "稳健", "配置"]))["retrieval"]
    assert info["variants_used"] == 2
    assert info["per_query_matches"] == [1, 1]
    assert info["strategy"] == "rrf_variants"


def test_variants_keep_scope_filters_and_report_empty_scope(store):
    save(store, "其他项目", "稳健配置", scope="project", scope_id="other")
    info = store.search_detailed(Search(query="稳健", query_variants=["配置"]))["retrieval"]
    assert info["reason"] == "empty_scope"
    assert info["scoped_active"] == 0


def test_context_separates_budget_omission_from_a_retrieval_miss(store):
    skipped = save(store, "小记录", "短")
    huge = save(store, "大记录", "长正文" * 400)
    result = store.context(Search(), 800, "compact")
    ids = [record["id"] for record in result["memories"]]
    assert skipped["id"] in ids and huge["id"] not in ids
    # The dropped record is a budget decision, never a retrieval failure.
    assert result["retrieval"]["reason"] == "matched"
    assert result["retrieval"]["returned"] == 2
    assert result["returned_after_budget"] == 1
    assert result["omitted_from_page"] == 1


def test_context_on_an_empty_scope_reports_it(store):
    result = store.context(Search(query="稳健"), 6000, "compact")
    assert result["memories"] == []
    assert result["retrieval"]["reason"] == "empty_scope"
    assert result["returned_after_budget"] == 0
    assert result["omitted_from_page"] == 0


def test_context_keeps_retrieval_counts_identical_to_search(store):
    for index in range(4):
        save(store, f"记录{index}", "稳健配置")
    selection = Search(query="稳健", limit=3)
    context = store.context(selection, 100_000, "full")
    assert context["retrieval"] == store.search_detailed(selection)["retrieval"]
    assert context["returned_after_budget"] == len(context["memories"]) == 3


def test_scope_inventory_reports_separate_fields(store):
    save(store, "全局记录", "正文")
    save(store, "项目记录", "正文", scope="project", scope_id="project:personal-memory")
    save(store, "另一项目", "正文", scope="project", scope_id="other")
    save(store, "领域记录", "正文", scope="domain", scope_id="learning")
    status = store.status()
    entries = {
        (entry["scope"], entry["scope_id"]): entry
        for entry in status["scopes"]
    }
    assert set(entries) == {
        ("global", None),
        ("project", "project:personal-memory"),
        ("project", "other"),
        ("domain", "learning"),
    }
    global_entry = entries[("global", None)]
    assert set(global_entry) == {
        "scope",
        "scope_id",
        "total_count",
        "active_count",
        "newest_updated_at",
    }
    assert global_entry["total_count"] == 1 and global_entry["active_count"] == 1
    assert global_entry["newest_updated_at"]
    # scope and scope_id stay separate; no "project:project:personal-memory" strings.
    assert all(":" not in entry["scope"] for entry in status["scopes"])
    assert "as_of" in status


def test_scope_inventory_counts_only_current_records_as_active(store):
    save(store, "当前", "正文")
    save(store, "已过期", "正文", valid_from="2020-01-01T00:00:00Z", valid_to="2020-06-01T00:00:00Z")
    save(store, "未来", "正文", valid_from="2099-01-01T00:00:00Z")
    forgotten = save(store, "已遗忘", "正文")
    store.forget(forgotten["id"], 1)
    entry = next(
        item for item in store.status()["scopes"] if item["scope"] == "global" and item["scope_id"] is None
    )
    assert entry["total_count"] == 4
    assert entry["active_count"] == 1


def test_scope_inventory_keeps_a_scope_whose_records_are_all_forgotten(store):
    record = save(store, "已遗忘", "正文", scope="project", scope_id="archived")
    store.forget(record["id"], 1)
    entry = next(item for item in store.status()["scopes"] if item["scope_id"] == "archived")
    assert entry["total_count"] == 1
    assert entry["active_count"] == 0
    assert store.search_detailed(Search(scope="project", scope_id="archived"))["retrieval"]["reason"] == (
        "empty_scope"
    )
