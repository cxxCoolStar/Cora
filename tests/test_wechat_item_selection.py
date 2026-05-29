from __future__ import annotations

import pytest

from core.clawbot.wechat_item_selection import classify_item_selection_follow_up


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2", ("select", 2)),
        ("第二个", ("select", 2)),
        ("第2个", ("select", 2)),
        ("取消", ("cancel", None)),
        ("帮我展开讲讲", ("invalid", None)),
    ],
)
def test_classify_item_selection_follow_up(text: str, expected: tuple[str, int | None]) -> None:
    assert classify_item_selection_follow_up(text, max_rank=3) == expected
