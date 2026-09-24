"""Rank fusion for multi-query retrieval.

Each query variant is executed as an independent strict FTS5 query; this module only
merges their ranked lists. It is lexical fusion, not vector or semantic search.
"""

from typing import Any

# Reciprocal Rank Fusion constant; the standard default from Cormack et al. (2009).
RRF_K = 60

# One strict FTS query never returns more than this many candidates before pagination.
# Every variant may contribute up to this many, so a merged multi-query page draws from
# at most (1 + len(query_variants)) * CANDIDATES_PER_QUERY rows.
CANDIDATES_PER_QUERY = 100


def _tiebreak(record: dict) -> tuple:
    """Stable order for equal fused scores: importance, confidence, updated_at, then id.

    updated_at is a normalised UTC ISO-8601 string, so ordering it descending means
    reversing the whole key while keeping the final id ascending.
    """
    return (
        -float(record.get("importance") or 0.0),
        -float(record.get("confidence") or 0.0),
        str(record.get("updated_at") or ""),
        str(record.get("id") or ""),
    )


def _sorted_ids(scores: dict[str, float], records: dict[str, dict]) -> list[str]:
    """Sort by fused score, then importance, confidence, updated_at, with id ascending last.

    Python's sort is stable, so applying the keys from lowest to highest priority gives
    descending importance/confidence/updated_at and an ascending id without mixing directions.
    """
    order = sorted(scores, key=lambda memory_id: (str(memory_id),))
    order.sort(key=lambda memory_id: str(records[memory_id].get("updated_at") or ""), reverse=True)
    order.sort(key=lambda memory_id: float(records[memory_id].get("confidence") or 0.0), reverse=True)
    order.sort(key=lambda memory_id: float(records[memory_id].get("importance") or 0.0), reverse=True)
    order.sort(key=lambda memory_id: scores[memory_id], reverse=True)
    return order


def fuse(ranked_lists: list[list[dict]], limit: int, offset: int) -> tuple[list[dict], dict[str, Any]]:
    """Merge ranked result lists with RRF and apply offset/limit after fusion.

    The first list is the original query; later lists are variants. Records are merged by
    memory id, so a record found by several queries ranks above one found by a single query.
    """
    scores: dict[str, float] = {}
    records: dict[str, dict] = {}
    matched: dict[str, list[int]] = {}
    for index, ranked in enumerate(ranked_lists):
        for rank, record in enumerate(ranked, start=1):
            memory_id = record["id"]
            scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (RRF_K + rank)
            records.setdefault(memory_id, record)
            matched.setdefault(memory_id, []).append(index)
    order = _sorted_ids(scores, records)
    page = [
        records[memory_id] | {"fusion_score": round(scores[memory_id], 9), "matched_queries": matched[memory_id]}
        for memory_id in order[offset : offset + limit]
    ]
    diagnostics = {
        "fusion": "rrf",
        "rrf_k": RRF_K,
        "queries": len(ranked_lists),
        "candidates_per_query": CANDIDATES_PER_QUERY,
        "candidate_pool": len(order),
        "returned": len(page),
    }
    return page, diagnostics


# Fields a compact record always keeps: identity, revision, body, scope, type,
# validity, provenance timestamp, and the two ranking weights.
COMPACT_FIELDS = (
    "id",
    "revision",
    "title",
    "content",
    "scope",
    "scope_id",
    "type",
    "valid_from",
    "valid_to",
    "updated_at",
    "confidence",
    "importance",
)

# --- Relaxed fallback (search_mode="auto") -------------------------------------------
#
# Purpose: a phrase whose words never appear next to each other in the text ("投资偏好"
# against "投资背景与分析偏好") makes the strict AND expression return nothing. The fallback
# recalls candidates with OR over the same fragments, then filters and ranks them instead of
# returning every OR hit. It adds no model, embedding or tokenizer dependency.
#
# Thresholds are deliberately explicit and are covered by tests and by the docs.
FALLBACK_CANDIDATES = 50
FALLBACK_MAX_FRAGMENTS = 12
# A fragment that appears in many scoped records carries no discriminating power: it is what
# turns a generic question ("我是做什么工作的") into a false recall. Such fragments are dropped
# from both recall and coverage scoring.
GENERIC_DF_MIN = 3
GENERIC_DF_RATIO = 0.5
# How many distinct fragments a candidate must contain to survive. When only one usable
# fragment remains the requirement degrades to one, and the result is still flagged relaxed.
MIN_COVERAGE_FRAGMENTS = 2
RELAXED_NOTICE = "宽松匹配，需核对相关性（relaxed match: verify relevance）"
RELAXED_MATCH_QUALITY = "relaxed"


def generic_fragments(document_frequencies: dict[str, int], scoped_active: int) -> list[str]:
    """Fragments too common in the scope to discriminate, sorted for stable reporting."""
    if scoped_active <= 0:
        return []
    return sorted(
        fragment
        for fragment, frequency in document_frequencies.items()
        if frequency >= GENERIC_DF_MIN and frequency / scoped_active >= GENERIC_DF_RATIO
    )


def unusable_fragments(document_frequencies: dict[str, int]) -> list[str]:
    """Fragments matching nothing in scope.

    They recall nothing, and keeping them would raise the coverage requirement with a
    fragment no record can satisfy: the bigram spanning two adjacent query words that never
    touch in the text is exactly such a fragment.
    """
    return sorted(
        fragment for fragment, frequency in document_frequencies.items() if frequency == 0
    )


def searchable_text(record: dict) -> str:
    """The text a fragment is matched against: title, body and tags, case-folded."""
    parts = [record.get("title") or "", record.get("content") or "", *(record.get("tags") or [])]
    return " ".join(parts).lower()


def coverage_required(fragment_count: int) -> int:
    """One usable fragment cannot discriminate, so the requirement degrades gracefully."""
    return min(MIN_COVERAGE_FRAGMENTS, fragment_count)


def select_fragments(usable: list[str], frequencies: dict[str, int]) -> tuple[list[str], list[str]]:
    """Keep the most discriminative fragments, at most ``FALLBACK_MAX_FRAGMENTS``.

    Lowest document frequency first, original order for ties, so the selection is deterministic
    and a long question keeps its rare, discriminative words instead of losing whatever sits
    past a positional cut. Returns ``(kept, dropped_by_cap)``.
    """
    order = sorted(range(len(usable)), key=lambda index: (frequencies[usable[index]], index))
    kept = [usable[index] for index in order[:FALLBACK_MAX_FRAGMENTS]]
    dropped = [usable[index] for index in order[FALLBACK_MAX_FRAGMENTS :]]
    return kept, dropped


def rank_relaxed(
    candidates: list[tuple[dict, float]],
    fragments: list[str],
    limit: int,
    offset: int,
) -> tuple[list[dict], dict]:
    """Filter OR candidates by fragment coverage, then rank them.

    ``candidates`` pairs each record with its bm25 score (more negative is better). Records
    below the coverage requirement are dropped rather than returned, so the OR result is only
    ever a candidate set: ``surviving_pool`` is what remains and is what pagination is measured
    against, while ``recalled_candidates`` is the raw OR recall.

    Ordering is coverage, then bm25, importance, confidence, ``updated_at`` newest first and
    finally id ascending — the same tie-break chain the strict path uses. ``updated_at`` is a
    normalised UTC ISO-8601 string and one key cannot mix directions, so the directions are
    applied as successive stable sorts from the lowest priority upwards.
    """
    required = coverage_required(len(fragments))
    folded = [(fragment, fragment.lower()) for fragment in fragments]
    scored: list[tuple[int, float, dict]] = []
    rejected = 0
    for record, score in candidates:
        text = searchable_text(record)
        matched = sum(1 for _, lowered in folded if lowered in text)
        if matched < required:
            rejected += 1
            continue
        scored.append((matched, score, record))
    scored.sort(key=lambda item: str(item[2].get("id") or ""))
    scored.sort(key=lambda item: str(item[2].get("updated_at") or ""), reverse=True)
    scored.sort(key=lambda item: float(item[2].get("confidence") or 0.0), reverse=True)
    scored.sort(key=lambda item: float(item[2].get("importance") or 0.0), reverse=True)
    scored.sort(key=lambda item: item[1])
    scored.sort(key=lambda item: -item[0])
    page = [
        record | {"match_quality": RELAXED_MATCH_QUALITY}
        for _, _, record in scored[offset : offset + limit]
    ]
    return page, {
        "coverage_required": required,
        "recalled_candidates": len(candidates),
        "surviving_pool": len(scored),
        "candidates_rejected": rejected,
    }


# Deterministic provenance extraction: no model rewriting, no inference. A missing value
# stays None so the reader can see that it is absent instead of guessing.
SOURCE_SUMMARY_KEYS = (
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
)

# Kept byte-for-byte, never shortened: a qualifier such as "未逐项外部核实" usually sits at
# the end of the field, so truncation would silently turn a caveat into a plain fact. When
# such a value does not fit the context budget the whole record is omitted and counted.
VERBATIM_SOURCE_KEYS = ("verification", "epistemic_status")

MAX_SUMMARY_FILES = 3
MAX_SUMMARY_CHARS = 160


def _summarize(value: Any) -> Any:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text[:MAX_SUMMARY_CHARS]
    if isinstance(value, list):
        items = [_summarize(item) for item in value[:MAX_SUMMARY_FILES]]
        if len(value) > MAX_SUMMARY_FILES:
            items.append(f"+{len(value) - MAX_SUMMARY_FILES} more")
        return items
    if isinstance(value, dict):
        return {str(key): _summarize(item) for key, item in list(value.items())[:MAX_SUMMARY_FILES]}
    return value


def _provenance_value(key: str, value: Any) -> Any:
    """Reduce one source field, keeping reliability caveats intact."""
    if value in (None, "", [], {}):
        return None
    if key in VERBATIM_SOURCE_KEYS:
        return value
    return _summarize(value)


def compact_record(record: dict) -> dict:
    """Project one full memory onto the compact context shape.

    The body is never truncated or rewritten; only metadata is reduced, and the
    reliability qualifiers in ``VERBATIM_SOURCE_KEYS`` survive unshortened. A relaxed match
    keeps its quality marker, because the compact view is the recommended startup read and
    losing "verify relevance" there would defeat the point of marking it. Use memory_history
    when the full provenance or a revision snapshot is needed.
    """
    compact = {field: record.get(field) for field in COMPACT_FIELDS}
    source = record.get("source") or {}
    compact["source_summary"] = {key: _provenance_value(key, source.get(key)) for key in SOURCE_SUMMARY_KEYS}
    compact["evidence"] = "compact view; use memory_history for full provenance"
    if record.get("match_quality"):
        compact["match_quality"] = record["match_quality"]
    return compact
