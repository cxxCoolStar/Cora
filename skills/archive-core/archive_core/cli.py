from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from archive_core.models import ArchiveRequest, ArchiveResult
from archive_core.runtime import ArchiveRuntime
from archive_core.store.file_store import FileArchiveStore


def default_archive_root() -> Path:
    env = str(os.environ.get("ARCHIVE_ROOT") or os.environ.get("CORA_ARCHIVE_ROOT_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path(".cora/archive")


def run_request(payload: dict, *, archive_root: Path | None = None) -> ArchiveResult:
    root = archive_root or default_archive_root()
    request = ArchiveRequest.from_dict(payload)
    runtime = ArchiveRuntime(FileArchiveStore(root))
    return runtime.run(request)


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("archive-cli expects a JSON object on stdin")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("archive-cli stdin payload must be a JSON object")
        result = run_request(payload)
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0
    except Exception as exc:
        error = ArchiveResult(
            message=str(exc),
            status="failed",
            disposition="respond",
        )
        print(json.dumps(error.to_dict(), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
