from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from archive_core.store.file_store import FileArchiveStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the archive JSONL index.")
    parser.add_argument("--archive-root", required=True, help="Archive root directory.")
    parser.add_argument("--query", default="", help="Free-text query over topic, filename, summary, and description.")
    parser.add_argument("--topic", default="", help="Optional exact topic filter.")
    parser.add_argument("--id", dest="record_id", default="", help="Optional exact record id filter.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of results.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = FileArchiveStore(args.archive_root)
    matches = store.search(
        query=args.query,
        topic=args.topic,
        record_id=args.record_id,
        limit=args.limit,
    )
    results = [item.to_summary() for item in matches]
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
