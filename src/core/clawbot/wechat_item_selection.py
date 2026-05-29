from __future__ import annotations

import re
from typing import Literal

PENDING_ITEM_SELECTION = "item_selection"

_CANCEL_PHRASES = frozenset({"取消", "不用", "算了", "不要了", "别发了", "不发了", "不要发"})
_ORDINAL_MAP = {
    "1": 1,
    "2": 2,
    "3": 3,
    "一": 1,
    "二": 2,
    "三": 3,
    "①": 1,
    "②": 2,
    "③": 3,
}
_ORDINAL_WORDS = {
    "第一个": 1,
    "第二个": 2,
    "第三个": 3,
    "第一": 1,
    "第二": 2,
    "第三": 3,
}


def classify_item_selection_follow_up(
    text: str,
    *,
    max_rank: int = 3,
) -> tuple[Literal["cancel", "select", "invalid"], int | None]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return "invalid", None
    compact = cleaned.strip("。！!？? ")
    if compact in _CANCEL_PHRASES or any(
        compact == phrase or compact.startswith(f"{phrase}了") for phrase in _CANCEL_PHRASES
    ):
        if len(compact) <= 12:
            return "cancel", None
    for phrase, rank in _ORDINAL_WORDS.items():
        if phrase in compact and rank <= max_rank:
            return "select", rank
    match = re.search(r"第\s*([123一二三①②③])\s*个?", compact)
    if match:
        rank = _ORDINAL_MAP.get(match.group(1))
        if rank is not None and rank <= max_rank:
            return "select", rank
    if compact in _ORDINAL_MAP and _ORDINAL_MAP[compact] <= max_rank:
        return "select", _ORDINAL_MAP[compact]
    return "invalid", None


__all__ = ["PENDING_ITEM_SELECTION", "classify_item_selection_follow_up"]
