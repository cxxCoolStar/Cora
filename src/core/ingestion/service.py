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
from core.ingestion.parsers.doc_parser import DocFileParser
from core.ingestion.parsers.docling_parser import DoclingFileParser
from core.ingestion.parsers.link_parser import LinkParser
from core.ingestion.parsers.text_parser import TextParser
from core.ingestion.parsers.txt_parser import TxtFileParser
from core.storage.repositories import ItemChunkRepository, ItemRepository, MessageRepository, UserSignalRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestedItemResult:
    item_id: str
    reply: str


class IngestionService:
    def __init__(
        self,
        *,
        item_repository: ItemRepository,
        item_chunk_repository: ItemChunkRepository,
        message_repository: MessageRepository,
        user_signal_repository: UserSignalRepository,
        storage_dir: Path,
    ) -> None:
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository
        self.message_repository = message_repository
        self.user_signal_repository = user_signal_repository
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.text_parser = TextParser()
        self.link_parser = LinkParser()
        self.txt_parser = TxtFileParser()
        self.docling_parser = DoclingFileParser()
        self.doc_parser = DocFileParser()

    async def ingest(
        self,
        *,
        session_id: str,
        source_message_id: str,
        text: str | None,
        upload: UploadFile | None,
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
        summary = self._summarize(parsed.normalized_text) or self._summarize(parsed.title)
        tags = self._extract_tags(parsed.normalized_text, parsed.item_type)
        locator_hint = self._build_locator_hint(parsed)
        chunk_texts = parsed.metadata.pop("_chunk_texts", None)
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
            self.item_repository.mark_superseded(item_id=previous_current.id, superseded_by_item_id=item.id)
        self._record_user_signals(
            session_id=session_id,
            item_id=item.id,
            item_type=parsed.item_type,
            tags=tags,
            title=item.title,
        )
        if isinstance(chunk_texts, list) and any(str(c).strip() for c in chunk_texts):
            chunks = [str(c).strip() for c in chunk_texts if str(c).strip()]
        else:
            chunks = self._chunk_text(parsed.normalized_text)
        for index, chunk in enumerate(chunks):
            self.item_chunk_repository.create(item_id=item.id, chunk_index=index, content=chunk, metadata={"title": item.title})
        logger.info("ingestion stored item_id=%s chunks=%d item_type=%s", item.id, len(chunks), parsed.item_type)

        if parsed.metadata.get("parse_status") in {"unsupported", "failed"}:
            original_name = parsed.metadata.get("original_file_name", item.title)
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
                    f"Updated `{item.title}` to v{item.version} (previous version kept as history). "
                    f"Summary: {summary}"
                )
            else:
                reply = f"Saved `{item.title}` as a {item.item_type.replace('_', ' ')}. Summary: {summary}"
        return IngestedItemResult(item_id=item.id, reply=reply)

    async def _parse_input(self, *, text: str | None, upload: UploadFile | None) -> ParsedContent:
        has_real_upload = upload is not None and bool((upload.filename or "").strip())
        if has_real_upload:
            suffix = Path(upload.filename or "").suffix.lower()
            target = self.storage_dir / f"{uuid.uuid4()}_{upload.filename}"
            data = await upload.read()
            target.write_bytes(data)
            logger.info("ingestion upload_saved filename=%s suffix=%s path=%s bytes=%d", upload.filename, suffix, target, len(data))
            source = FileSource(path=target, filename=upload.filename or target.name)
            if suffix == ".txt":
                parsed = self.txt_parser.parse(source)
            elif suffix in {".md", ".markdown", ".docx"}:
                try:
                    parsed = await anyio.to_thread.run_sync(self.docling_parser.parse, source)
                except Exception as exc:
                    # If docling isn't installed or parsing fails, keep the original file.
                    return ParsedContent(
                        item_type="file_upload",
                        title=upload.filename or target.name,
                        raw_content="",
                        normalized_text="",
                        metadata={
                            "parse_status": "failed",
                            "file_suffix": suffix or "unknown",
                            "original_file_name": upload.filename or target.name,
                            "stored_file_path": str(target),
                            "parse_error": f"{type(exc).__name__}: {exc}",
                        },
                    )
            elif suffix == ".doc":
                try:
                    parsed = await anyio.to_thread.run_sync(self.doc_parser.parse, source)
                except Exception as exc:
                    # Keep the original file even if parsing fails (bad file, unsupported on this machine, etc).
                    return ParsedContent(
                        item_type="file_upload",
                        title=upload.filename or target.name,
                        raw_content="",
                        normalized_text="",
                        metadata={
                            "parse_status": "failed",
                            "file_suffix": suffix or "unknown",
                            "original_file_name": upload.filename or target.name,
                            "stored_file_path": str(target),
                            "parse_error": f"{type(exc).__name__}: {exc}",
                    },
                )
            else:
                # Keep the original file even if we can't parse it yet.
                return ParsedContent(
                    item_type="file_upload",
                    title=upload.filename or target.name,
                    raw_content="",
                    normalized_text="",
                    metadata={
                        "parse_status": "unsupported",
                        "file_suffix": suffix or "unknown",
                        "original_file_name": upload.filename or target.name,
                        "stored_file_path": str(target),
                    },
                )
            parsed.metadata["stored_file_path"] = str(target)
            parsed.metadata["original_file_name"] = upload.filename or target.name
            return parsed
        if text is None or not text.strip():
            raise ValueError("Either text or a supported file is required.")
        stripped = text.strip()
        if self._looks_like_url(stripped):
            return self.link_parser.parse(stripped)
        return self.text_parser.parse(stripped)

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
    def _chunk_text(text: str) -> list[str]:
        parts = [part.strip() for part in text.split("\n\n") if part.strip()]
        if parts:
            return parts
        return [text.strip()] if text.strip() else []

    @staticmethod
    def _build_locator_hint(parsed: ParsedContent) -> str | None:
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
