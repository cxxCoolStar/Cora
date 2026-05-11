from __future__ import annotations

import argparse

from _content_common import build_database, candidate_match_score, item_to_summary, print_json
from core.storage.repositories import ItemRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search saved content in Cora's database.")
    parser.add_argument("--query", required=True, help="Free-text query.")
    parser.add_argument("--session-id", default="", help="Optional session filter.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of results.")
    parser.add_argument("--database-url", default="", help="Optional SQLAlchemy database URL override.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    database = build_database(args.database_url or None)
    item_repository = ItemRepository(database)
    query = (args.query or "").strip()
    limit = max(1, int(args.limit))
    if args.session_id:
        records = item_repository.list_by_session(session_id=args.session_id, current_only=True)
    else:
        records = item_repository.list_all(current_only=True)
    scored: list[tuple[object, int]] = []
    for record in records:
        score = candidate_match_score(record=record, query=query)
        if score > 0:
            scored.append((record, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    results = [item_to_summary(record, score=score) for record, score in scored[:limit]]
    reply = "没有找到匹配的内容。" if not results else f"找到了 {len(results)} 条相关内容。"
    return print_json(
        {
            "success": True,
            "action": "retrieve",
            "reply": reply,
            "count": len(results),
            "results": results,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
