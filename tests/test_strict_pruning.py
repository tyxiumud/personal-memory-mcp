"""Narrow, explicitly marked pruning of impossible middle Chinese bigrams."""

import pytest

from personal_memory.models import MemoryInput, Search
from personal_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "strict-pruning.sqlite3")


def save(store, title, content, **kwargs):
    return store.store(MemoryInput(title=title, content=content, **kwargs))


def test_short_compound_prunes_only_the_zero_frequency_middle(store):
    record = save(store, "投资背景与分析偏好", "长期稳健，关注红利。")
    result = store.search_detailed(Search(query="投资偏好"))
    assert [item["id"] for item in result["memories"]] == [record["id"]]
    assert result["memories"][0]["match_quality"] == "pruned"
    info = result["retrieval"]
    assert info["strategy"] == "strict_pruned"
    assert info["strict_dropped_fragments"] == ["资偏"]
    assert info["total_matches"] == info["candidate_pool"] == 1
    assert info["reason"] == "matched"
    assert info["fallback"]["attempted"] is False

    compact = store.context(Search(query="投资偏好"), view="compact")
    assert compact["memories"][0]["match_quality"] == "pruned"


def test_existing_exact_hit_is_not_pruned(store):
    record = save(store, "投资偏好", "用户偏好长期配置。")
    result = store.search_detailed(Search(query="投资偏好"))
    assert [item["id"] for item in result["memories"]] == [record["id"]]
    assert "match_quality" not in result["memories"][0]
    assert result["retrieval"]["strategy"] == "strict"
    assert result["retrieval"]["strict_dropped_fragments"] == []


@pytest.mark.parametrize("query", ["投资未知", "量子计算", "我之前跟你说过我的投资账户密码吗"])
def test_missing_edge_or_long_question_stays_strict(store, query):
    save(store, "投资背景与分析偏好", "长期稳健。")
    result = store.search_detailed(Search(query=query))
    assert result["memories"] == []
    assert result["retrieval"]["strategy"] == "strict"
    assert result["retrieval"]["strict_dropped_fragments"] == []
    assert result["retrieval"]["reason"] == "no_lexical_match"


def test_pruning_respects_scope_type_validity_and_forget(store):
    live = save(store, "投资背景与分析偏好", "长期稳健。", scope="project", scope_id="one", type="preference")
    save(store, "投资背景与分析偏好", "另一项目。", scope="project", scope_id="two", type="preference")
    save(store, "投资背景与分析偏好", "类型不同。", scope="project", scope_id="one", type="fact")
    save(
        store,
        "投资背景与分析偏好",
        "已过期。",
        scope="project",
        scope_id="one",
        type="preference",
        valid_from="2020-01-01T00:00:00Z",
        valid_to="2020-06-01T00:00:00Z",
    )
    gone = save(store, "投资背景与分析偏好", "已遗忘。", scope="project", scope_id="one", type="preference")
    store.forget(gone["id"], 1)

    result = store.search_detailed(
        Search(query="投资偏好", scope="project", scope_id="one", include_global=False, type="preference")
    )
    assert [item["id"] for item in result["memories"]] == [live["id"]]
    assert result["retrieval"]["scoped_active"] == 1


def test_pruned_query_keeps_exact_pool_and_pagination(store):
    for index in range(3):
        save(store, f"投资背景与分析偏好 {index}", "长期稳健。")
    for index in range(5):
        save(store, f"无关记录 {index}", "完全不同的内容。")
    first = store.search_detailed(Search(query="投资偏好", limit=2))
    second = store.search_detailed(Search(query="投资偏好", limit=2, offset=2))
    assert first["retrieval"]["candidate_pool"] == second["retrieval"]["candidate_pool"] == 3
    assert first["retrieval"]["has_more_in_pool"] is True
    assert second["retrieval"]["has_more_in_pool"] is False
    assert len(first["memories"]) == 2 and len(second["memories"]) == 1
    assert {item["id"] for item in first["memories"]}.isdisjoint(
        {item["id"] for item in second["memories"]}
    )


def test_variants_report_pruning_without_changing_fusion_contract(store):
    record = save(store, "投资背景与分析偏好", "长期稳健。")
    result = store.search_detailed(Search(query="投资偏好", query_variants=["稳健"]))
    assert result["memories"][0]["id"] == record["id"]
    assert result["retrieval"]["strategy"] == "rrf_variants"
    assert result["retrieval"]["strict_dropped_fragments"] == ["资偏"]
    assert result["retrieval"]["per_query_matches"] == [1, 1]


def test_ascii_and_single_character_keep_existing_behavior(store):
    save(store, "投资背景与分析偏好", "local scripting tools")
    for query in ("local scripting", "投"):
        info = store.search_detailed(Search(query=query))["retrieval"]
        assert info["strategy"] == "strict"
        assert info["strict_dropped_fragments"] == []
