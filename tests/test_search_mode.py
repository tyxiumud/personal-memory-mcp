"""search_mode: strict stays the default, auto adds one bounded relaxed pass.

The relaxed pass exists because a phrase whose words are never adjacent in the text
("投资偏好" against "投资背景与分析偏好") makes the strict AND expression return nothing.
It must widen recall without becoming "return everything": the rules that keep it in check
are covered here, together with the guarantee that it never changes a strict hit.
"""

import itertools

import pytest

from personal_memory.models import MemoryInput, Search, now
from personal_memory.retrieval import (
    FALLBACK_CANDIDATES,
    FALLBACK_MAX_FRAGMENTS,
    GENERIC_DF_MIN,
    GENERIC_DF_RATIO,
    MIN_COVERAGE_FRAGMENTS,
    RELAXED_MATCH_QUALITY,
    RELAXED_NOTICE,
    rank_relaxed,
    select_fragments,
)
from personal_memory.store import MemoryStore

PHRASE = "投资偏好"
FRAGMENTS = ["投资", "资偏", "偏好"]
# The middle bigram spans two words that never touch in the text, so it matches nothing.
IMPOSSIBLE_FRAGMENT = "资偏"

# Long questions with the core words 投资偏好 at three different positions. A positional cut
# of the fragment list keeps the leading pleasantries and loses the core words entirely.
LONG_QUESTION_START = "投资偏好请问一下你还记不记得我之前描述过的内容"
LONG_QUESTION_MIDDLE = "请问一下你还记不记得我之前描述过的投资偏好是什么"
LONG_QUESTION_END = "请问一下你还记不记得我之前向你描述过的个人投资偏好是什么"
_batches = itertools.count()


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "模式.sqlite3")


def save(store, title, content, **kwargs):
    return store.store(MemoryInput(title=title, content=content, **kwargs))


def bulk(store, count, title_prefix, content, **overrides):
    # Each call gets its own id range so a test can load several synthetic batches.
    batch = next(_batches)
    records = []
    for index in range(count):
        record = {
            "id": f"bulk-{batch:02d}-{index:04d}",
            "title": f"{title_prefix}{index}",
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


def test_strict_is_the_default_and_unchanged(store):
    record = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健。")
    assert Search(query=PHRASE).search_mode == "strict"
    assert store.search(Search(query=PHRASE)) == []
    assert store.search(Search(query=PHRASE)) == store.search(Search(query=PHRASE, search_mode="strict"))
    assert record["id"] not in {r["id"] for r in store.search(Search(query=PHRASE))}


def test_unknown_search_mode_is_rejected(store):
    with pytest.raises(ValueError):
        Search(query="投资", search_mode="relaxed")


def test_auto_recovers_a_phrase_whose_words_are_not_adjacent(store):
    record = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健，关注红利与指数。")
    strict = store.search_detailed(Search(query=PHRASE))
    assert strict["memories"] == []
    assert strict["retrieval"]["reason"] == "no_lexical_match"

    relaxed = store.search_detailed(Search(query=PHRASE, search_mode="auto"))
    assert [item["id"] for item in relaxed["memories"]] == [record["id"]]
    info = relaxed["retrieval"]
    assert info["reason"] == "relaxed_match"
    assert info["strategy"] == "relaxed"
    assert info["search_mode"] == "auto"
    fallback = info["fallback"]
    assert fallback["attempted"] is True and fallback["applied"] is True
    assert fallback["method"] == "or_fragment_coverage"
    assert fallback["trigger_reason"] == "empty_candidate_pool"
    # The impossible middle bigram is reported rather than silently distorting coverage, and
    # the used fragment list contains only the ones actually searched for.
    assert fallback["dropped_unmatched_fragments"] == [IMPOSSIBLE_FRAGMENT]
    assert fallback["fragments_generated"] == len(FRAGMENTS)
    assert fallback["fragments"] == ["投资", "偏好"]
    assert fallback["dropped_by_cap_fragments"] == []
    assert fallback["notice"] == RELAXED_NOTICE


def test_relaxed_records_are_marked_and_confidence_is_not_rewritten(store):
    record = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健。", confidence=0.42, importance=0.31)
    hit = store.search_detailed(Search(query=PHRASE, search_mode="auto"))["memories"][0]
    assert hit["match_quality"] == RELAXED_MATCH_QUALITY
    assert hit["confidence"] == 0.42 and hit["importance"] == 0.31
    assert store.status()["total"] == 1
    assert store.history(record["id"])[0]["revision"] == 1


def test_auto_never_changes_an_existing_strict_hit(store):
    """The fallback only runs on an empty pool, so it cannot replace or reorder hits."""
    first = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健。")
    second = save(store, "投资笔记", "关注红利与指数基金。")
    strict = store.search_detailed(Search(query="投资", query_variants=["稳健"]))
    relaxed = store.search_detailed(Search(query="投资", query_variants=["稳健"], search_mode="auto"))
    assert [item["id"] for item in strict["memories"]] == [item["id"] for item in relaxed["memories"]]
    assert strict["retrieval"]["reason"] == relaxed["retrieval"]["reason"] == "matched"
    assert relaxed["retrieval"]["fallback"]["attempted"] is False
    assert all("match_quality" not in item for item in relaxed["memories"])
    assert {first["id"], second["id"]} == {item["id"] for item in relaxed["memories"]}


def test_no_fallback_when_pagination_already_passed_the_pool(store):
    for index in range(3):
        save(store, f"投资记录{index}", "投资 稳健")
    result = store.search_detailed(Search(query="投资", search_mode="auto", limit=5, offset=3))
    info = result["retrieval"]
    assert result["memories"] == []
    assert info["reason"] == "offset_beyond_pool"
    assert info["fallback"]["attempted"] is False


def test_no_fallback_when_the_scope_is_empty(store):
    save(store, "投资记录", "投资 稳健", scope="project", scope_id="other")
    info = store.search_detailed(Search(query=PHRASE, search_mode="auto"))["retrieval"]
    assert info["reason"] == "empty_scope"
    assert info["fallback"]["attempted"] is False


def test_fallback_keeps_scope_validity_type_and_forgotten_filters(store):
    live = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健。", type="preference")
    save(store, "其他项目", "投资 偏好", scope="project", scope_id="other", type="preference")
    save(
        store,
        "已过期",
        "投资 偏好",
        type="preference",
        valid_from="2020-01-01T00:00:00Z",
        valid_to="2020-06-01T00:00:00Z",
    )
    gone = save(store, "已遗忘", "投资 偏好", type="preference")
    store.forget(gone["id"], 1)
    save(store, "类型不同", "投资 偏好", type="episodic")

    result = store.search_detailed(Search(query=PHRASE, search_mode="auto", type="preference"))
    assert [item["id"] for item in result["memories"]] == [live["id"]]
    assert result["retrieval"]["fallback"]["applied"] is True


def test_fallback_candidates_are_filtered_by_coverage(store):
    """A candidate matching one fragment is rejected when two are available."""
    weak = save(store, "投资说明", "只提到投资这一个词。")
    strong = save(store, "投资与偏好", "同时提到投资和偏好两个方面。")
    result = store.search_detailed(Search(query=PHRASE, search_mode="auto"))
    info = result["retrieval"]
    assert [item["id"] for item in result["memories"]] == [strong["id"]]
    assert weak["id"] not in {item["id"] for item in result["memories"]}
    assert info["fallback"]["coverage_required"] == MIN_COVERAGE_FRAGMENTS
    assert info["fallback"]["recalled_candidates"] == 2
    assert info["fallback"]["surviving_pool"] == 1
    assert info["fallback"]["candidates_rejected"] == 1


def test_pool_and_pagination_use_the_surviving_pool_not_the_raw_recall(store):
    """3 recalled, 2 rejected, 1 returned: the pool the caller pages over is 1."""
    strong = save(store, "投资与偏好", "同时提到投资和偏好。")
    save(store, "投资说明", "只提到投资。")
    save(store, "投资其他", "只提到投资，别的不提。")
    for index in range(4):
        save(store, f"无关记录{index}", "完全无关的正文内容。")

    result = store.search_detailed(Search(query=PHRASE, search_mode="auto"))
    info = result["retrieval"]
    fallback = info["fallback"]
    assert fallback["recalled_candidates"] == 3
    assert fallback["candidates_rejected"] == 2
    assert fallback["surviving_pool"] == 1
    assert fallback["candidate_limit_reached"] is False
    assert fallback["uninspected_candidates_possible"] is False
    # Top-level pagination must describe the filtered pool, not the raw OR recall.
    assert info["candidate_pool"] == 1
    assert info["returned"] == 1
    assert info["has_more_in_pool"] is False
    assert [item["id"] for item in result["memories"]] == [strong["id"]]


@pytest.mark.parametrize(
    "question",
    [LONG_QUESTION_START, LONG_QUESTION_MIDDLE, LONG_QUESTION_END],
    ids=["core-at-start", "core-in-middle", "core-at-end"],
)
def test_long_question_keeps_the_core_fragments_wherever_they_sit(store, question):
    """The fragment cap must be applied after measuring DF, not positionally.

    The pleasantries match no record at all, so once their document frequency is known they
    are dropped and the core words survive no matter where the question puts them.
    """
    record = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健，关注红利与指数。")
    result = store.search_detailed(Search(query=question, search_mode="auto"))
    assert [item["id"] for item in result["memories"]] == [record["id"]]
    fallback = result["retrieval"]["fallback"]
    assert fallback["applied"] is True
    assert "投资" in fallback["fragments"]
    assert fallback["fragments_generated"] > FALLBACK_MAX_FRAGMENTS
    # The pleasantries are reported as matching nothing rather than silently discarded.
    assert "请问" in fallback["dropped_unmatched_fragments"]


def test_required_long_question_recalls_the_investment_preference(store):
    record = save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健，关注红利与指数。")
    save(store, "职业背景", "数字 IC 设计工程师。")
    question = "请问一下你还记不记得我之前向你描述过的个人投资偏好是什么"
    assert store.search(Search(query=question)) == []
    relaxed = store.search_detailed(Search(query=question, search_mode="auto"))
    assert relaxed["memories"][0]["id"] == record["id"]
    assert relaxed["retrieval"]["reason"] == "relaxed_match"


def test_select_fragments_prefers_low_df_and_reports_the_cap():
    usable = [f"f{index:02d}" for index in range(20)]
    frequencies = {f"f{index:02d}": index + 1 for index in range(20)}  # f00 rarest
    kept, dropped = select_fragments(usable, frequencies)
    assert kept == [f"f{index:02d}" for index in range(FALLBACK_MAX_FRAGMENTS)]
    assert dropped == [f"f{index:02d}" for index in range(FALLBACK_MAX_FRAGMENTS, 20)]


def test_select_fragments_keeps_original_order_for_equal_df():
    usable = ["b", "a", "c"]
    kept, dropped = select_fragments(usable, {"a": 1, "b": 1, "c": 1})
    assert kept == ["b", "a", "c"]
    assert dropped == []


def test_cap_drops_the_most_common_fragments_and_reports_them(store):
    """With more usable fragments than the cap, the commonest are the ones left out.

    A positional cut would have kept the first word (the common one) and dropped the last
    (a rare one), which is the defect this ordering fixes.
    """
    common = "稳健"
    rare = ["投资", "偏好", "红利", "指数", "长期", "配置", "回撤", "短线", "交易", "基金", "风险", "收益"]
    for index in range(5):
        save(store, f"常见记录{index}", common)
    for index, word in enumerate(rare):
        save(store, f"罕见记录{index}", word)
    query = " ".join([common, *rare])
    info = store.search_detailed(Search(query=query, search_mode="auto"))["retrieval"]
    fallback = info["fallback"]
    assert fallback["fragments_generated"] == 1 + len(rare)
    # The common word is not generic (5 of 17 records is below the ratio), but it is the one
    # the cap drops, and the rare words survive.
    assert fallback["dropped_generic_fragments"] == []
    assert fallback["dropped_by_cap_fragments"] == [common]
    assert common not in fallback["fragments"]
    assert rare[-1] in fallback["fragments"]
    assert len(fallback["fragments"]) == FALLBACK_MAX_FRAGMENTS == 1 + len(rare) - 1


def test_relaxed_ties_prefer_newest_updated_at_then_id():
    """Ties must follow the strict path: newest updated_at first, then id ascending."""
    def record(memory_id, updated_at):
        return {
            "id": memory_id,
            "title": "投资偏好",
            "content": "投资与偏好",
            "importance": 0.5,
            "confidence": 0.5,
            "updated_at": updated_at,
        }

    older = record("id-a", "2025-01-01T00:00:00+00:00")
    newer = record("id-b", "2026-06-01T00:00:00+00:00")
    page, stats = rank_relaxed([(older, -1.0), (newer, -1.0)], ["投资", "偏好"], 10, 0)
    assert [item["id"] for item in page] == ["id-b", "id-a"]
    assert stats["surviving_pool"] == 2 and stats["candidates_rejected"] == 0

    # Fully identical metadata: the id is the last resort and keeps the order deterministic.
    same = "2026-01-01T00:00:00+00:00"
    page, _ = rank_relaxed(
        [(record("id-b", same), -1.0), (record("id-a", same), -1.0)], ["投资"], 10, 0
    )
    assert [item["id"] for item in page] == ["id-a", "id-b"]


def test_relaxed_ranking_prefers_coverage_over_recency():
    """Coverage outranks bm25, importance, confidence and recency; both must survive first."""
    older = {
        "id": "id-old",
        "title": "投资偏好",
        "content": "投资与偏好都提到，另外还有红利",
        "importance": 0.5,
        "confidence": 0.5,
        "updated_at": "2025-01-01T00:00:00+00:00",
    }
    newer = {
        "id": "id-new",
        "title": "投资偏好",
        "content": "投资与偏好都提到",
        "importance": 0.9,
        "confidence": 0.9,
        "updated_at": "2026-06-01T00:00:00+00:00",
    }
    page, stats = rank_relaxed([(newer, -9.0), (older, -0.1)], ["投资", "偏好", "红利"], 10, 0)
    assert [item["id"] for item in page] == ["id-old", "id-new"]
    assert stats["surviving_pool"] == 2


def test_coverage_requirement_degrades_when_only_one_fragment_is_usable(store):
    save(store, "只有投资一个词", "投资 说明")
    info = store.search_detailed(Search(query=PHRASE, search_mode="auto"))["retrieval"]
    assert info["fallback"]["coverage_required"] == 1
    assert info["returned"] == 1


def test_generic_fragments_are_dropped_and_reported(store):
    """A fragment present in most scoped records cannot discriminate, so it is dropped."""
    bulk(store, 8, "稳健记录", "稳健配置 说明")
    save(store, "无关记录", "完全无关的正文")
    info = store.search_detailed(Search(query="稳健 投资", search_mode="auto"))["retrieval"]
    fallback = info["fallback"]
    assert fallback["attempted"] is True
    assert fallback["dropped_generic_fragments"] == ["稳健"]
    # "投资" matches nothing, "稳健" was dropped as generic: no usable fragment remains.
    assert fallback["dropped_unmatched_fragments"] == ["投资"]
    assert fallback["applied"] is False
    assert info["returned"] == 0
    assert info["reason"] == "no_lexical_match"
    assert info["fallback"]["notice"] is None


def test_query_made_only_of_generic_fragments_returns_nothing(store):
    # The body deliberately does not contain the query as a substring, so strict finds
    # nothing and the fallback is what decides the outcome.
    bulk(store, 6, "记录", "我的配置很稳健")
    strict = store.search_detailed(Search(query="我的稳健", search_mode="strict"))
    relaxed = store.search_detailed(Search(query="我的稳健", search_mode="auto"))
    assert strict["memories"] == []
    assert relaxed["memories"] == []
    assert relaxed["retrieval"]["fallback"]["attempted"] is True
    assert relaxed["retrieval"]["fallback"]["applied"] is False
    assert sorted(relaxed["retrieval"]["fallback"]["dropped_generic_fragments"]) == ["我的", "稳健"]


def test_generic_rule_needs_both_frequency_and_ratio(store):
    """Below the ratio a common word stays usable; the rule is not just a frequency cut."""
    bulk(store, 4, "记录", "投资 稳健")
    bulk(store, 40, "其他", "无关正文")
    info = store.search_detailed(Search(query="稳健 投资", search_mode="auto"))["retrieval"]
    fallback = info["fallback"]
    assert "稳健" not in fallback["dropped_generic_fragments"]
    assert info["returned"] > 0
    assert GENERIC_DF_MIN <= 4 and 4 / 44 < GENERIC_DF_RATIO


def test_fallback_candidate_pool_is_capped_and_reported_as_a_floor(store):
    bulk(store, 120, "无关记录", "无关正文")
    bulk(store, 55, "投资记录", "投资 说明")
    info = store.search_detailed(Search(query=PHRASE, search_mode="auto"))["retrieval"]
    fallback = info["fallback"]
    assert fallback["applied"] is True
    assert fallback["candidate_limit_reached"] is True
    assert fallback["recalled_candidates"] == FALLBACK_CANDIDATES
    assert fallback["surviving_pool"] == FALLBACK_CANDIDATES
    # Hitting the recall cap means candidates were never inspected, so the pool is a floor.
    assert fallback["uninspected_candidates_possible"] is True
    assert info["candidate_pool"] == FALLBACK_CANDIDATES
    assert info["total_matches"] is None
    assert info["has_more_in_pool"] is True


def test_relaxed_ranking_prefers_fragment_coverage(store):
    single = save(store, "投资说明", "只提到投资。")
    both = save(store, "投资偏好说明", "投资与偏好都提到，另外还有稳健。")
    save(store, "投资偏好稳健", "投资、偏好与稳健都有。")
    result = store.search_detailed(Search(query=PHRASE, search_mode="auto", limit=5))
    ids = [item["id"] for item in result["memories"]]
    assert single["id"] not in ids
    assert ids[0] in {item for item in ids}
    assert both["id"] in ids


def test_auto_with_variants_does_not_regress_the_variant_path(store):
    save(store, "投资背景与分析偏好", "长期稳健、红利与指数。")
    save(store, "无关记录", "另一件完全无关的事情。")
    strict = store.search_detailed(Search(query="投资", query_variants=["稳健", "红利"]))
    relaxed = store.search_detailed(
        Search(query="投资", query_variants=["稳健", "红利"], search_mode="auto")
    )
    assert strict["memories"] == relaxed["memories"]
    assert relaxed["retrieval"]["fallback"]["applied"] is False
    assert relaxed["retrieval"]["variants_used"] == 3


def test_context_reports_relaxed_quality_without_touching_the_budget_fields(store):
    save(store, "投资背景与分析偏好", "用户投资风格偏长期稳健。")
    result = store.context(Search(query=PHRASE, search_mode="auto"), 6000, "compact")
    assert result["retrieval"]["reason"] == "relaxed_match"
    assert result["memories"][0]["match_quality"] == RELAXED_MATCH_QUALITY
    assert result["returned_after_budget"] == 1
    assert result["omitted_from_page"] == 0
