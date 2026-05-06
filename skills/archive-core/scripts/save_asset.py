from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from _archive_common import (
    append_index_record,
    archive_paths,
    ensure_archive_layout,
    generate_asset_id,
    normalize_asset_type,
    normalize_created_at,
    normalize_topic,
    unique_destination,
)


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
    paths = archive_paths(args.archive_root)
    ensure_archive_layout(paths)

    source_file = Path(args.source_file).expanduser().resolve()
    if not source_file.is_file():
        raise FileNotFoundError(f"source file not found: {source_file}")

    topic = normalize_topic(args.topic)
    asset_type = normalize_asset_type(args.asset_type)
    topic_dir = paths.topics_root / topic
    topic_dir.mkdir(parents=True, exist_ok=True)

    destination = unique_destination(topic_dir, source_file.name)
    if args.move:
        shutil.move(str(source_file), str(destination))
    else:
        shutil.copy2(source_file, destination)

    created_at = normalize_created_at(args.created_at)
    relative_path = destination.relative_to(paths.archive_root).as_posix()
    record = {
        "id": generate_asset_id(asset_type),
        "type": asset_type,
        "topic": topic,
        "path": relative_path,
        "filename": destination.name,
        "summary": (args.summary or "").strip(),
        "description": (args.description or "").strip(),
        "source": (args.source or "unknown").strip() or "unknown",
        "user_note": (args.user_note or "").strip(),
        "created_at": created_at,
    }

    append_index_record(paths.index_path, record)
    print(
        json.dumps(
            {
                "success": True,
                "topic": topic,
                "stored_path": str(destination),
                "relative_path": relative_path,
                "record": record,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
