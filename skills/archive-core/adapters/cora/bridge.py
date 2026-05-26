from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_skill_paths() -> None:
    root = str(skill_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def run_portable_archive(
    payload: dict[str, Any],
    *,
    archive_root: Path,
    transport: str = "in_process",
) -> dict[str, Any]:
    normalized = dict(payload)
    if transport == "cli":
        return _run_via_cli(normalized, archive_root=archive_root)
    ensure_skill_paths()
    from archive_core.cli import run_request

    return run_request(normalized, archive_root=archive_root).to_dict()


def _run_via_cli(payload: dict[str, Any], *, archive_root: Path) -> dict[str, Any]:
    import os

    env = {**os.environ, "ARCHIVE_ROOT": str(archive_root)}
    completed = subprocess.run(
        [sys.executable, "-m", "archive_core.cli"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(skill_root()),
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        raise RuntimeError(completed.stderr.strip() or "archive-cli failed")
    return json.loads(completed.stdout)


def archive_result_to_skill_payload(result: dict[str, Any]) -> dict[str, Any]:
    effects: list[dict[str, Any]] = []
    for action in list(result.get("actions") or []):
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "").strip()
        action_payload = dict(action.get("payload") or {})
        if action_type == "store_file":
            record = dict(action_payload.get("record") or {})
            path = str(action_payload.get("resolved_path") or "").strip()
            if path:
                effects.append(
                    {
                        "kind": "ingest_saved_uploads",
                        "payload": {
                            "entries": [
                                {
                                    "upload_path": path,
                                    "upload_filename": record.get("filename") or Path(path).name,
                                }
                            ],
                            "user_note": record.get("user_note"),
                        },
                    }
                )
        elif action_type == "deliver_file":
            effects.append(
                {
                    "kind": "deliver_file",
                    "payload": {
                        "file_path": action_payload.get("path"),
                        "title": action_payload.get("title"),
                    },
                }
            )

    return {
        "message": str(result.get("message") or ""),
        "status": str(result.get("status") or "completed"),
        "disposition": str(result.get("disposition") or "respond"),
        "action": _action_from_result(result),
        "artifacts": list(result.get("artifacts") or []),
        "effects": effects,
        "pending_state_delta": _pending_from_result(result),
        "state_delta": {"last_action": _action_from_result(result)},
    }


def _action_from_result(result: dict[str, Any]) -> str:
    if result.get("pending"):
        return "clarify"
    for action in list(result.get("actions") or []):
        if isinstance(action, dict) and action.get("type") == "deliver_file":
            return "retrieve"
    return "chat"


def _pending_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    pending = result.get("pending")
    if not isinstance(pending, dict):
        return None
    if pending.get("kind") != "item_selection":
        return None
    candidates = list(pending.get("candidates") or [])
    return {
        "request": {
            "kind": "item_selection",
            "question": "找到了多条可能匹配的内容，请先确认你要哪一条。",
            "choices": ["第一个", "第二个", "第三个", "取消"],
            "payload": {
                "type": "item_selection",
                "query": pending.get("query"),
                "requested_intent": "deliver",
                "candidates": candidates,
            },
        }
    }
