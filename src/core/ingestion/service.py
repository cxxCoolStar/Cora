from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from urllib.parse import urlparse
import uuid

import anyio
from fastapi import UploadFile

from core.ingestion.parsers.base import FileSource, ParsedContent
from core.ingestion.parsers.code_parser import CodeFileParser
from core.ingestion.parsers.doc_parser import DocFileParser
from core.ingestion.parsers.docling_parser import DoclingFileParser
from core.ingestion.parsers.image_parser import ImageFileParser
from core.ingestion.parsers.link_parser import LinkParser
from core.ingestion.parsers.text_parser import TextParser
from core.ingestion.parsers.txt_parser import TxtFileParser
from core.storage.repositories import ItemRepository, MessageRepository, UserSignalRepository
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(slots=True)
class IngestedItemResult:
    item_id: str
    reply: str
    topic_name: str | None = None


class IngestionService:
    def __init__(
        self,
        *,
        item_repository: ItemRepository,
        message_repository: MessageRepository,
        user_signal_repository: UserSignalRepository,
        storage_dir: Path,
        image_parser: ImageFileParser | None = None,
        topic_organizer: TopicOrganizerService | None = None,
    ) -> None:
        self.item_repository = item_repository
        self.message_repository = message_repository
        self.user_signal_repository = user_signal_repository
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.topic_organizer = topic_organizer
        self.text_parser = TextParser()
        self.link_parser = LinkParser()
        self.txt_parser = TxtFileParser()
        self.code_parser = CodeFileParser()
        self.docling_parser = DoclingFileParser()
        self.doc_parser = DocFileParser()
        self.image_parser = image_parser or ImageFileParser(describer=None)

    async def ingest(
        self,
        *,
        session_id: str,
        source_message_id: str,
        source_event_id: str | None,
        text: str | None,
        upload: UploadFile | None,
        user_note: str | None = None,
    ) -> IngestedItemResult:
        logger.info(
            "ingestion start session_id=%s source_message_id=%s has_text=%s has_upload=%s",
            session_id,
            source_message_id,
            bool(text and text.strip()),
            bool(upload and (upload.filename or "").strip()),
        )
        parsed = await self._parse_input(text=text, upload=upload)
        logger.info("ingestion parsed item_type=%s title=%s", parsed.item_type, parsed.title)
        parsed = self._apply_user_note(parsed=parsed, user_note=user_note)
        return self._store_parsed_item(
            session_id=session_id,
            source_message_id=source_message_id,
            source_event_id=source_event_id,
            parsed=parsed,
        )

    async def ingest_saved_upload(
        self,
        *,
        session_id: str,
        source_message_id: str,
        source_event_id: str | None,
        file_path: Path,
        filename: str,
        user_note: str | None = None,
    ) -> IngestedItemResult:
        parsed = await self._parse_saved_upload(file_path=file_path, filename=filename)
        parsed = self._apply_user_note(parsed=parsed, user_note=user_note)
        return self._store_parsed_item(
            session_id=session_id,
            source_message_id=source_message_id,
            source_event_id=source_event_id,
            parsed=parsed,
        )

    def _store_parsed_item(
        self,
        *,
        session_id: str,
        source_message_id: str,
        source_event_id: str | None,
        parsed: ParsedContent,
        forced_topic_slug: str | None = None,
        forced_topic_reason: str | None = None,
    ) -> IngestedItemResult:
        summary = self._summarize(parsed.normalized_text) or self._summarize(parsed.title)
        tags = self._extract_tags(parsed.normalized_text, parsed.item_type)
        locator_hint = self._build_locator_hint(parsed)
        document_key = self._build_document_key(parsed=parsed)
        previous_current = None
        next_version = 1
        if document_key:
            previous_current = self.item_repository.find_current_by_document_key(
                session_id=session_id,
                document_key=document_key,
            )
            if previous_current is not None:
                next_version = max(1, int(previous_current.version) + 1)
        item = self.item_repository.create(
            session_id=session_id,
            source_message_id=source_message_id,
            source_event_id=source_event_id,
            item_type=parsed.item_type,
            title=parsed.title,
            raw_content=parsed.raw_content,
            normalized_text=parsed.normalized_text,
            summary=summary,
            metadata={**parsed.metadata, "tags": tags},
            locator_hint=locator_hint,
            document_key=document_key,
            version=next_version,
            is_current=1,
        )
        if previous_current is not None and previous_current.id != item.id:
            self.item_repository.mark_superseded(
                item_id=previous_current.id,
                superseded_by_item_id=item.id,
            )
        self._record_user_signals(
            session_id=session_id,
            item_id=item.id,
            item_type=parsed.item_type,
            tags=tags,
            title=item.title,
        )
        logger.info("ingestion stored item_id=%s item_type=%s", item.id, parsed.item_type)
        if self.topic_organizer is not None:
            from core.channels.wechat.progress import (
                WechatProgressStage,
                schedule_wechat_progress_stage,
            )

            schedule_wechat_progress_stage(WechatProgressStage.INGEST_STORE)
        topic_name: str | None = None
        if self.topic_organizer is not None:
            if forced_topic_slug:
                assignment = self.topic_organizer.link_item_to_topic_slug(
                    session_id=session_id,
                    item=item,
                    slug=forced_topic_slug,
                    topic_name=forced_topic_slug.replace("-", " ").title(),
                    summary=summary,
                    tags=tags,
                    reason=forced_topic_reason or "Linked by archive workflow.",
                )
            else:
                assignment = self.topic_organizer.assign_item_to_topic(
                    session_id=session_id,
                    item=item,
                )
            topic_name = assignment.topic.name
            metadata = dict(item.metadata_json or {})
            metadata.update(
                {
                    "topic_slug": assignment.topic.slug,
                    "topic_name": assignment.topic.name,
                    "topic_selection_source": assignment.source,
                    "topic_selection_confidence": assignment.confidence,
                    "topic_selection_reason": assignment.reason,
                }
            )
            item = self.item_repository.update_metadata(item_id=item.id, metadata=metadata)
            logger.info(
                "ingestion topic_assignment item_id=%s topic=%s created=%s",
                item.id,
                assignment.topic.slug,
                assignment.created,
            )
        reply = self._build_reply(
            item=item,
            parsed=parsed,
            summary=summary,
            topic_name=topic_name,
            previous_current=previous_current,
        )
        try:
            from core.skills.hooks import fire_item_saved_hooks

            fire_item_saved_hooks(item=item, parsed=parsed)
        except Exception:
            logger.exception("item_saved hooks failed item_id=%s", getattr(item, "id", ""))
        return IngestedItemResult(item_id=item.id, reply=reply, topic_name=topic_name)

    @staticmethod
    def _build_reply(
        *,
        item: object,
        parsed: ParsedContent,
        summary: str,
        topic_name: str | None,
        previous_current: object | None,
    ) -> str:
        if parsed.metadata.get("parse_status") in {"unsupported", "failed"}:
            original_name = parsed.metadata.get("original_file_name", getattr(item, "title", "file"))
            suffix = parsed.metadata.get("file_suffix", "")
            parse_status = parsed.metadata.get("parse_status")
            parse_error = parsed.metadata.get("parse_error")
            error_hint = f" (parse error: {parse_error})" if parse_status == "failed" and parse_error else ""
            reply = (
                f"Saved `{original_name}` as a file upload ({suffix or 'unknown'}). "
                f"I can't extract searchable text from this file type yet{error_hint}, but I kept the original file so you can find it later. "
                f"Summary: {summary}"
            )
        else:
            if previous_current is not None:
                reply = (
                    f"Updated `{getattr(item, 'title', 'item')}` to v{getattr(item, 'version', 1)} (previous version kept as history). "
                    f"Summary: {summary}"
                )
            else:
                reply = f"Saved `{getattr(item, 'title', 'item')}` as a {parsed.item_type.replace('_', ' ')}. Summary: {summary}"
        if topic_name:
            reply += f" Topic: `{topic_name}`."
        return reply

    async def _parse_input(self, *, text: str | None, upload: UploadFile | None) -> ParsedContent:
        has_real_upload = upload is not None and bool((upload.filename or "").strip())
        if has_real_upload:
            suffix = Path(upload.filename or "").suffix.lower()
            target = self.storage_dir / f"{uuid.uuid4()}_{upload.filename}"
            data = await upload.read()
            target.write_bytes(data)
            logger.info("ingestion upload_saved filename=%s suffix=%s path=%s bytes=%d", upload.filename, suffix, target, len(data))
            parsed = await self._parse_saved_upload(file_path=target, filename=upload.filename or target.name)
            return parsed
        if text is None or not text.strip():
            raise ValueError("Either text or a supported file is required.")
        stripped = text.strip()
        if self._looks_like_url(stripped):
            return self.link_parser.parse(stripped)
        return self.text_parser.parse(stripped)

    async def _parse_saved_upload(self, *, file_path: Path, filename: str) -> ParsedContent:
        suffix = file_path.suffix.lower()
        source = FileSource(path=file_path, filename=filename)
        if suffix == ".txt":
            parsed = self.txt_parser.parse(source)
        elif self.code_parser.supports_suffix(suffix):
            try:
                parsed = self.code_parser.parse(source)
            except Exception as exc:
                return ParsedContent(
                    item_type="file_upload",
                    title=filename or file_path.name,
                    raw_content="",
                    normalized_text="",
                    metadata={
                        "parse_status": "failed",
                        "file_suffix": suffix or "unknown",
                        "original_file_name": filename or file_path.name,
                        "stored_file_path": str(file_path),
                        "parse_error": f"{type(exc).__name__}: {exc}",
                    },
                )
        elif suffix in IMAGE_EXTENSIONS:
            try:
                parsed = self.image_parser.parse(source)
            except Exception as exc:
                description = (
                    "Image uploaded without a visual description because image analysis was unavailable. "
                    f"Original parser error: {type(exc).__name__}: {exc}"
                )
                parsed = ParsedContent(
                    item_type="image",
                    title=Path(filename or file_path.name).stem or (filename or file_path.name),
                    raw_content=description,
                    normalized_text=f"Image file: {filename or file_path.name}\nVisual description:\n{description}",
                    metadata={
                        "vision_status": "unavailable",
                        "mime_type": self._guess_mime_type(filename or file_path.name),
                        "file_suffix": suffix or "unknown",
                    },
                )
        elif suffix in {".md", ".markdown", ".docx", ".pdf"}:
            try:
                parsed = await anyio.to_thread.run_sync(self.docling_parser.parse, source)
            except Exception as exc:
                return ParsedContent(
                    item_type="file_upload",
                    title=filename or file_path.name,
                    raw_content="",
                    normalized_text="",
                    metadata={
                        "parse_status": "failed",
                        "file_suffix": suffix or "unknown",
                        "original_file_name": filename or file_path.name,
                        "stored_file_path": str(file_path),
                        "parse_error": f"{type(exc).__name__}: {exc}",
                    },
                )
        elif suffix == ".doc":
            try:
                parsed = await anyio.to_thread.run_sync(self.doc_parser.parse, source)
            except Exception as exc:
                return ParsedContent(
                    item_type="file_upload",
                    title=filename or file_path.name,
                    raw_content="",
                    normalized_text="",
                    metadata={
                        "parse_status": "failed",
                        "file_suffix": suffix or "unknown",
                        "original_file_name": filename or file_path.name,
                        "stored_file_path": str(file_path),
                        "parse_error": f"{type(exc).__name__}: {exc}",
                    },
                )
        else:
            try:
                parsed = self.txt_parser.parse(source)
            except Exception:
                return ParsedContent(
                    item_type="file_upload",
                    title=filename or file_path.name,
                    raw_content="",
                    normalized_text="",
                    metadata={
                        "parse_status": "unsupported",
                        "file_suffix": suffix or "unknown",
                        "original_file_name": filename or file_path.name,
                        "stored_file_path": str(file_path),
                    },
                )
        parsed.metadata["stored_file_path"] = str(file_path)
        parsed.metadata["original_file_name"] = filename or file_path.name
        return parsed

    @staticmethod
    def _guess_mime_type(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix == ".png":
            return "image/png"
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        return "application/octet-stream"

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _summarize(text: str) -> str:
        compact = " ".join(text.split())
        if len(compact) <= 160:
            return compact
        return compact[:157] + "..."

    def preview_summary(self, text: str) -> str:
        return self._summarize(text)

    @staticmethod
    def _extract_tags(text: str, item_type: str) -> list[str]:
        lowered = text.lower()
        tags = [item_type]
        for keyword in ["agent", "rag", "interview", "github", "prompt", "memory"]:
            if keyword in lowered:
                tags.append(keyword)
        return sorted(set(tags))

    @staticmethod
    def _build_locator_hint(parsed: ParsedContent) -> str | None:
        user_locator_hint = parsed.metadata.get("user_locator_hint")
        if user_locator_hint:
            return str(user_locator_hint)
        original_name = parsed.metadata.get("original_file_name")
        if original_name:
            return f"Look for the file message named `{original_name}` on the saved date."
        url = parsed.metadata.get("url")
        if url:
            return f"Look for the link message containing {url}."
        return None

    @staticmethod
    def _build_document_key(*, parsed: ParsedContent) -> str | None:
        item_type = parsed.item_type or ""
        if item_type not in {"document", "file_upload"}:
            return None
        original_name = str(parsed.metadata.get("original_file_name") or "").strip()
        if not original_name:
            return None
        stem = Path(original_name).stem.lower().strip()
        # normalize common update suffixes: v2, final, final2, 2026-04-30, copy
        stem = re.sub(r"[\s_\-]*(v\d+|final\d*|copy\d*|\d{4}[-_]\d{2}[-_]\d{2})$", "", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        return stem or None

    @staticmethod
    def _apply_user_note(*, parsed: ParsedContent, user_note: str | None) -> ParsedContent:
        note = (user_note or "").strip()
        if not note:
            return parsed
        metadata = dict(parsed.metadata or {})
        metadata["user_note"] = note
        normalized = parsed.normalized_text.strip()
        if parsed.item_type in {"document", "image", "file_upload"}:
            combined = f"{note}\n\n{normalized}".strip() if normalized else note
            locator = metadata.get("user_locator_hint") or f"Look for this item using: {note}"
            metadata["user_locator_hint"] = locator
            return ParsedContent(
                item_type=parsed.item_type,
                title=parsed.title,
                raw_content=parsed.raw_content,
                normalized_text=combined,
                metadata=metadata,
            )
        return ParsedContent(
            item_type=parsed.item_type,
            title=parsed.title,
            raw_content=parsed.raw_content,
            normalized_text=normalized,
            metadata=metadata,
        )

    def _record_user_signals(
        self,
        *,
        session_id: str,
        item_id: str,
        item_type: str,
        tags: list[str],
        title: str,
    ) -> None:
        self.user_signal_repository.create(
            session_id=session_id,
            item_id=item_id,
            signal_type="content_type",
            signal_value=item_type,
            confidence="high",
            metadata={"title": title},
        )
        for tag in tags:
            self.user_signal_repository.create(
                session_id=session_id,
                item_id=item_id,
                signal_type="interest_topic",
                signal_value=tag,
                confidence="medium",
                metadata={"title": title},
            )
