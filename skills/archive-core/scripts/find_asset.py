from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _archive_common import archive_paths, read_index_records, summarize_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the archive JSONL index.")
    parser.add_argument("--archive-root", required=True, help="Archive root directory.")
    parser.add_argument("--query", default="", help="Free-text query over topic, filename, summary, and description.")
    parser.add_argument("--topic", default="", help="Optional exact topic filter.")
    parser.add_argument("--id", dest="record_id", default="", help="Optional exact record id filter.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of results.")
    return parser


def _match(record: dict[str, Any], *, query: str, topic: str, record_id: str) -> bool:
    if topic and str(record.get("topic") or "") != topic:
        return False
    if record_id and str(record.get("id") or "") != record_id:
        return False
    if not query:
        return True
    haystack = " ".join(
        str(record.get(field) or "")
        for field in ("id", "topic", "path", "filename", "summary", "description", "source", "user_note")
    ).lower()
    return query in haystack


def main() -> int:
    args = build_parser().parse_args()
    paths = archive_paths(args.archive_root)
    records = read_index_records(paths.index_path)
    query = (args.query or "").strip().lower()
    topic = (args.topic or "").strip().lower()
    record_id = (args.record_id or "").strip()
    limit = max(1, int(args.limit))

    matches = [record for record in records if _match(record, query=query, topic=topic, record_id=record_id)]
    results = []
    for record in matches[:limit]:
        item = summarize_record(record)
        path_value = str(record.get("path") or "")
        asset_path = (paths.archive_root / Path(path_value)).resolve()
        item["file_exists"] = asset_path.exists()
        item["resolved_path"] = str(asset_path)
        results.append(item)

    print(
        json.dumps(
            {
                "success": True,
                "count": len(results),
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
