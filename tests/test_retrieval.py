"""Multi-query (query_variants) retrieval: fusion, filters, validation and pagination."""

import json

import pytest

from personal_memory.models import MemoryInput, Search
from personal_memory.retrieval import CANDIDATES_PER_QUERY, RRF_K, fuse
from personal_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "检索.sqlite3")


def save(store, title, content, **kwargs):
    return store.store(MemoryInput(title=title, content=content, **kwargs))


def test_old_single_query_behaviour_is_unchanged(store):
    """A call without query_variants must behave exactly like the previous release."""
    english = save(store, "Preference", "Use Python for tooling")
    chinese = save(store, "编程偏好", "用户喜欢使用中文交流和轻量工具")
    assert store.search(Search(query="python"))[0]["id"] == english["id"]
    assert store.search(Search(query="中文交流"))[0]["id"] == chinese["id"]
    assert store.search(Search(query='" OR 1=1 --')) == []
    assert store.search(Search(query="***")) == []
    plain = store.search(Search(query="中文交流"))
    assert "fusion_score" not in plain[0] and "matched_queries" not in plain[0]


def test_question_form_fails_but_variants_recover_the_record(store):
    """Reproduces the reported defect: the full question matches nothing, keywords do."""
    career = save(
        store,
        "职业背景：虚构的数字 IC 设计工程师",
        "用户是数字 IC 设计工程师，目前从事交换芯片相关工作。",
        type="profile",
    )
    assert store.search(Search(query="我是做什么工作的")) == []
    hits = store.search(Search(query="职业", query_variants=["数字 IC", "工作"]))
    assert hits[0]["id"] == career["id"]
    assert hits[0]["matched_queries"] == [0, 1, 2]
    assert hits[0]["fusion_score"] == pytest.approx(3 / (RRF_K + 1))


def test_investment_style_variants_find_existing_record(store):
    investment = save(
        store,
        "投资背景与分析偏好：长期稳健、红利与指数",
        "用户偏好长期稳健的配置，关注红利与指数基金。",
        type="preference",
    )
    assert store.search(Search(query="我的投资风格是什么")) == []
    hits = store.search(Search(query="投资", query_variants=["稳健", "红利"]))
    assert hits[0]["id"] == investment["id"]


def test_fusion_deduplicates_and_ranks_by_coverage(store):
    both = save(store, "同时命中", "投资 稳健 红利")
    one = save(store, "只命中一路", "投资 相关")
    hits = store.search(Search(query="投资", query_variants=["稳健"]))
    assert [hit["id"] for hit in hits] == [both["id"], one["id"]]
    assert hits[0]["matched_queries"] == [0, 1]
    assert hits[1]["matched_queries"] == [0]
    assert len({hit["id"] for hit in hits}) == 2


def test_fusion_rank_uses_one_based_positions(store):
    first = save(store, "甲", "稳健配置")
    second = save(store, "乙", "稳健配置 附加说明")
    hits = store.search(Search(query="稳健"))
    assert [hit["id"] for hit in hits] == [first["id"], second["id"]]
    fused = store.search(Search(query="", query_variants=["稳健"]))
    assert fused[0]["fusion_score"] == pytest.approx(1 / (RRF_K + 1))
    assert fused[1]["fusion_score"] == pytest.approx(1 / (RRF_K + 2))


def test_equal_scores_use_importance_then_confidence(store):
    """When fused scores tie, importance and confidence decide the order."""
    saved = [
        save(store, "甲", "稳健配置 长期", importance=0.2, confidence=0.5),
        save(store, "乙", "稳健配置 长期", importance=0.9, confidence=0.5),
        save(store, "丙", "稳健配置 长期", importance=0.9, confidence=0.9),
    ]
    hits = store.search(Search(query="", query_variants=["稳健配置"]))
    assert [hit["id"] for hit in hits] == [saved[2]["id"], saved[1]["id"], saved[0]["id"]]


def test_tiebreak_falls_back_to_updated_at_then_id():
    """RRF ties are broken by importance, confidence, updated_at, then id."""
    def record(memory_id, importance=0.5, confidence=0.5, updated_at="2026-01-01T00:00:00+00:00"):
        return {
            "id": memory_id,
            "importance": importance,
            "confidence": confidence,
            "updated_at": updated_at,
        }

    older = record("id-old", updated_at="2025-01-01T00:00:00+00:00")
    newer = record("id-new", updated_at="2026-06-01T00:00:00+00:00")
    # Each record takes rank 1 in one list and rank 2 in the other, so both score
    # 1/(60+1) + 1/(60+2); the tie falls through to updated_at, newest first.
    page, _ = fuse([[newer, older], [older, newer]], limit=10, offset=0)
    assert [item["id"] for item in page] == ["id-new", "id-old"]
    assert page[0]["fusion_score"] == page[1]["fusion_score"]

    # Fully identical metadata: the id is the last resort and keeps the order stable.
    first, second = record("id-b"), record("id-a")
    page, _ = fuse([[first, second], [second, first]], limit=10, offset=0)
    assert [item["id"] for item in page] == ["id-a", "id-b"]
    assert page[0]["fusion_score"] == page[1]["fusion_score"]


def test_offset_and_limit_apply_after_fusion(store):
    for index in range(6):
        save(store, f"记录{index}", "稳健配置 共同词")
    first = store.search(Search(query="稳健", limit=2))
    second = store.search(Search(query="稳健", limit=2, offset=2))
    third = store.search(Search(query="稳健", limit=2, offset=4))
    ids = [hit["id"] for hit in first + second + third]
    assert len(ids) == len(set(ids)) == 6
    beyond = store.search(Search(query="稳健", limit=2, offset=CANDIDATES_PER_QUERY + 10))
    assert beyond == []


def test_variants_do_not_bypass_scope_type_validity_or_forget(store):
    global_hit = save(store, "全局可见", "稳健配置", type="preference")
    other_scope = save(store, "其他项目", "稳健配置", scope="project", scope_id="other", type="preference")
    save(store, "未来生效", "稳健配置", valid_from="2099-01-01T00:00:00Z", type="preference")
    save(
        store,
        "已过期",
        "稳健配置",
        valid_from="2020-01-01T00:00:00Z",
        valid_to="2020-06-01T00:00:00Z",
        type="preference",
    )
    forgotten = save(store, "已遗忘", "稳健配置", type="preference")
    store.forget(forgotten["id"], 1)
    wrong_type = save(store, "类型不同", "无关正文", type="episodic")

    # Default scope=global must keep the project record out even when a variant matches it.
    assert {hit["id"] for hit in store.search(Search(query="投资", query_variants=["稳健", "配置"]))} == {
        global_hit["id"]
    }
    typed = store.search(Search(query="投资", query_variants=["稳健"], type="preference"))
    assert [hit["id"] for hit in typed] == [global_hit["id"]]
    assert wrong_type["id"] not in {hit["id"] for hit in typed}
    in_scope = store.search(
        Search(query="稳健", scope="project", scope_id="other", include_global=False, query_variants=["配置"])
    )
    assert [hit["id"] for hit in in_scope] == [other_scope["id"]]


def test_variants_respect_as_of_validity(store):
    old = save(store, "旧配置", "稳健配置", valid_from="2020-01-01T00:00:00Z", valid_to="2021-01-01T00:00:00Z")
    new = save(store, "新配置", "稳健配置", valid_from="2021-01-01T00:00:00Z")
    selection = Search(query="投资", query_variants=["稳健"], as_of="2020-06-01T00:00:00Z")
    assert [hit["id"] for hit in store.search(selection)] == [old["id"]]
    assert [hit["id"] for hit in store.search(Search(query="投资", query_variants=["稳健"]))] == [new["id"]]


def test_empty_query_with_variants_skips_the_browse(store):
    relevant = save(store, "稳健配置", "投资偏好")
    save(store, "无关记录", "另一件完全无关的事情")
    hits = store.search(Search(query="", query_variants=["稳健"]))
    assert [hit["id"] for hit in hits] == [relevant["id"]]


def test_empty_query_without_variants_still_browses(store):
    save(store, "记录甲", "内容甲")
    save(store, "记录乙", "内容乙")
    assert len(store.search(Search())) == 2


def test_punctuation_only_queries_are_safe(store):
    save(store, "记录甲", "内容甲")
    assert store.search(Search(query="***", query_variants=["？？？", "***"])) == []


def test_blank_and_duplicate_variants_are_rejected(store):
    with pytest.raises(ValueError, match="blank"):
        Search(query="投资", query_variants=["稳健", "   "])
    with pytest.raises(ValueError, match="blank"):
        Search(query="投资", query_variants=[""])
    with pytest.raises(ValueError, match="100 characters"):
        Search(query="投资", query_variants=["稳" * 101])
    with pytest.raises(ValueError):
        Search(query="投资", query_variants=["稳健"] * 6)


def test_variants_are_trimmed_and_deduplicated(store):
    selection = Search(query="投资", query_variants=[" 稳健 ", "稳健", "红利", "红利"])
    assert selection.query_variants == ["稳健", "红利"]


@pytest.mark.parametrize("primary", ["alpha", " alpha "])
def test_primary_query_is_not_counted_twice_in_fusion(store, primary):
    first = save(store, "A", "alpha", importance=0.1)
    second = save(store, "B", "beta", importance=0.9)
    baseline = store.search(Search(query="alpha", query_variants=["beta"]))
    repeated = store.search(Search(query=primary, query_variants=[" alpha ", "beta", "alpha"]))
    assert [item["id"] for item in baseline] == [second["id"], first["id"]]
    assert repeated == baseline


def test_unknown_variant_keywords_return_nothing(store):
    save(store, "稳健配置", "投资偏好")
    assert store.search(Search(query="量子计算", query_variants=["赛马", "区块链"])) == []


def test_fuse_is_stable_and_reports_diagnostics():
    records = [
        {"id": f"id-{index}", "importance": 0.5, "confidence": 0.5, "updated_at": "2026-01-01"}
        for index in range(4)
    ]
    page, diagnostics = fuse([records[:3], records[1:]], limit=10, offset=0)
    # id-1 appears in both lists, so it outranks the single-list records regardless of position.
    assert [item["id"] for item in page] == ["id-1", "id-2", "id-0", "id-3"]
    assert page[0]["matched_queries"] == [0, 1]
    assert page[0]["fusion_score"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert diagnostics["fusion"] == "rrf"
    assert diagnostics["candidate_pool"] == 4
    assert diagnostics["queries"] == 2
    assert json.loads(json.dumps(page)) == page
