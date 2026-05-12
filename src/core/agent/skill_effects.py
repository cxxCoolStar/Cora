from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.agent.skill_protocol import HostEffect


class HostEffectDispatcher:
    def __init__(
        self,
        *,
        ingestion_service: Any,
        can_send_files_to_user: Any,
        resolve_external_user_id: Any,
        send_file: Any,
        persist_temp_upload: Any,
        current_source_event_id: Any,
        item_artifact: Any,
        ingest_upload: Any,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.can_send_files_to_user = can_send_files_to_user
        self.resolve_external_user_id = resolve_external_user_id
        self.send_file = send_file
        self.persist_temp_upload = persist_temp_upload
        self.current_source_event_id = current_source_event_id
        self.item_artifact = item_artifact
        self.ingest_upload = ingest_upload

    async def apply(self, *, invocation: Any, execution: Any, effects: list[HostEffect]) -> None:
        for effect in effects:
            if effect.kind == "ingest_text":
                await self._ingest_text(invocation=invocation, execution=execution, payload=effect.payload)
                continue
            if effect.kind == "ingest_saved_uploads":
                await self._ingest_saved_uploads(invocation=invocation, execution=execution, payload=effect.payload)
                continue
            if effect.kind == "deliver_file":
                await self._deliver_file(invocation=invocation, execution=execution, payload=effect.payload)
                continue
            raise ValueError(f"Unsupported host effect: {effect.kind}")

    async def _ingest_text(self, *, invocation: Any, execution: Any, payload: dict[str, Any]) -> None:
        result = await self.ingestion_service.ingest(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=self.current_source_event_id(invocation),
            text=str(payload.get("text") or invocation.text or "").strip(),
            upload=None,
            user_note=str(payload.get("user_note") or "").strip() or None,
        )
        execution.reply = result.reply
        execution.item_id = result.item_id
        execution.artifacts = [self.item_artifact(item_id=result.item_id, topic_name=result.topic_name)]

    async def _ingest_saved_uploads(self, *, invocation: Any, execution: Any, payload: dict[str, Any]) -> None:
        entries = list(payload.get("entries") or [])
        user_note = str(payload.get("user_note") or "").strip() or None
        artifacts: list[dict[str, Any]] = []
        last_item_id: str | None = None
        last_reply = execution.reply
        for entry in entries:
            upload = UploadFile(
                filename=str(entry.get("upload_filename") or "upload.bin"),
                file=open(str(entry.get("upload_path") or ""), "rb"),
            )
            try:
                ingested = await self.ingest_upload(invocation=invocation, upload=upload, user_note=user_note)
            finally:
                await upload.close()
            artifacts.append(self.item_artifact(item_id=ingested.item_id, topic_name=ingested.topic_name))
            last_item_id = ingested.item_id
            last_reply = ingested.reply
        execution.reply = execution.reply or (f"已保存 {len(artifacts)} 个文件。" if len(artifacts) > 1 else last_reply)
        execution.item_id = last_item_id
        execution.artifacts = artifacts

    async def _deliver_file(self, *, invocation: Any, execution: Any, payload: dict[str, Any]) -> None:
        file_path = str(payload.get("file_path") or "").strip()
        if not file_path:
            execution.reply = "没有可发送的原始文件路径。"
            execution.action = "chat"
            execution.status = "failed"
            return
        if not self.can_send_files_to_user():
            execution.reply = f"已定位到 `{payload.get('title') or '该资料'}` 的原始文件，但当前会话没有可用的发送通道。"
            execution.action = "chat"
            execution.status = "failed"
            return
        external_user_id = self.resolve_external_user_id(invocation.session_id)
        if not external_user_id:
            execution.reply = "当前会话没有可用的用户映射，暂时无法发送文件。"
            execution.action = "chat"
            execution.status = "failed"
            return
        try:
            result = await self.send_file(
                user_id=external_user_id,
                file_path=file_path,
                file_name=str(payload.get("title") or Path(file_path).name),
            )
        except Exception as exc:  # pragma: no cover - defensive
            execution.reply = str(exc)
            execution.action = "chat"
            execution.status = "failed"
            return
        if result.get("ret") != 0 or result.get("errcode") not in {None, 0}:
            execution.reply = str(result)
            execution.action = "chat"
            execution.status = "failed"
            return
        execution.reply = "已经发送，请查收。"
        execution.action = "retrieve"


__all__ = ["HostEffectDispatcher"]
