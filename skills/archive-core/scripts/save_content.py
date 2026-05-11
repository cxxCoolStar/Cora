from __future__ import annotations

import argparse

from _content_common import build_database, build_ingestion_service, print_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save text content into Cora's content store.")
    parser.add_argument("--session-id", required=True, help="Session id that owns the content.")
    parser.add_argument("--source-message-id", required=True, help="Source message id for the saved content.")
    parser.add_argument("--text", required=True, help="Content text to save.")
    parser.add_argument("--source-event-id", default="", help="Optional source event id.")
    parser.add_argument("--user-note", default="", help="Optional user note.")
    parser.add_argument("--database-url", default="", help="Optional SQLAlchemy database URL override.")
    parser.add_argument("--storage-dir", default="", help="Optional files storage directory override.")
    return parser


async def _run() -> int:
    args = build_parser().parse_args()
    database = build_database(args.database_url or None)
    ingestion = build_ingestion_service(database=database, storage_dir=args.storage_dir or None)
    result = await ingestion.ingest(
        session_id=args.session_id,
        source_message_id=args.source_message_id,
        source_event_id=args.source_event_id or None,
        text=args.text,
        upload=None,
        user_note=args.user_note or None,
    )
    return print_json(
        {
            "success": True,
            "action": "capture",
            "item_id": result.item_id,
            "reply": result.reply,
            "topic_name": result.topic_name,
        }
    )


def main() -> int:
    import asyncio

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
