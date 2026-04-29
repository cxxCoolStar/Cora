from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import uuid

from fastapi import UploadFile

from core.ingestion.parsers.base import FileSource, ParsedContent
from core.ingestion.parsers.docx_parser import DocxFileParser
from core.ingestion.parsers.link_parser import LinkParser
from core.ingestion.parsers.text_parser import TextParser
from core.ingestion.parsers.txt_parser import TxtFileParser
from core.storage.repositories import ItemChunkRepository, ItemRepository, MessageRepository, UserSignalRepository


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
        self.docx_parser = DocxFileParser()

    async def ingest(
        self,
        *,
        session_id: str,
        source_message_id: str,
        text: str | None,
        upload: UploadFile | None,
    ) -> IngestedItemResult:
        parsed = await self._parse_input(text=text, upload=upload)
        summary = self._summarize(parsed.normalized_text)
        tags = self._extract_tags(parsed.normalized_text, parsed.item_type)
        locator_hint = self._build_locator_hint(parsed)
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
        )
        self._record_user_signals(
            session_id=session_id,
            item_id=item.id,
            item_type=parsed.item_type,
            tags=tags,
            title=item.title,
        )
        for index, chunk in enumerate(self._chunk_text(parsed.normalized_text)):
            self.item_chunk_repository.create(
                item_id=item.id,
                chunk_index=index,
                content=chunk,
                metadata={"title": item.title},
            )
        reply = f"Saved `{item.title}` as a {item.item_type.replace('_', ' ')}. Summary: {summary}"
        return IngestedItemResult(item_id=item.id, reply=reply)

    async def _parse_input(self, *, text: str | None, upload: UploadFile | None) -> ParsedContent:
        has_real_upload = upload is not None and bool((upload.filename or "").strip())
        if has_real_upload:
            suffix = Path(upload.filename or "").suffix.lower()
            target = self.storage_dir / f"{uuid.uuid4()}_{upload.filename}"
            data = await upload.read()
            target.write_bytes(data)
            source = FileSource(path=target, filename=upload.filename or target.name)
            if suffix == ".txt":
                parsed = self.txt_parser.parse(source)
            elif suffix == ".docx":
                parsed = self.docx_parser.parse(source)
            else:
                raise ValueError(f"Unsupported file type: {suffix or 'unknown'}")
            parsed.metadata["stored_file_path"] = str(target)
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
