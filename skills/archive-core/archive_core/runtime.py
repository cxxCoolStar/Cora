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

        topic = str(arguments.get("topic") or "inbox").strip()
        asset_type = str(arguments.get("type") or arguments.get("asset_type") or "file").strip()
        record = self.store.save_asset(
            source_path=Path(upload_path),
            topic=topic,
            asset_type=asset_type,
            summary=str(arguments.get("summary") or "").strip(),
            description=str(arguments.get("description") or "").strip(),
            source=str(arguments.get("source") or "unknown").strip(),
            user_note=str(arguments.get("user_note") or "").strip(),
            created_at=str(arguments.get("created_at") or "").strip(),
            move=bool(arguments.get("move") or upload.get("move")),
        )
        resolved = ""
        if hasattr(self.store, "resolve_path"):
            resolved = str(self.store.resolve_path(record))  # type: ignore[attr-defined]
        return ArchiveResult(
            message=f"已保存到主题 `{record.topic}`：{record.filename}",
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

        lines = ["找到了多条可能匹配的内容："]
        artifacts: list[ArchiveArtifact] = []
        for index, item in enumerate(matches, start=1):
            title = item.record.summary or item.record.filename or item.record.id
            lines.append(f"{index}. {title}（{item.record.topic}）")
            artifacts.append(self._artifact_for(item, rank=index))
        return ArchiveResult(
            message="\n".join(lines),
            disposition="clarify",
            artifacts=artifacts,
            pending={
                "kind": "item_selection",
                "query": query,
                "candidates": [item.to_summary() for item in matches],
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
        item = self._resolve_target(request)
        if item is None:
            return ArchiveResult(message="没有找到可发送的文件。", status="failed")
        if not item.file_exists:
            return ArchiveResult(
                message="找到了记录，但原始文件已不存在。",
                status="failed",
            )
        title = item.record.summary or item.record.filename or item.record.id
        action = ArchiveAction(
            type="deliver_file",
            payload={
                "path": item.resolved_path,
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

    def _resolve_target(self, request: ArchiveRequest) -> ScoredRecord | None:
        arguments = dict(request.arguments or {})
        record_id = str(arguments.get("item_id") or arguments.get("record_id") or "").strip()
        if record_id:
            matches = self.store.search(record_id=record_id, limit=1)
            return matches[0] if matches else None
        query = str(arguments.get("query") or arguments.get("text") or "").strip()
        matches = self.store.search(query=query, limit=1)
        return matches[0] if matches else None

    @staticmethod
    def _describe_record(item: ScoredRecord, *, mode: str) -> str:
        record = item.record
        title = record.summary or record.filename or record.id
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
        return ArchiveArtifact(kind="archive_record", ref=item.record.id, payload=payload)
