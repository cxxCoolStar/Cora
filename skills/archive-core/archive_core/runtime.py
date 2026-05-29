from __future__ import annotations

from pathlib import Path

from archive_core.host import ArchiveHost, DeliverOutcome
from archive_core.models import (
    ArchiveAction,
    ArchiveArtifact,
    ArchiveRequest,
    ArchiveResult,
    ScoredRecord,
)
from archive_core.store.base import ArchiveStore

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
DEFAULT_IMAGE_TOPIC = "personal-photos"
TOPIC_DISPLAY_NAMES = {
    "personal-photos": "个人照片",
    "inbox": "收件箱",
}


class ArchiveRuntime:
    """Host-agnostic archive workflow runtime."""

    def __init__(self, store: ArchiveStore, host: ArchiveHost | None = None) -> None:
        self.store = store
        self.host = host

    def run(self, request: ArchiveRequest) -> ArchiveResult:
        intent = request.intent
        if intent == "save":
            return self._save(request)
        if intent == "search":
            return self._search(request)
        if intent == "read":
            return self._read(request)
        if intent == "deliver":
            return self._deliver(request)
        if intent == "delete":
            return self._delete(request)
        if intent == "overview":
            return self._overview()
        if intent == "list_topics":
            return self._list_topics()
        raise ValueError(f"unsupported intent: {intent}")

    def _save(self, request: ArchiveRequest) -> ArchiveResult:
        upload = dict(request.upload or {})
        arguments = dict(request.arguments or {})
        upload_path = str(upload.get("path") or arguments.get("upload_path") or "").strip()
        if not upload_path:
            return ArchiveResult(
                message="save requires upload.path (absolute file path).",
                status="failed",
                disposition="respond",
            )

        topic = str(arguments.get("topic") or "").strip()
        if not topic:
            topic = _default_topic_for_path(Path(upload_path))
        asset_type = str(arguments.get("type") or arguments.get("asset_type") or _asset_type_for_path(Path(upload_path))).strip()
        user_note = str(arguments.get("user_note") or arguments.get("note") or "").strip()
        record = self.store.save_asset(
            source_path=Path(upload_path),
            topic=topic,
            asset_type=asset_type,
            summary=str(arguments.get("summary") or user_note or "").strip(),
            description=str(arguments.get("description") or user_note or "").strip(),
            source=str(arguments.get("source") or request.session.get("channel") or "unknown").strip(),
            user_note=user_note,
            created_at=str(arguments.get("created_at") or "").strip(),
            move=bool(arguments.get("move") or upload.get("move")),
        )
        resolved = ""
        if hasattr(self.store, "resolve_path"):
            resolved = str(self.store.resolve_path(record))  # type: ignore[attr-defined]
        return ArchiveResult(
            message=_save_success_message(record),
            artifacts=[
                ArchiveArtifact(
                    kind="archive_record",
                    ref=record.id,
                    payload=record.to_dict(),
                )
            ],
            actions=[
                ArchiveAction(
                    type="store_file",
                    payload={
                        "record": record.to_dict(),
                        "resolved_path": resolved,
                    },
                )
            ],
            raw={"record": record.to_dict()},
        )

    def _search(self, request: ArchiveRequest) -> ArchiveResult:
        arguments = dict(request.arguments or {})
        query = str(arguments.get("query") or arguments.get("text") or "").strip()
        topic = str(arguments.get("topic") or "").strip()
        limit = int(arguments.get("limit") or 5)
        matches = self.store.search(query=query, topic=topic, limit=limit)
        if not matches:
            return ArchiveResult(message="没有找到匹配的内容。")

        if len(matches) == 1:
            item = matches[0]
            return ArchiveResult(
                message=self._describe_record(item, mode="summary"),
                artifacts=[self._artifact_for(item)],
                raw={"matches": [item.to_summary()]},
            )

        lines = ["找到了多条可能匹配的内容，请回复序号（如 1、2）："]
        artifacts: list[ArchiveArtifact] = []
        pending_candidates: list[dict[str, object]] = []
        for index, item in enumerate(matches, start=1):
            title = _display_title(item.record)
            lines.append(f"{index}. {title}（{TOPIC_DISPLAY_NAMES.get(item.record.topic, item.record.topic)}）")
            artifacts.append(self._artifact_for(item, rank=index))
            summary = item.to_summary()
            summary["rank"] = index
            summary["item_id"] = item.record.id
            summary["topic_name"] = TOPIC_DISPLAY_NAMES.get(item.record.topic, item.record.topic)
            pending_candidates.append(summary)
        return ArchiveResult(
            message="\n".join(lines),
            disposition="clarify",
            artifacts=artifacts,
            pending={
                "kind": "item_selection",
                "query": query,
                "requested_intent": str(arguments.get("requested_intent") or "search"),
                "candidates": pending_candidates,
            },
            raw={"matches": [item.to_summary() for item in matches]},
        )

    def _read(self, request: ArchiveRequest) -> ArchiveResult:
        item = self._resolve_target(request)
        if item is None:
            return ArchiveResult(message="没有找到要读取的内容。", status="failed")
        mode = str((request.arguments or {}).get("mode") or "summary").strip()
        return ArchiveResult(
            message=self._describe_record(item, mode=mode),
            artifacts=[self._artifact_for(item)],
            raw={"record": item.record.to_dict()},
        )

    def _deliver(self, request: ArchiveRequest) -> ArchiveResult:
        arguments = dict(request.arguments or {})
        record_id = str(arguments.get("item_id") or arguments.get("record_id") or "").strip()
        if not record_id:
            record_id = _first_record_id(arguments)
        if record_id:
            item = self._find_by_id(record_id)
        else:
            query = _normalize_query(str(arguments.get("query") or arguments.get("text") or "").strip())
            matches = self.store.search(query=query, limit=5)
            if not matches:
                return ArchiveResult(message="没有找到可发送的文件。", status="failed")
            if len(matches) > 1 and matches[1].score >= max(30, matches[0].score - 10):
                return self._selection_clarification(
                    query=query,
                    matches=matches,
                    requested_intent="deliver",
                )
            item = matches[0]
        if item is None:
            return ArchiveResult(message="没有找到可发送的文件。", status="failed")
        if not item.file_exists:
            return ArchiveResult(
                message="找到了记录，但原始文件已不存在。",
                status="failed",
            )
        title = _display_title(item.record)
        action = ArchiveAction(
            type="deliver_file",
            payload={
                "path": item.resolved_path,
                "file_path": item.resolved_path,
                "title": title,
                "record_id": item.record.id,
            },
        )
        if self.host is not None:
            outcome = self.host.deliver_file(
                path=Path(item.resolved_path),
                title=title,
                session=dict(request.session or {}),
            )
            if outcome.delivered:
                return ArchiveResult(
                    message=outcome.message or "已经发送，请查收。",
                    actions=[action],
                    artifacts=[self._artifact_for(item)],
                    raw={"delivered": True},
                )
            return ArchiveResult(
                message=outcome.message or "无法发送文件。",
                status="failed",
                actions=[action],
                artifacts=[self._artifact_for(item)],
            )

        return ArchiveResult(
            message=(
                f"已定位到 `{title}`，路径：`{item.resolved_path}`。"
                "当前宿主未配置发送能力，请由通道手动发送。"
            ),
            actions=[action],
            artifacts=[self._artifact_for(item)],
        )

    def _delete(self, request: ArchiveRequest) -> ArchiveResult:
        arguments = dict(request.arguments or {})
        record_id = str(arguments.get("item_id") or arguments.get("record_id") or "").strip()
        if not record_id:
            item = self._resolve_target(request)
            record_id = item.record.id if item else ""
        if not record_id:
            return ArchiveResult(message="请指定要删除的记录。", status="failed")
        deleted = self.store.delete(record_id)
        if deleted is None:
            return ArchiveResult(message="没有找到要删除的记录。", status="failed")
        return ArchiveResult(message=f"已删除 `{deleted.filename or deleted.id}`。")

    def _overview(self) -> ArchiveResult:
        count = self.store.count_records()
        topics = self.store.list_topics()
        message = f"当前归档共有 {count} 条记录，{len(topics)} 个主题。"
        if topics:
            message += "\n主题：" + "、".join(topics[:10])
            if len(topics) > 10:
                message += f" 等（共 {len(topics)} 个）"
        return ArchiveResult(message=message, raw={"count": count, "topics": topics})

    def _list_topics(self) -> ArchiveResult:
        topics = self.store.list_topics()
        if not topics:
            return ArchiveResult(message="当前还没有任何主题。")
        lines = ["当前主题："] + [f"- {name}" for name in topics]
        return ArchiveResult(message="\n".join(lines), raw={"topics": topics})

    def _find_by_id(self, record_id: str) -> ScoredRecord | None:
        matches = self.store.search(record_id=record_id, limit=1)
        return matches[0] if matches else None

    def _selection_clarification(
        self,
        *,
        query: str,
        matches: list[ScoredRecord],
        requested_intent: str,
    ) -> ArchiveResult:
        lines = ["找到了多张可能匹配的图片，请回复序号（如 1、2）："]
        artifacts: list[ArchiveArtifact] = []
        pending_candidates: list[dict[str, object]] = []
        for index, item in enumerate(matches[:3], start=1):
            title = _display_title(item.record)
            lines.append(f"{index}. {title}")
            artifacts.append(self._artifact_for(item, rank=index))
            summary = item.to_summary()
            summary["rank"] = index
            summary["item_id"] = item.record.id
            summary["topic_name"] = TOPIC_DISPLAY_NAMES.get(item.record.topic, item.record.topic)
            pending_candidates.append(summary)
        return ArchiveResult(
            message="\n".join(lines),
            disposition="clarify",
            artifacts=artifacts,
            pending={
                "kind": "item_selection",
                "query": query,
                "requested_intent": requested_intent,
                "candidates": pending_candidates,
            },
            raw={"matches": [item.to_summary() for item in matches[:3]]},
        )

    def _resolve_target(self, request: ArchiveRequest) -> ScoredRecord | None:
        arguments = dict(request.arguments or {})
        record_id = str(arguments.get("item_id") or arguments.get("record_id") or "").strip()
        if record_id:
            matches = self.store.search(record_id=record_id, limit=1)
            return matches[0] if matches else None
        query = _normalize_query(str(arguments.get("query") or arguments.get("text") or "").strip())
        matches = self.store.search(query=query, limit=1)
        return matches[0] if matches else None

    @staticmethod
    def _describe_record(item: ScoredRecord, *, mode: str) -> str:
        record = item.record
        title = _display_title(record)
        if mode == "full_text" and record.description:
            return f"这是 `{title}` 的全文：\n{record.description}"
        if record.description and len(record.description) <= 420:
            return f"`{title}` 内容：\n{record.description}"
        if record.description:
            return f"`{title}` 的摘要是：{record.description[:400]}..."
        return f"`{title}`（{record.topic}）"

    @staticmethod
    def _artifact_for(item: ScoredRecord, *, rank: int | None = None) -> ArchiveArtifact:
        payload = item.to_summary()
        if rank is not None:
            payload["rank"] = rank
        payload["item_id"] = item.record.id
        payload["topic_name"] = TOPIC_DISPLAY_NAMES.get(item.record.topic, item.record.topic)
        return ArchiveArtifact(kind="archive_record", ref=item.record.id, payload=payload)


def _display_title(record: object) -> str:
    filename = str(getattr(record, "filename", "") or "").strip()
    note = str(getattr(record, "user_note", "") or "").strip()
    if note:
        return note
    if filename and not filename.lower().startswith("wechat_image"):
        return filename
    return str(getattr(record, "summary", "") or filename or getattr(record, "id", ""))


def _save_success_message(record: object) -> str:
    topic = str(getattr(record, "topic", "") or "")
    topic_label = TOPIC_DISPLAY_NAMES.get(topic, topic)
    return f"照片已保存 ✅ 已归档到「{topic_label}」话题，方便日后查找。"


def _default_topic_for_path(path: Path) -> str:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return DEFAULT_IMAGE_TOPIC
    return "inbox"


def _asset_type_for_path(path: Path) -> str:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return "image"
    return "file"


def _normalize_query(raw: str) -> str:
    cleaned = raw
    for phrase in (
        "帮我找一下",
        "帮我找",
        "帮我查",
        "发给我",
        "发我",
        "发送给我",
        "把我",
        "把",
        "的照片",
        "照片",
        "相关",
        "请",
        "吗",
    ):
        cleaned = cleaned.replace(phrase, " ")
    return " ".join(cleaned.split())


def _first_record_id(arguments: dict[str, object]) -> str:
    for key in ("item_refs", "item_ref", "refs"):
        value = arguments.get(key)
        if isinstance(value, list):
            for entry in value:
                candidate = str(entry or "").strip()
                if candidate:
                    return candidate
        elif value is not None:
            candidate = str(value).strip()
            if candidate:
                return candidate
    return ""
