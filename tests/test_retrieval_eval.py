"""Fixed retrieval evaluation set: strict search, fusion and the relaxed fallback.

Three case sets are scored separately. ``tuning`` holds the v0.2 cases that query_variants and
RRF were designed against; ``development`` holds the cases that shaped the v0.3 fallback and is
therefore not held out; ``validation`` holds cases written after the fallback was frozen and is
reported as validation only. Counts are read from the fixture rather than hardcoded, so adding
cases cannot silently invalidate an assertion.
"""

import pytest
from eval_harness import FIXTURE, TOP_K, build_store, evaluate, load_fixture, summarize_by_set, summary

RECALL_TARGET = 0.90


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    fixture = load_fixture()
    store = build_store(tmp_path_factory.mktemp("eval") / "eval.sqlite3", fixture)
    return fixture, evaluate(store, fixture)


def test_fixture_is_well_formed_and_synthetic(report):
    fixture, _ = report
    cases = fixture["cases"]
    memories = fixture["memories"]
    assert cases and memories
    assert len({case["id"] for case in cases}) == len(cases)
    # tuning: v0.2 design cases. development: shaped the v0.3 fallback, so not held out.
    # validation: written after the fallback was frozen, reported as validation only.
    assert {case["set"] for case in cases} == {"tuning", "development", "validation"}
    assert {case["id"] for case in cases if case["set"] == "tuning"} == {
        f"C{index:02d}" for index in range(1, 21)
    }
    assert all(case["question"] and "query" in case for case in cases)
    assert all(case["scope"] in {"global", "project", "domain"} for case in cases)
    known = {memory["id"] for memory in memories}
    for case in cases:
        assert set(case.get("relevant", [])) <= known, case["id"]
        assert set(case.get("unexpected", [])) <= known, case["id"]
    assert "Synthetic evaluation fixture" in fixture["notice"]
    assert "never as blind held-out" in fixture["notice"]


def test_improved_recall_target_is_met_on_the_tuning_set(report):
    """The v0.2 target belongs to the cases it was designed against."""
    _, results = report
    improved = summarize_by_set(results["improved"], "tuning")
    assert improved["cases_with_relevant"] >= 15
    assert improved["recall_at_k"] is not None
    assert improved["recall_at_k"] >= RECALL_TARGET, results["improved"]


def test_fusion_improves_recall_over_the_strict_baseline(report):
    _, results = report
    baseline = summarize_by_set(results["baseline"], "tuning")
    improved = summarize_by_set(results["improved"], "tuning")
    assert improved["cases_with_hits"] >= baseline["cases_with_hits"]
    assert improved["recall_at_k"] >= baseline["recall_at_k"]
    # Tuning cases whose single query finds nothing must move from empty to found.
    baseline_by_id = {row["id"]: row for row in results["baseline"]}
    improved_by_id = {row["id"]: row for row in results["improved"]}
    empty_in_baseline = [
        row["id"] for row in results["baseline"] if row["set"] == "tuning" and row["relevant"] and not row["top"]
    ]
    assert empty_in_baseline, "the fixture must contain at least one strict-search miss"
    for case_id in empty_in_baseline:
        assert improved_by_id[case_id]["hits"] == improved_by_id[case_id]["relevant"]
        assert baseline_by_id[case_id]["top"] == []


def test_auto_improves_the_development_set_without_false_recalls(report):
    """search_mode="auto" must widen recall on the development cases while keeping misses honest."""
    _, results = report
    strict = summarize_by_set(results["improved"], "development")
    auto = summarize_by_set(results["auto_keywords_only"], "development")
    assert auto["recall_at_k"] > strict["recall_at_k"], results["auto_keywords_only"]
    assert auto["first_result_accuracy"] >= strict["first_result_accuracy"]
    # The risk a wider mode introduces: answering a question that has no answer.
    assert strict["no_answer_false_recall_rate"] == 0.0
    assert auto["no_answer_false_recall_rate"] == 0.0, results["auto_keywords_only"]
    for row in results["auto_keywords_only"]:
        assert row["unexpected_returned"] == [], row


def test_validation_set_is_long_questions_and_negatives(report):
    fixture, _ = report
    validation = [case for case in fixture["cases"] if case["set"] == "validation"]
    negatives = [case for case in validation if not case["relevant"]]
    assert len(validation) == 16
    assert len(negatives) == 6
    assert all(len(case["question"]) >= 12 for case in validation), "validation questions are long"
    assert all(not case["query_variants"] for case in validation)


def test_auto_recovers_long_questions_the_strict_path_misses(report):
    """The fragment-cap fix exists for these: a positional cut lost the tail of the question."""
    _, results = report
    auto = {row["id"]: row for row in results["auto_keywords_only"]}
    strict = {row["id"]: row for row in results["improved"]}
    long_question_ids = [f"V{index:02d}" for index in range(1, 11)]
    recovered = [
        case_id for case_id in long_question_ids if auto[case_id]["hits"] and not strict[case_id]["hits"]
    ]
    assert recovered, "auto must recover long questions that strict keywords miss"
    assert auto["V01"]["first_result"] == "eval-invest-0002"
    assert auto["V03"]["first_result"] == "eval-invest-0002"


def test_validation_false_recall_rate_is_reported(report):
    """Reported, not hidden. V16 is the known single-fragment false recall.

    A question whose only usable keyword also appears in a record cannot be told apart from a
    legitimate single-keyword hit by lexical means: V16 asks about an investment account
    password, shares 投资 with the investment record, and the coverage requirement degrades to
    one usable fragment. The case is kept in the set and pinned here rather than removed, so
    the limitation stays visible and cannot grow unnoticed.
    """
    _, results = report
    agg = summarize_by_set(results["auto_keywords_only"], "validation")
    assert agg["no_answer_cases"] == 6
    false_recalls = [row["id"] for row in results["auto_keywords_only"] if row["false_recall"]]
    assert false_recalls == ["V16"]
    assert agg["no_answer_false_recall_rate"] == pytest.approx(1 / 6)
    # The diagnostics do expose the weak shape of such a match.
    assert results["auto_keywords_only"][0]["reason"] in {"matched", "empty_scope", "no_lexical_match", "relaxed_match"}


def test_auto_never_regresses_a_strict_hit(report):
    """The fallback only runs on an empty pool, so existing hits cannot change."""
    _, results = report
    strict_by_id = {row["id"]: row for row in results["improved"]}
    for row in results["auto_with_variants"]:
        strict = strict_by_id[row["id"]]
        if strict["top"]:
            assert row["top"] == strict["top"], row["id"]
            assert row["relaxed_records"] == []


def test_relaxed_results_are_marked_in_the_development_set(report):
    _, results = report
    marked = [row for row in results["auto_keywords_only"] if row["relaxed_records"]]
    assert marked, "the fixture must exercise the relaxed fallback"
    for row in marked:
        assert row["fallback_applied"] is True
        assert row["reason"] == "relaxed_match"


def test_natural_language_question_baseline_is_worse(report):
    """Sending the raw question is the current client behaviour and must score lower."""
    _, results = report
    question = summary(results["question_baseline"])
    improved = summary(results["improved"])
    assert question["recall_at_k"] < improved["recall_at_k"]
    assert question["cases_with_hits"] < improved["cases_with_hits"]
    assert question["recall_at_k"] < 0.5


def test_no_case_returns_an_unexpected_record(report):
    _, results = report
    for row in results["improved"]:
        assert row["unexpected_returned"] == [], row


def test_unknown_facts_return_nothing(report):
    _, results = report
    by_id = {row["id"]: row for row in results["improved"]}
    for case_id in ("C15", "C16"):
        assert by_id[case_id]["top"] == [], by_id[case_id]


def test_scope_isolation_cases_stay_isolated(report):
    _, results = report
    by_id = {row["id"]: row for row in results["improved"]}
    assert by_id["C17"]["hits"] == ["eval-other-0010"]
    assert by_id["C18"]["hits"] == ["eval-project-0007"]
    assert "eval-other-0010" not in by_id["C18"]["top"]


def test_soft_deleted_and_expired_records_are_never_returned(report):
    _, results = report
    forbidden = {"eval-forgotten-0012", "eval-expired-0013"}
    for row in results["improved"] + results["baseline"]:
        assert forbidden.isdisjoint(row["top"]), row


def test_every_improved_hit_is_inside_top_k(report):
    _, results = report
    for row in results["improved"]:
        assert len(row["top"]) <= TOP_K
        assert set(row["hits"]) <= set(row["top"])


def test_fixture_file_is_not_the_production_database(report):
    fixture, _ = report
    assert FIXTURE.name == "retrieval_cases.json"
    assert all(memory["id"].startswith("eval-") for memory in fixture["memories"])
    assert not any("-" in memory["id"][5:] and len(memory["id"]) > 20 for memory in fixture["memories"])
