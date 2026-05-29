from __future__ import annotations

from archive_core.models import ArchiveRecord


def filename_token_score(query: str, text: str) -> int:
    """Score how well a filename (or path fragment) matches a user query."""
    lowered_query = (query or "").strip().lower()
    haystack = (text or "").strip().lower()
    if not lowered_query or not haystack:
        return 0

    compact_query = "".join(lowered_query.split())
    compact_haystack = "".join(haystack.split())
    tokens = [token for token in lowered_query.split() if token]
    score = 0

    if compact_query and compact_query in compact_haystack:
        score += 35
    for token in tokens:
        if token in haystack:
            score += 10
    return score


def score_archive_record(*, record: ArchiveRecord, query: str) -> int:
    """Rank archive records; filename is the primary search surface."""
    filename = record.filename or ""
    score = filename_token_score(query, filename)
    if score <= 0:
        score = max(
            filename_token_score(query, record.topic or ""),
            filename_token_score(query, record.path or ""),
        )
    return score
