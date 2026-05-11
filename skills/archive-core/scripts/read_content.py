from __future__ import annotations

import argparse

from _content_common import build_database, candidate_match_score, item_to_summary, print_json
from core.storage.repositories import ItemRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read saved content from Cora's database.")
    parser.add_argument("--item-id", default="", help="Exact item id to read.")
    parser.add_argument("--query", default="", help="Fallback free-text query when item id is unknown.")
    parser.add_argument("--session-id", default="", help="Optional session filter used with query.")
    parser.add_argument("--mode", default="summary", help="summary, full_text, or key_points.")
    parser.add_argument("--database-url", default="", help="Optional SQLAlchemy database URL override.")
    return parser


def _render_reply(*, record: object, mode: str) -> str:
    normalized_text = (getattr(record, "normalized_text", "") or "").strip()
    summary = getattr(record, "summary", "") or ""
    title = getattr(record, "title", "item")
    locator_hint = getattr(record, "locator_hint", None)
    if mode == "full_text" and normalized_text:
        reply = f"这是 `{title}` 的全文：\n{normalized_text}"
    elif mode == "key_points":
        reply = f"`{title}` 的重点是：{summary}"
    else:
        reply = f"`{title}` 的摘要是：{summary}"
    if locator_hint:
        reply += f"\n定位提示：{locator_hint}"
    return reply


def main() -> int:
    args = build_parser().parse_args()
    database = build_database(args.database_url or None)
    item_repository = ItemRepository(database)
    mode = (args.mode or "summary").strip()

    record = None
    if args.item_id:
        record = item_repository.get_any(item_id=args.item_id)
    elif args.query:
        if args.session_id:
            records = item_repository.list_by_session(session_id=args.session_id, current_only=True)
        else:
            records = item_repository.list_all(current_only=True)
        scored: list[tuple[object, int]] = []
        for candidate in records:
            score = candidate_match_score(record=candidate, query=args.query)
            if score > 0:
                scored.append((candidate, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        if not scored:
            return print_json(
                {
                    "success": True,
                    "action": "retrieve",
                    "reply": "没有找到匹配的内容。",
                    "count": 0,
                    "results": [],
                }
            )
        top_record, top_score = scored[0]
        if len(scored) > 1 and scored[1][1] >= max(30, top_score - 10):
            return print_json(
                {
                    "success": True,
                    "action": "clarify",
                    "needs_clarification": True,
                    "reply": "找到了多条可能匹配的内容，请先选择一条。",
                    "results": [item_to_summary(record, score=score) for record, score in scored[:3]],
                }
            )
        record = top_record
    else:
        raise ValueError("Either --item-id or --query is required.")

    return print_json(
        {
            "success": True,
            "action": "retrieve",
            "item_id": record.id,
            "reply": _render_reply(record=record, mode=mode),
            "item": item_to_summary(record),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
