from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from _content_common import build_database, build_ingestion_service, candidate_match_score, item_to_summary
from core.storage.repositories import ItemRepository


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


def _resolve_query(arguments: dict[str, Any]) -> str:
    return str(arguments.get("query") or arguments.get("text") or arguments.get("title") or "").strip()


def _score_records(*, item_repository: ItemRepository, session_id: str, query: str) -> list[tuple[Any, int]]:
    records = item_repository.list_by_session(session_id=session_id, current_only=True)
    scored: list[tuple[Any, int]] = []
    for record in records:
        score = candidate_match_score(record=record, query=query)
        if score > 0:
            scored.append((record, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _clarify_payload(*, query: str, scored: list[tuple[Any, int]]) -> dict[str, Any]:
    results = [item_to_summary(record, score=score) for record, score in scored[:3]]
    return {
        "message": "找到了多条可能匹配的内容，请先确认你要哪一条。",
        "status": "completed",
        "disposition": "clarify",
        "action": "archive.search",
        "artifacts": [{"kind": "item", "ref": result["item_id"], "payload": result} for result in results],
        "state_update": {
            "pending_skill": "archive-core",
            "skill_state": {
                "archive-core": {
                    "last_query": query,
                    "candidates": results,
                }
            },
        },
    }


async def _save_content(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    ingestion = build_ingestion_service(database=database, storage_dir=payload.get("storage_dir"))
    session_id = str(payload["session_id"])
    source_message_id = str(payload["source_message_id"])
    source_event_id = str(payload.get("source_event_id") or "").strip() or None
    arguments = dict(payload.get("arguments") or {})
    text = str(arguments.get("text") or payload.get("text") or "").strip()
    if not text:
        raise ValueError("archive save requires text when no upload_path is provided.")
    result = await ingestion.ingest(
        session_id=session_id,
        source_message_id=source_message_id,
        source_event_id=source_event_id,
        text=text,
        upload=None,
        user_note=str(arguments.get("user_note") or "").strip() or None,
    )
    return {
        "message": result.reply,
        "status": "completed",
        "disposition": "respond",
        "action": "archive.save",
        "artifacts": [{"kind": "item", "ref": result.item_id, "payload": {"topic_name": result.topic_name}}],
        "state_update": {"last_action": "archive.save"},
    }


async def _save_upload(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    ingestion = build_ingestion_service(database=database, storage_dir=payload.get("storage_dir"))
    session_id = str(payload["session_id"])
    source_message_id = str(payload["source_message_id"])
    source_event_id = str(payload.get("source_event_id") or "").strip() or None
    arguments = dict(payload.get("arguments") or {})
    upload_path = str(payload.get("upload_path") or arguments.get("upload_path") or "").strip()
    upload_name = str(payload.get("upload_name") or arguments.get("upload_name") or "").strip()
    if not upload_path or not upload_name:
        raise ValueError("archive save for uploads requires upload_path and upload_name.")
    result = await ingestion.ingest_saved_upload(
        session_id=session_id,
        source_message_id=source_message_id,
        source_event_id=source_event_id,
        file_path=Path(upload_path),
        filename=upload_name,
        user_note=str(arguments.get("user_note") or "").strip() or None,
    )
    return {
        "message": result.reply,
        "status": "completed",
        "disposition": "respond",
        "action": "archive.save",
        "artifacts": [{"kind": "item", "ref": result.item_id, "payload": {"topic_name": result.topic_name}}],
        "state_update": {"last_action": "archive.save"},
    }


def _search(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    query = _resolve_query(dict(payload.get("arguments") or {}))
    scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
    results = [item_to_summary(record, score=score) for record, score in scored[:5]]
    return {
        "message": "没有找到匹配的内容。" if not results else f"找到了 {len(results)} 条相关内容。",
        "status": "completed",
        "disposition": "respond",
        "action": "archive.search",
        "artifacts": [{"kind": "item", "ref": result["item_id"], "payload": result} for result in results],
        "state_update": {"last_action": "archive.search"},
    }


def _read(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = dict(payload.get("arguments") or {})
    item_id = str(arguments.get("item_id") or "").strip()
    mode = str(arguments.get("mode") or "summary").strip()
    if item_id:
        record = item_repository.get_any(item_id=item_id)
    else:
        query = _resolve_query(arguments)
        scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
        if not scored:
            return {
                "message": "没有找到匹配的内容。",
                "status": "completed",
                "disposition": "respond",
                "action": "archive.read",
                "artifacts": [],
                "state_update": {"last_action": "archive.read"},
            }
        top_record, top_score = scored[0]
        if len(scored) > 1 and scored[1][1] >= max(30, top_score - 10):
            return _clarify_payload(query=query, scored=scored)
        record = top_record
    normalized_text = (record.normalized_text or "").strip()
    if mode == "full_text" and normalized_text:
        message = f"这是 `{record.title}` 的全文：\n{normalized_text}"
    elif mode == "key_points":
        message = f"`{record.title}` 的重点是：{record.summary}"
    else:
        message = f"`{record.title}` 的摘要是：{record.summary}"
    if record.locator_hint:
        message += f"\n定位提示：{record.locator_hint}"
    summary = item_to_summary(record)
    return {
        "message": message,
        "status": "completed",
        "disposition": "respond",
        "action": "archive.read",
        "artifacts": [{"kind": "item", "ref": record.id, "payload": summary}],
        "state_update": {"last_action": "archive.read"},
    }


def _delete(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = dict(payload.get("arguments") or {})
    item_id = str(arguments.get("item_id") or "").strip()
    if not item_id:
        query = _resolve_query(arguments)
        scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
        if not scored:
            return {
                "message": "没有找到要删除的内容。",
                "status": "completed",
                "disposition": "respond",
                "action": "archive.delete",
                "artifacts": [],
                "state_update": {"last_action": "archive.delete"},
            }
        if len(scored) > 1 and scored[1][1] >= max(30, scored[0][1] - 10):
            return _clarify_payload(query=query, scored=scored)
        item_id = scored[0][0].id
    deleted = item_repository.soft_delete(item_id=item_id, session_id=session_id)
    return {
        "message": f"已删除资料 `{deleted.title}`。",
        "status": "completed",
        "disposition": "respond",
        "action": "archive.delete",
        "artifacts": [{"kind": "item", "ref": deleted.id, "payload": {"title": deleted.title}}],
        "state_update": {"last_action": "archive.delete"},
    }


def _deliver(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = dict(payload.get("arguments") or {})
    query = _resolve_query(arguments)
    scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
    if not scored:
        return {
            "message": "没有找到可发送的资料。",
            "status": "completed",
            "disposition": "respond",
            "action": "archive.deliver",
            "artifacts": [],
            "state_update": {"last_action": "archive.deliver"},
        }
    if len(scored) > 1 and scored[1][1] >= max(30, scored[0][1] - 10):
        return _clarify_payload(query=query, scored=scored)
    record = scored[0][0]
    stored_path = str((record.metadata_json or {}).get("stored_file_path") or "").strip()
    if not stored_path:
        return {
            "message": f"资料 `{record.title}` 没有可发送的原始文件路径。",
            "status": "failed",
            "disposition": "respond",
            "action": "archive.deliver",
            "artifacts": [],
            "state_update": {"last_action": "archive.deliver"},
        }
    return {
        "message": f"已定位到 `{record.title}` 的原始文件。",
        "status": "completed",
        "disposition": "respond",
        "action": "archive.deliver",
        "artifacts": [
            {
                "kind": "delivery_target",
                "ref": record.id,
                "payload": {
                    "title": record.title,
                    "file_path": stored_path,
                },
            }
        ],
        "state_update": {"last_action": "archive.deliver"},
    }


def main() -> int:
    payload = _read_request()
    intent = str(payload.get("intent") or "").strip().lower()
    if intent == "save":
        if str(payload.get("upload_path") or "").strip():
            result = asyncio.run(_save_upload(payload=payload))
        else:
            result = asyncio.run(_save_content(payload=payload))
    elif intent == "search":
        result = _search(payload=payload)
    elif intent == "read":
        result = _read(payload=payload)
    elif intent == "delete":
        result = _delete(payload=payload)
    elif intent == "deliver":
        result = _deliver(payload=payload)
    else:
        raise ValueError(f"Unsupported archive intent: {intent}")
    return _print(result)


if __name__ == "__main__":
    raise SystemExit(main())
