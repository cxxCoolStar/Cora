from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "archive-core"
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from archive_core.filename_match import filename_token_score, score_archive_record  # noqa: E402
from archive_core.models import ArchiveRecord  # noqa: E402


def test_filename_token_score_matches_tokens_and_phrase() -> None:
    assert filename_token_score("女朋友 做蛋糕", "20260529_女朋友做蛋糕背影.jpg") > 0
    assert filename_token_score("女朋友 做蛋糕", "readme.txt") == 0


def test_score_archive_record_prefers_filename() -> None:
    record = ArchiveRecord(
        id="image_1",
        type="image",
        topic="personal-photos",
        path="topics/personal-photos/20260529_女朋友做蛋糕.jpg",
        filename="20260529_女朋友做蛋糕.jpg",
        user_note="女朋友做蛋糕",
    )
    assert score_archive_record(record=record, query="女朋友 做蛋糕") > 0
