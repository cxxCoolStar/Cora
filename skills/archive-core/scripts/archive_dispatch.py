from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _ensure_paths() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    repo_src = skill_root.parents[1] / "src"
    for entry in (str(repo_src), str(skill_root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("archive_dispatch.py expected a JSON payload on stdin.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("archive_dispatch.py expected a JSON object.")
    return payload


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def run_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_paths()
    from adapters.cora.dispatch import run_dispatch as dispatch

    return dispatch(payload)


def main() -> int:
    return _print(run_dispatch(_read_request()))


if __name__ == "__main__":
    raise SystemExit(main())
