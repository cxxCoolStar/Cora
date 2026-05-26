from __future__ import annotations

from archive_core.models import ArchiveRecord


def score_record(*, record: ArchiveRecord, query: str) -> int:
    if not query:
        return 0
    haystack = " ".join(
        (
            record.id,
            record.topic,
            record.path,
            record.filename,
            record.summary,
            record.description,
            record.source,
            record.user_note,
        )
    ).lower()
    if query not in haystack:
        return 0
    score = 10
    for field in (record.filename, record.summary, record.id):
        if query in (field or "").lower():
            score += 20
    if query in (record.description or "").lower():
        score += 5
    return score
