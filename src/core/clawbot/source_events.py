from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import UploadFile


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(slots=True)
class SourceEventManager:
    source_event_repository: Any

    @staticmethod
    def detect_media_kind(*, upload: UploadFile | None) -> str | None:
        if upload is None or not (upload.filename or "").strip():
            return None
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        return "file"

    def create_source_event(
        self,
        *,
        session_id: str,
        source_message_id: str,
        text: str | None,
        upload: UploadFile | None,
        metadata: dict[str, Any] | None = None,
    ) -> object:
        has_upload = upload is not None and bool((upload.filename or "").strip())
        event_type = "file" if has_upload else "text"
        media_kind = self.detect_media_kind(upload=upload)
        if media_kind == "image":
            event_type = "image"
        elif text and text.strip():
            stripped = text.strip()
            if stripped.startswith("http://") or stripped.startswith("https://"):
                event_type = "link"
        return self.source_event_repository.create(
            session_id=session_id,
            source_message_id=source_message_id,
            channel=str((metadata or {}).get("channel") or "chat"),
            external_event_id=(metadata or {}).get("external_event_id"),
            external_user_id=(metadata or {}).get("external_user_id"),
            event_type=event_type,
            raw_text=text or "",
            original_file_name=upload.filename if has_upload and upload is not None else None,
            mime_type=getattr(upload, "content_type", None) if has_upload and upload is not None else None,
            metadata=metadata or {},
        )
