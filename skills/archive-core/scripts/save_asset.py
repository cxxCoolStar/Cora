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
    parser = argparse.ArgumentParser(description="Save an asset into a topic folder and append an index record.")
    parser.add_argument("--archive-root", required=True, help="Archive root directory.")
    parser.add_argument("--source-file", required=True, help="Path to the source file.")
    parser.add_argument("--topic", required=True, help="Topic folder slug.")
    parser.add_argument("--type", dest="asset_type", required=True, help="Asset type such as image or document.")
    parser.add_argument("--summary", default="", help="Short summary for retrieval.")
    parser.add_argument("--description", default="", help="Longer description for retrieval.")
    parser.add_argument("--source", default="unknown", help="Source platform or producer.")
    parser.add_argument("--user-note", default="", help="Optional note from the user.")
    parser.add_argument("--created-at", default="", help="Optional ISO timestamp.")
    parser.add_argument("--move", action="store_true", help="Move the source file instead of copying it.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = FileArchiveStore(args.archive_root)
    record = store.save_asset(
        source_path=Path(args.source_file),
        topic=args.topic,
        asset_type=args.asset_type,
        summary=args.summary,
        description=args.description,
        source=args.source,
        user_note=args.user_note,
        created_at=args.created_at,
        move=args.move,
    )
    resolved = store.resolve_path(record)
    print(
        json.dumps(
            {
                "success": True,
                "topic": record.topic,
                "stored_path": str(resolved),
                "relative_path": record.path,
                "record": record.to_dict(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
