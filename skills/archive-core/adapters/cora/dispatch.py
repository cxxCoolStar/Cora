from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from adapters.cora._content_common import build_database, candidate_match_score, item_to_summary
from core.storage.repositories import ItemRepository, SourceEventRepository

FULL_TEXT_REPLY_THRESHOLD = 420
ACTION_CAPTURE = "capture"
ACTION_RETRIEVE = "retrieve"
ACTION_DELETE = "delete"
ACTION_CLARIFY = "clarify"
ACTION_ORGANIZE = "organize"
PENDING_ITEM_SELECTION = "item_selection"
PENDING_SAVE_DECISION = "save_decision"
PENDING_UPLOAD_SAVE = "upload_save"


def _try_file_archive_search(*, payload: dict[str, Any], query: str) -> dict[str, Any] | None:
    try:
        from adapters.cora.file_fallback import try_search_file_archive_response

        return try_search_file_archive_response(payload=payload, query=query)
    except Exception:
        return None


def _try_file_archive_deliver(*, payload: dict[str, Any], query: str) -> dict[str, Any] | None:
    try:
        from adapters.cora.file_fallback import try_deliver_from_file_archive

        return try_deliver_from_file_archive(payload=payload, query=query)
    except Exception:
        return None


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


def _arguments(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload.get("arguments") or {})


def _runtime_pending(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(payload.get("runtime_state") or {})
    pending = runtime.get("pending_state") or {}
    return dict(pending) if isinstance(pending, dict) else {}


def _resolve_query(arguments: dict[str, Any]) -> str:
    raw = str(arguments.get("query") or arguments.get("text") or arguments.get("title") or "").strip()
    if not raw:
        return ""
    cleaned = raw
    for phrase in [
        "帮我找一下",
        "帮我找",
        "帮我查一下",
        "帮我查",
        "我之前存的",
        "我之前保存的",
        "之前存的",
        "之前保存的",
        "你能帮我",
        "你能",
        "帮我",
        "一份",
        "吗",
        "请告诉我",
        "告诉我",
        "请给我",
        "给我",
        "把",
        "发给我",
        "发送给我",
        "给我看",
        "打开",
        "查看",
        "看看",
        "读取",
        "删除",
        "删掉",
        "移除",
        "总结",
        "概括",
        "这份",
        "这个",
        "那份",
        "那个",
        "那条资料",
        "这条资料",
        "那条",
        "这条",
        "资料",
    ]:
        cleaned = cleaned.replace(phrase, " ")
    return " ".join(cleaned.strip(" ，。！？,.").split())


def _looks_like_direct_open_request(*, payload: dict[str, Any]) -> bool:
    arguments = _arguments(payload)
    raw_text = " ".join(
        part
        for part in [
            str(payload.get("text") or "").strip(),
            str(arguments.get("text") or "").strip(),
            str(arguments.get("query") or "").strip(),
        ]
        if part
    )
    if not raw_text:
        return False
    return any(token in raw_text for token in ("打开", "查看", "看看", "读取", "读一下", "显示", "给我看"))


def _score_records(*, item_repository: ItemRepository, session_id: str, query: str) -> list[tuple[Any, int]]:
    records = item_repository.list_by_session(session_id=session_id, current_only=True)
    scored: list[tuple[Any, int]] = []
    for record in records:
        score = candidate_match_score(record=record, query=query)
        if score > 0:
            scored.append((record, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _score_records_any_session(*, item_repository: ItemRepository, query: str) -> list[tuple[Any, int]]:
    records = item_repository.list_all(current_only=True)
    scored: list[tuple[Any, int]] = []
    for record in records:
        score = candidate_match_score(record=record, query=query)
        if score > 0:
            scored.append((record, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _pending_payload(*, question: str, payload: dict[str, Any], choices: list[str] | None = None) -> dict[str, Any]:
    return {
        "message": question,
        "status": "completed",
        "disposition": "clarify",
        "action": ACTION_CLARIFY,
        "artifacts": [],
        "pending_state_delta": {
            "request": {
                "kind": str(payload.get("type") or "choice"),
                "question": question,
                "choices": list(choices or []),
                "payload": payload,
            }
        },
        "effects": [],
        "state_delta": {
            "last_action": ACTION_CLARIFY,
        },
    }


def _reference_clarification(
    *,
    query: str,
    scored: list[tuple[Any, int]],
    requested_intent: str,
    mode: str | None = None,
) -> dict[str, Any]:
    candidates = []
    for index, (record, score) in enumerate(scored[:3], start=1):
        summary = item_to_summary(record, score=score)
        summary["rank"] = index
        candidates.append(summary)
    question = "找到了多条可能匹配的内容，请先确认你要哪一条。"
    return {
        "message": question,
        "status": "completed",
        "disposition": "clarify",
        "action": ACTION_CLARIFY,
        "artifacts": [{"kind": "item", "ref": candidate["item_id"], "payload": candidate} for candidate in candidates],
        "pending_state_delta": {
            "request": {
                "kind": PENDING_ITEM_SELECTION,
                "question": question,
                "choices": ["第一个", "第二个", "第三个", "取消"],
                "payload": {
                    "type": PENDING_ITEM_SELECTION,
                    "query": query,
                    "requested_intent": requested_intent,
                    "mode": mode or "summary",
                    "candidates": candidates,
                },
            }
        },
        "effects": [],
        "state_delta": {
            "last_action": ACTION_CLARIFY,
            "skill_state": {
                "archive-core": {
                    "last_query": query,
                    "candidates": candidates,
                }
            },
        },
    }


def _save_content(*, payload: dict[str, Any], text: str | None = None, user_note: str | None = None) -> dict[str, Any]:
    arguments = _arguments(payload)
    resolved_text = str(text if text is not None else arguments.get("text") or payload.get("text") or "").strip()
    if not resolved_text:
        raise ValueError("archive save requires text when no upload_path is provided.")
    recent_upload = _find_recent_unsaved_upload(payload=payload)
    if recent_upload is not None and _looks_like_asset_description(text=resolved_text):
        return _save_upload(
            payload=payload,
            upload_path=str(recent_upload.stored_file_path or "").strip(),
            upload_name=str(recent_upload.original_file_name or "").strip() or "upload.bin",
            user_note=user_note or resolved_text,
        )
    return {
        "message": "",
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_CAPTURE,
        "artifacts": [],
        "effects": [{
            "kind": "ingest_text",
            "payload": {
                "text": resolved_text,
                "user_note": user_note or (str(arguments.get("user_note") or "").strip() or None),
            },
        }],
        "state_delta": {"last_action": ACTION_CAPTURE},
        "pending_state_delta": {"status": "resolved"},
    }


def _stored_file_path(record: Any) -> str:
    return str((record.metadata_json or {}).get("stored_file_path") or "").strip()


def _is_deliverable_record(record: Any) -> bool:
    return bool(_stored_file_path(record))


def _looks_like_asset_description(*, text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    if any(token in lowered for token in ("照片", "图片", "截图", "图", "文件", "简历", "文档", "photo", "image", "file")):
        return True
    if any(token in lowered for token in ("这个是", "这是", "这张", "这个文件", "这份")) and any(
        token in lowered for token in ("保存", "存", "save", "记住")
    ):
        return True
    return False


def _find_recent_unsaved_upload(*, payload: dict[str, Any]) -> Any | None:
    database = build_database(payload.get("database_url"))
    source_event_repository = SourceEventRepository(database)
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    items = item_repository.list_by_session(session_id=session_id, current_only=True)
    consumed_source_event_ids = {
        str(item.source_event_id or "").strip()
        for item in items
        if str(item.source_event_id or "").strip()
    }
    for event in source_event_repository.list_by_session(session_id=session_id, limit=12):
        if event.event_type not in {"image", "file"}:
            continue
        if str(event.id or "").strip() in consumed_source_event_ids:
            continue
        if not str(event.stored_file_path or "").strip():
            continue
        return event
    return None


def _resolve_deliverable_match(*, payload: dict[str, Any], descriptor: Any) -> list[tuple[Any, int]]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    topic_name = str((descriptor.metadata_json or {}).get("topic_name") or "").strip()
    query_parts = [
        str(descriptor.title or "").strip(),
        str(descriptor.summary or "").strip(),
        str((descriptor.metadata_json or {}).get("user_note") or "").strip(),
    ]
    candidates: list[tuple[Any, int]] = []
    for record in item_repository.list_by_session(session_id=session_id, current_only=True):
        if record.id == descriptor.id or not _is_deliverable_record(record):
            continue
        score = 0
        for part in query_parts:
            if part:
                score += candidate_match_score(record=record, query=part)
        if topic_name and str((record.metadata_json or {}).get("topic_name") or "").strip() == topic_name:
            score += 30
        delta_seconds = abs((descriptor.created_at - record.created_at).total_seconds())
        if delta_seconds <= 1800:
            score += 20
        elif delta_seconds <= 86400:
            score += 10
        if score > 0:
            candidates.append((record, score))
    candidates.sort(key=lambda pair: pair[1], reverse=True)
    return candidates


def _save_upload(
    *,
    payload: dict[str, Any],
    upload_path: str | None = None,
    upload_name: str | None = None,
    user_note: str | None = None,
) -> dict[str, Any]:
    arguments = _arguments(payload)
    resolved_upload_path = str(upload_path or payload.get("upload_path") or arguments.get("upload_path") or "").strip()
    resolved_upload_name = str(upload_name or payload.get("upload_name") or arguments.get("upload_name") or "").strip()
    if not resolved_upload_path or not resolved_upload_name:
        raise ValueError("archive save for uploads requires upload_path and upload_name.")
    return {
        "message": "",
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_CAPTURE,
        "artifacts": [],
        "effects": [{
            "kind": "ingest_saved_uploads",
            "payload": {
                "entries": [
                    {
                        "upload_path": resolved_upload_path,
                        "upload_filename": resolved_upload_name,
                    }
                ],
                "user_note": user_note or (str(arguments.get("user_note") or "").strip() or None),
            },
        }],
        "state_delta": {"last_action": ACTION_CAPTURE},
        "pending_state_delta": {"status": "resolved"},
    }


def _format_record_reply(record: Any, *, mode: str) -> str:
    normalized_text = (record.normalized_text or "").strip()
    if mode == "full_text" and normalized_text:
        message = f"这是 `{record.title}` 的全文：\n{normalized_text}"
    elif mode == "key_points":
        message = f"`{record.title}` 的重点是：{record.summary}"
    elif normalized_text and len(normalized_text) <= FULL_TEXT_REPLY_THRESHOLD:
        message = f"`{record.title}` 内容不长，我直接给你全文：\n{normalized_text}"
    else:
        message = f"`{record.title}` 的摘要是：{record.summary}"
    if record.locator_hint:
        message += f"\n定位提示：{record.locator_hint}"
    return message


def _search(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    query = _resolve_query(_arguments(payload))
    scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
    if not scored:
        scored = _score_records_any_session(item_repository=item_repository, query=query)
    if not scored:
        file_result = _try_file_archive_search(payload=payload, query=query)
        if file_result is not None:
            return file_result
        return {
            "message": "没有找到匹配的内容。",
            "status": "completed",
            "disposition": "respond",
            "action": ACTION_RETRIEVE,
            "artifacts": [],
            "effects": [],
            "state_delta": {"last_action": ACTION_RETRIEVE},
        }
    if len(scored) > 1 and _looks_like_direct_open_request(payload=payload):
        if scored[1][1] >= max(30, scored[0][1] - 10):
            return _reference_clarification(query=query, scored=scored, requested_intent="read", mode="summary")
    if len(scored) == 1:
        record = scored[0][0]
        summary = item_to_summary(record, score=scored[0][1])
        return {
            "message": _format_record_reply(record, mode="summary"),
            "status": "completed",
            "disposition": "respond",
            "action": ACTION_RETRIEVE,
            "artifacts": [{"kind": "item", "ref": record.id, "payload": summary}],
            "effects": [],
            "state_delta": {"last_action": ACTION_RETRIEVE},
        }
    lines = [f"找到了 {len(scored[:5])} 条相关内容："]
    artifacts = []
    for index, (record, score) in enumerate(scored[:5], start=1):
        summary = item_to_summary(record, score=score)
        summary["rank"] = index
        artifacts.append({"kind": "item", "ref": record.id, "payload": summary})
        lines.append(f"{index}. {record.title} - {record.summary}")
    lines.append("如果你想继续查看其中一条，请直接说文件名或序号。")
    return {
        "message": "\n".join(lines),
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_RETRIEVE,
        "artifacts": artifacts,
        "effects": [],
        "state_delta": {
            "last_action": ACTION_RETRIEVE,
            "skill_state": {"archive-core": {"last_query": query}},
        },
    }


def _read(*, payload: dict[str, Any], item_id: str | None = None, mode: str | None = None) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = _arguments(payload)
    resolved_item_id = str(item_id or arguments.get("item_id") or "").strip()
    resolved_mode = str(mode or arguments.get("mode") or "summary").strip()
    if resolved_item_id:
        record = item_repository.get_any(item_id=resolved_item_id)
    else:
        query = _resolve_query(arguments)
        scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
        if not scored:
            scored = _score_records_any_session(item_repository=item_repository, query=query)
        if not scored:
            return {
                "message": "没有找到匹配的内容。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_RETRIEVE,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_RETRIEVE},
            }
        if len(scored) > 1 and scored[1][1] >= max(30, scored[0][1] - 10):
            return _reference_clarification(query=query, scored=scored, requested_intent="read", mode=resolved_mode)
        record = scored[0][0]
    summary = item_to_summary(record)
    return {
        "message": _format_record_reply(record, mode=resolved_mode),
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_RETRIEVE,
        "artifacts": [{"kind": "item", "ref": record.id, "payload": summary}],
        "effects": [],
        "state_delta": {"last_action": ACTION_RETRIEVE},
        "pending_state_delta": {"status": "resolved"} if _runtime_pending(payload) else {},
    }


def _delete(*, payload: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = _arguments(payload)
    resolved_item_id = str(item_id or arguments.get("item_id") or "").strip()
    if not resolved_item_id:
        query = _resolve_query(arguments)
        scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
        if not scored:
            return {
                "message": "没有找到要删除的内容。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_DELETE,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_DELETE},
            }
        if len(scored) > 1 and scored[1][1] >= max(30, scored[0][1] - 10):
            return _reference_clarification(query=query, scored=scored, requested_intent="delete")
        resolved_item_id = scored[0][0].id
    deleted = item_repository.soft_delete(item_id=resolved_item_id, session_id=session_id)
    return {
        "message": f"已删除资料 `{deleted.title}`。",
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_DELETE,
        "artifacts": [{"kind": "item", "ref": deleted.id, "payload": {"title": deleted.title}}],
        "effects": [],
        "state_delta": {"last_action": ACTION_DELETE},
        "pending_state_delta": {"status": "resolved"} if _runtime_pending(payload) else {},
    }


def _deliver(*, payload: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    arguments = _arguments(payload)
    resolved_item_id = str(item_id or arguments.get("item_id") or "").strip()
    if resolved_item_id:
        record = item_repository.get_any(item_id=resolved_item_id)
    else:
        query = _resolve_query(arguments)
        scored = _score_records(item_repository=item_repository, session_id=session_id, query=query)
        if not scored:
            file_result = _try_file_archive_deliver(payload=payload, query=query)
            if file_result is not None:
                return file_result
            return {
                "message": "没有找到可发送的资料。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_RETRIEVE,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_RETRIEVE},
            }
        if len(scored) > 1 and scored[1][1] >= max(30, scored[0][1] - 10):
            return _reference_clarification(query=query, scored=scored, requested_intent="deliver")
        record = scored[0][0]
    stored_path = _stored_file_path(record)
    if not stored_path:
        deliverable_matches = _resolve_deliverable_match(payload=payload, descriptor=record)
        if deliverable_matches:
            if len(deliverable_matches) > 1 and deliverable_matches[1][1] >= max(30, deliverable_matches[0][1] - 10):
                query = _resolve_query(arguments) or str(record.title or "").strip()
                return _reference_clarification(query=query, scored=deliverable_matches, requested_intent="deliver")
            record = deliverable_matches[0][0]
            stored_path = _stored_file_path(record)
    if not stored_path:
        file_result = _try_file_archive_deliver(payload=payload, query=_resolve_query(arguments))
        if file_result is not None:
            return file_result
        return {
            "message": f"资料 `{record.title}` 没有可发送的原始文件路径。",
            "status": "failed",
            "disposition": "respond",
            "action": ACTION_RETRIEVE,
            "artifacts": [],
            "effects": [],
            "state_delta": {"last_action": ACTION_RETRIEVE},
        }
    return {
        "message": f"已定位到 `{record.title}` 的原始文件。",
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_RETRIEVE,
        "artifacts": [{"kind": "item", "ref": record.id, "payload": {"title": record.title, "file_path": stored_path}}],
        "effects": [{
            "kind": "deliver_file",
            "payload": {
                "title": record.title,
                "file_path": stored_path,
            },
        }],
        "state_delta": {"last_action": ACTION_RETRIEVE},
        "pending_state_delta": {"status": "resolved"} if _runtime_pending(payload) else {},
    }


def _overview(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    items = item_repository.list_by_session(session_id=session_id, current_only=True)
    topics = sorted(
        {
            str((item.metadata_json or {}).get("topic_name") or "").strip()
            for item in items
            if str((item.metadata_json or {}).get("topic_name") or "").strip()
        }
    )
    topic_preview = "、".join(topics[:5]) if topics else "暂无 topic"
    return {
        "message": f"知识库里共有 {len(items)} 条资料，涉及 {len(topics)} 个 topic：{topic_preview}",
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_RETRIEVE,
        "artifacts": [],
        "effects": [],
        "state_delta": {"last_action": ACTION_RETRIEVE},
    }


def _list_topics(*, payload: dict[str, Any]) -> dict[str, Any]:
    database = build_database(payload.get("database_url"))
    item_repository = ItemRepository(database)
    session_id = str(payload["session_id"])
    items = item_repository.list_by_session(session_id=session_id, current_only=True)
    topics = sorted(
        {
            str((item.metadata_json or {}).get("topic_name") or "").strip()
            for item in items
            if str((item.metadata_json or {}).get("topic_name") or "").strip()
        }
    )
    if not topics:
        message = f"当前共有 {len(items)} 条资料，但还没有可列出的主题。"
    else:
        message = f"当前共有 {len(items)} 条资料，主题如下：\n" + "\n".join(f"- {topic}" for topic in topics)
    return {
        "message": message,
        "status": "completed",
        "disposition": "respond",
        "action": ACTION_RETRIEVE,
        "artifacts": [],
        "effects": [],
        "state_delta": {"last_action": ACTION_RETRIEVE},
    }


def _clarify(*, payload: dict[str, Any]) -> dict[str, Any]:
    arguments = _arguments(payload)
    text = str(payload.get("text") or arguments.get("text") or "").strip()
    if str(payload.get("upload_path") or "").strip():
        upload_name = str(payload.get("upload_name") or "").strip() or "上传文件"
        media_kind = "image" if Path(upload_name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "file"
        question = str(arguments.get("question") or f"我收到了文件 `{upload_name}`。你希望我直接保存，还是加一句说明再保存？").strip()
        return _pending_payload(
            question=question,
            choices=["直接保存", "加说明保存", "取消"],
            payload={
                "type": PENDING_UPLOAD_SAVE,
                "pending_input_type": "upload",
                "media_kind": media_kind,
                "upload_path": str(payload.get("upload_path") or ""),
                "upload_filename": upload_name,
                "source_event_id": str(payload.get("source_event_id") or "").strip() or None,
                "upload_entries": [
                    {
                        "upload_path": str(payload.get("upload_path") or ""),
                        "upload_filename": upload_name,
                        "source_event_id": str(payload.get("source_event_id") or "").strip() or None,
                    }
                ],
            },
        )
    question = str(arguments.get("question") or "这段内容你是想让我先保存，还是先帮你总结一下？").strip()
    return _pending_payload(
        question=question,
        choices=["保存", "总结", "取消"],
        payload={
            "type": PENDING_SAVE_DECISION,
            "text": text,
        },
    )


def _summarize_text(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= 120:
        return cleaned
    return cleaned[:117] + "..."


def _resolve_selected_item_id(pending: dict[str, Any], arguments: dict[str, Any]) -> str:
    target = dict(arguments.get("target") or {})
    target_type = str(target.get("type") or "").strip()
    if target_type == "item_id":
        return str(target.get("value") or "").strip()
    if target_type == "working_set_rank":
        rank = int(target.get("value") or 0)
        for candidate in pending.get("candidates") or []:
            if int(candidate.get("rank") or 0) == rank:
                return str(candidate.get("item_id") or "").strip()
    raise ValueError("未能解析你选择的目标。")


def _resolve_pending(*, payload: dict[str, Any]) -> dict[str, Any]:
    pending = _runtime_pending(payload)
    if not pending:
        return {
            "message": "当前没有待处理的澄清问题。",
            "status": "failed",
            "disposition": "respond",
            "action": ACTION_CLARIFY,
            "artifacts": [],
            "effects": [],
            "state_delta": {"last_action": ACTION_CLARIFY},
        }
    arguments = _arguments(payload)
    pending_type = str(pending.get("type") or "").strip()
    resolution = str(arguments.get("resolution") or "").strip()
    note = str(arguments.get("note") or "").strip()
    if pending_type == PENDING_UPLOAD_SAVE:
        if resolution == "cancel":
            return {
                "message": "好的，我先不保存这个上传文件。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_CLARIFY,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_CLARIFY},
                "pending_state_delta": {"status": "cancelled"},
            }
        entries = list(pending.get("upload_entries") or [])
        if entries:
            message = f"已保存 {len(entries)} 个文件。" if len(entries) > 1 else ""
            return {
                "message": message,
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_CAPTURE,
                "artifacts": [],
                "effects": [{
                    "kind": "ingest_saved_uploads",
                    "payload": {
                        "entries": [
                            {
                                "upload_path": str(entry.get("upload_path") or ""),
                                "upload_filename": str(entry.get("upload_filename") or ""),
                            }
                            for entry in entries
                        ],
                        "user_note": note or None,
                    },
                }],
                "state_delta": {"last_action": ACTION_CAPTURE},
                "pending_state_delta": {"status": "resolved"},
            }
        return (
            _save_upload(
                payload=payload,
                upload_path=str(pending.get("upload_path") or ""),
                upload_name=str(pending.get("upload_filename") or pending.get("upload_name") or ""),
                user_note=note or None,
            )
        )
    if pending_type == PENDING_SAVE_DECISION:
        if resolution == "cancel":
            return {
                "message": "好的，我先不处理这段内容。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_CLARIFY,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_CLARIFY},
                "pending_state_delta": {"status": "cancelled"},
            }
        if resolution == "summarize":
            text = str(pending.get("text") or "").strip()
            return {
                "message": f"我先帮你总结一下：{_summarize_text(text)}",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_ORGANIZE,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_ORGANIZE},
                "pending_state_delta": {"status": "resolved"},
            }
        return _save_content(payload=payload, text=str(pending.get("text") or "").strip(), user_note=note or None)
    if pending_type == PENDING_ITEM_SELECTION:
        if resolution == "cancel":
            return {
                "message": "好的，这次我先不继续操作这条资料。",
                "status": "completed",
                "disposition": "respond",
                "action": ACTION_CLARIFY,
                "artifacts": [],
                "effects": [],
                "state_delta": {"last_action": ACTION_CLARIFY},
                "pending_state_delta": {"status": "cancelled"},
            }
        item_id = _resolve_selected_item_id(pending, arguments)
        requested_intent = str(pending.get("requested_intent") or "read").strip()
        if requested_intent == "read":
            return _read(payload=payload, item_id=item_id, mode=str(arguments.get("mode") or pending.get("mode") or "summary"))
        if requested_intent == "delete":
            return _delete(payload=payload, item_id=item_id)
        if requested_intent == "deliver":
            return _deliver(payload=payload, item_id=item_id)
    raise ValueError(f"Unsupported pending state type: {pending_type}")


def run_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    intent = str(payload.get("intent") or "").strip().lower()
    if intent == "save":
        if str(payload.get("upload_path") or "").strip():
            result = _save_upload(payload=payload)
        else:
            result = _save_content(payload=payload)
    elif intent == "search":
        result = _search(payload=payload)
    elif intent == "read":
        result = _read(payload=payload)
    elif intent == "delete":
        result = _delete(payload=payload)
    elif intent == "deliver":
        result = _deliver(payload=payload)
    elif intent == "overview":
        result = _overview(payload=payload)
    elif intent == "list_topics":
        result = _list_topics(payload=payload)
    elif intent == "clarify":
        result = _clarify(payload=payload)
    elif intent == "resolve_pending":
        result = _resolve_pending(payload=payload)
    else:
        raise ValueError(f"Unsupported archive intent: {intent}")
    return result


def main() -> int:
    payload = _read_request()
    return _print(run_dispatch(payload))


if __name__ == "__main__":
    raise SystemExit(main())
