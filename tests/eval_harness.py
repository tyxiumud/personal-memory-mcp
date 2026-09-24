"""Fixed retrieval evaluation harness: synthetic memories, no production data.

The backend uses the query/query_variants recorded in each case; the natural-language
question is what a client would have to rewrite into those keywords. Backend performance
and client rewriting are therefore reported separately.
"""

import json
from pathlib import Path

from personal_memory.models import Search, now
from personal_memory.store import MemoryStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "retrieval_cases.json"
TOP_K = 5


def load_fixture(path: Path | None = None) -> dict:
    return json.loads((path or FIXTURE).read_text(encoding="utf-8"))


def build_store(database: Path, fixture: dict | None = None) -> MemoryStore:
    """Fresh store with the synthetic fixture loaded through the archive importer.

    Importing keeps the readable ``eval-*`` ids from the fixture (the store otherwise
    generates UUIDs) and avoids touching any production database.
    """
    fixture = fixture or load_fixture()
    store = MemoryStore(database)
    records = []
    for item in fixture["memories"]:
        data = dict(item)
        forgotten = data.pop("forgotten", False)
        if forgotten:
            data["forgotten_at"] = now()
        records.append(data)
    store.import_bundle(
        {
            "format": "personal-memory-mcp",
            "version": 1,
            "exported_at": now(),
            "memories": records,
            "history": [],
        }
    )
    return store


def selection_for(case: dict, use_variants: bool, use_question: bool = False, mode: str = "strict") -> Search:
    kwargs = {
        "query": case["question"] if use_question else case["query"],
        "scope": case.get("scope", "global"),
        "scope_id": case.get("scope_id"),
        "include_global": True,
        "limit": TOP_K,
        "search_mode": mode,
    }
    if use_variants:
        kwargs["query_variants"] = case.get("query_variants") or []
    return Search(**kwargs)


def metrics(returned: list[str], relevant: list[str], unexpected: list[str], k: int = TOP_K) -> dict:
    top = returned[:k]
    hits = [memory_id for memory_id in top if memory_id in relevant]
    recall = len(hits) / len(relevant) if relevant else None
    precision = len(hits) / len(top) if top else None
    false_positives = [memory_id for memory_id in top if memory_id not in relevant]
    leaked = [memory_id for memory_id in top if memory_id in unexpected]
    first = top[0] if top else None
    if relevant:
        # A case with expected records is correct at rank 1 only when a relevant one leads.
        first_correct = bool(first) and first in relevant
    else:
        # A case with no answer is correct at rank 1 only when nothing is returned at all.
        first_correct = not top
    return {
        "top": top,
        "hits": hits,
        "recall_at_k": recall,
        "precision_at_k": precision,
        "false_positives": false_positives,
        "unexpected_returned": leaked,
        "answered": bool(top),
        "first_result": first,
        "first_result_correct": first_correct,
        "no_answer": not relevant,
        "false_recall": (not relevant) and bool(top),
    }


def run_case(
    store: MemoryStore,
    case: dict,
    use_variants: bool,
    use_question: bool = False,
    mode: str = "strict",
) -> dict:
    selection = selection_for(case, use_variants, use_question, mode)
    detailed = store.search_detailed(selection)
    returned = [record["id"] for record in detailed["memories"]]
    result = metrics(returned, case.get("relevant", []), case.get("unexpected", []))
    result["id"] = case["id"]
    result["group"] = case["group"]
    result["set"] = case.get("set", "tuning")
    result["question"] = case["question"]
    result["query"] = selection.query
    result["query_variants"] = case.get("query_variants") or []
    result["scope"] = case.get("scope", "global")
    result["scope_id"] = case.get("scope_id")
    result["relevant"] = case.get("relevant", [])
    result["notes"] = case.get("notes", "")
    result["reason"] = detailed["retrieval"]["reason"]
    result["fallback_applied"] = detailed["retrieval"]["fallback"]["applied"]
    result["relaxed_records"] = [
        record["id"] for record in detailed["memories"] if record.get("match_quality") == "relaxed"
    ]
    return result


def evaluate(store: MemoryStore, fixture: dict | None = None) -> dict:
    fixture = fixture or load_fixture()
    cases = fixture["cases"]
    return {
        # What the current client does today: send the natural-language question as-is.
        "question_baseline": [
            run_case(store, case, use_variants=False, use_question=True) for case in cases
        ],
        # Strict retrieval with the same keywords the improved path uses, but no fusion.
        "baseline": [run_case(store, case, use_variants=False) for case in cases],
        "improved": [run_case(store, case, use_variants=True) for case in cases],
        # search_mode="auto" without variants: what a client gets when it passes the phrase
        # the user typed and does not split it into keywords itself.
        "auto_keywords_only": [run_case(store, case, use_variants=False, mode="auto") for case in cases],
        # search_mode="auto" with the keywords, i.e. the documented recommendation.
        "auto_with_variants": [run_case(store, case, use_variants=True, mode="auto") for case in cases],
        "top_k": TOP_K,
    }


def summary(rows: list[dict]) -> dict:
    """Aggregate recall, first-result accuracy and false recalls.

    Recall@K and Precision@K are averaged over cases that have at least one relevant record.
    First-result accuracy covers every case: for a case with expected records the leading
    result must be relevant, and for a case with no answer the correct leading result is no
    result at all. The no-answer false-recall rate reports how often an unanswerable query
    returned anything, which is the risk a wider retrieval mode can introduce.
    """
    scored = [row for row in rows if row["relevant"]]
    recalls = [row["recall_at_k"] for row in scored if row["recall_at_k"] is not None]
    precisions = [row["precision_at_k"] for row in scored if row["precision_at_k"] is not None]
    no_answer = [row for row in rows if row["no_answer"]]
    false_recalls = [row for row in no_answer if row["false_recall"]]
    return {
        "cases": len(rows),
        "cases_with_relevant": len(scored),
        "recall_at_k": sum(recalls) / len(recalls) if recalls else None,
        "precision_at_k": sum(precisions) / len(precisions) if precisions else None,
        "first_result_accuracy": (
            sum(1 for row in rows if row["first_result_correct"]) / len(rows) if rows else None
        ),
        "cases_with_hits": sum(1 for row in scored if row["hits"]),
        "no_answer_cases": len(no_answer),
        "no_answer_false_recalls": len(false_recalls),
        "no_answer_false_recall_rate": (len(false_recalls) / len(no_answer)) if no_answer else None,
        "false_positive_records": sum(len(row["false_positives"]) for row in rows),
        "unexpected_returned": sum(len(row["unexpected_returned"]) for row in rows),
        "empty_result_cases": sum(1 for row in rows if not row["top"]),
        "relaxed_records": sum(len(row["relaxed_records"]) for row in rows),
    }


def summarize_by_set(rows: list[dict], case_set: str) -> dict:
    """Metrics restricted to one case set, so tuning, development and validation stay separate."""
    return summary([row for row in rows if row["set"] == case_set])
