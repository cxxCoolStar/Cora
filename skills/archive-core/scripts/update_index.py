from __future__ import annotations

import argparse
import json
from pathlib import Path

from _archive_common import append_index_record, archive_paths, ensure_archive_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append one record to the archive JSONL index.")
    parser.add_argument("--archive-root", required=True, help="Archive root directory.")
    parser.add_argument(
        "--record-file",
        required=True,
        help="Path to a JSON file containing one archive record object.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = archive_paths(args.archive_root)
    ensure_archive_layout(paths)

    record_path = Path(args.record_file).expanduser().resolve()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("record file must contain one JSON object")

    append_index_record(paths.index_path, record)
    print(
        json.dumps(
            {
                "success": True,
                "index_path": str(paths.index_path),
                "record_id": record.get("id"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
