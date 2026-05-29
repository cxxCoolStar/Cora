from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.cora._content_common import flatten_nested_arguments
from adapters.cora.bridge import archive_result_to_skill_payload
from adapters.cora.settings import get_cora_archive_settings
from archive_core.models import ArchiveRequest
from archive_core.runtime import ArchiveRuntime
from archive_core.store.file_store import FileArchiveStore

PENDING_ITEM_SELECTION = "item_selection"
PENDING_UPLOAD_SAVE = "upload_save"


def run_filesystem_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    archive_root = Path(str(payload.get("archive_root") or get_cora_archive_settings().archive_root_dir))
    runtime = ArchiveRuntime(FileArchiveStore(archive_root))
    intent = str(payload.get("intent") or "").strip().lower()
    had_pending = bool(_runtime_pending(payload)) if intent == "resolve_pending" else False
    if intent == "resolve_pending":
        result = _resolve_pending(runtime=runtime, payload=payload)
    else:
        request = _payload_to_request(payload)
        result = runtime.run(request).to_dict()
    skill_payload = archive_result_to_skill_payload(
        result,
        storage_mode="filesystem",
    )
    if had_pending and not (skill_payload.get("pending_state_delta") or {}).get("request"):
        arguments = flatten_nested_arguments(dict(payload.get("arguments") or {}))
        resolution = str(arguments.get("resolution") or "").strip()
        status = "cancelled" if resolution == "cancel" else "resolved"
        skill_payload["pending_state_delta"] = {"status": status}
    return skill_payload


def _payload_to_request(payload: dict[str, Any]) -> ArchiveRequest:
    arguments = flatten_nested_arguments(dict(payload.get("arguments") or {}))
    intent = str(payload.get("intent") or "").strip().lower()
    upload_path = str(payload.get("upload_path") or arguments.get("upload_path") or "").strip()
    upload_name = str(payload.get("upload_name") or arguments.get("upload_name") or "").strip()
    request_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "intent": intent,
        "arguments": arguments,
        "session": {
            "session_id": str(payload.get("session_id") or ""),
            "channel": _channel_from_payload(payload),
        },
    }
    if upload_path:
        request_payload["upload"] = {"path": upload_path, "name": upload_name}
    if str(payload.get("text") or "").strip() and intent == "save":
        note = str(arguments.get("user_note") or arguments.get("note") or payload.get("text") or "").strip()
        if note:
            request_payload["arguments"]["user_note"] = note
    return ArchiveRequest.from_dict(request_payload)


def _resolve_pending(*, runtime: ArchiveRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    pending = _runtime_pending(payload)
    if not pending:
        return {
            "message": "当前没有待处理的澄清问题。",
            "status": "failed",
            "disposition": "respond",
        }
    arguments = flatten_nested_arguments(dict(payload.get("arguments") or {}))
    pending_type = str(pending.get("type") or "").strip()
    resolution = str(arguments.get("resolution") or "").strip()
    note = str(arguments.get("note") or "").strip()

    if pending_type == PENDING_UPLOAD_SAVE:
        if resolution == "cancel":
            return {
                "message": "好的，我先不保存这个上传文件。",
                "status": "completed",
                "disposition": "respond",
            }
        entries = list(pending.get("upload_entries") or [])
        if not entries:
            entries = [
                {
                    "upload_path": str(pending.get("upload_path") or ""),
                    "upload_filename": str(pending.get("upload_filename") or pending.get("upload_name") or ""),
                }
            ]
        saved: list[dict[str, Any]] = []
        for entry in entries:
            upload_path = str(entry.get("upload_path") or "").strip()
            if not upload_path:
                continue
            result = runtime.run(
                _payload_to_request(
                    {
                        **payload,
                        "intent": "save",
                        "upload_path": upload_path,
                        "upload_name": str(entry.get("upload_filename") or Path(upload_path).name),
                        "arguments": {
                            "user_note": note,
                            "move": True,
                        },
                    }
                )
            )
            saved.append(result.to_dict())
        if not saved:
            return {"message": "没有可保存的上传文件。", "status": "failed", "disposition": "respond"}
        return saved[-1]

    if pending_type == PENDING_ITEM_SELECTION:
        if resolution == "cancel":
            return {
                "message": "好的，这次我先不继续操作这条资料。",
                "status": "completed",
                "disposition": "respond",
            }
        record_id = _resolve_selected_record_id(pending, arguments)
        requested_intent = str(pending.get("requested_intent") or "deliver").strip()
        request = ArchiveRequest.from_dict(
            {
                "schema_version": "1.0",
                "intent": requested_intent,
                "arguments": {"record_id": record_id},
                "session": {"session_id": str(payload.get("session_id") or "")},
            }
        )
        return runtime.run(request).to_dict()

    return {"message": f"不支持的 pending 类型：{pending_type}", "status": "failed", "disposition": "respond"}


def _runtime_pending(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(payload.get("runtime_state") or {})
    pending = runtime.get("pending_state") or {}
    return dict(pending) if isinstance(pending, dict) else {}


def _channel_from_payload(payload: dict[str, Any]) -> str:
    runtime = dict(payload.get("runtime_state") or {})
    for key in ("channel", "platform"):
        value = str(runtime.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _resolve_selected_record_id(pending: dict[str, Any], arguments: dict[str, Any]) -> str:
    target = dict(arguments.get("target") or {})
    target_type = str(target.get("type") or "").strip()
    if target_type == "item_id":
        return str(target.get("value") or "").strip()
    if target_type == "working_set_rank":
        rank = int(target.get("value") or 0)
        for candidate in pending.get("candidates") or []:
            if int(candidate.get("rank") or 0) == rank:
                return str(candidate.get("item_id") or candidate.get("id") or "").strip()
    if str(arguments.get("record_id") or arguments.get("item_id") or "").strip():
        return str(arguments.get("record_id") or arguments.get("item_id") or "").strip()
    raise ValueError("未能解析你选择的目标。")
