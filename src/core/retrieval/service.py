from __future__ import annotations

from dataclasses import dataclass
import re

from core.storage.models import ItemChunkRecord, ItemRecord
from core.storage.repositories import ItemChunkRepository, ItemRepository


@dataclass(slots=True)
class RetrievalResult:
    item: ItemRecord
    matched_chunk: ItemChunkRecord | None
    score: int


class RetrievalService:
    STOP_TOKENS = {
        "请",
        "告诉我",
        "告诉",
        "我",
        "一下",
        "帮我",
        "帮",
        "看看",
        "这个",
        "那个",
        "多少",
        "是什么",
    }

    def __init__(self, *, item_repository: ItemRepository, item_chunk_repository: ItemChunkRepository) -> None:
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository

    def search(self, *, session_id: str, query: str) -> RetrievalResult | None:
        tokens = self._tokenize(query)
        items = self.item_repository.list_all()
        if not items:
            return None

        item_scores: list[tuple[ItemRecord, int]] = []
        for item in items:
            haystack = " ".join(
                [
                    item.title or "",
                    item.summary or "",
                    item.normalized_text or "",
                    item.locator_hint or "",
                    " ".join((item.metadata_json or {}).get("tags", [])),
                ]
            ).lower()
            score = self._score_text(haystack, tokens)
            if score > 0:
                item_scores.append((item, score))

        if not item_scores:
            fallback = self.item_repository.search_latest_by_text(
                session_id=session_id,
                query=self._normalize_query_for_fallback(query),
            )
            if fallback is None:
                return None
            matched_chunk = self._best_chunk_for_item(item_id=fallback.id, query=query)
            return RetrievalResult(item=fallback, matched_chunk=matched_chunk, score=1)

        item_scores.sort(key=lambda pair: pair[1], reverse=True)
        best_item, best_score = item_scores[0]
        matched_chunk = self._best_chunk_for_item(item_id=best_item.id, query=query)
        return RetrievalResult(item=best_item, matched_chunk=matched_chunk, score=best_score)

    def _best_chunk_for_item(self, *, item_id: str, query: str) -> ItemChunkRecord | None:
        chunks = self.item_chunk_repository.list_by_item_ids(item_ids=[item_id])
        tokens = self._tokenize(query)
        best_chunk: ItemChunkRecord | None = None
        best_score = -1
        for chunk in chunks:
            score = self._score_text(chunk.content.lower(), tokens)
            if score > best_score:
                best_score = score
                best_chunk = chunk
        return best_chunk

    @staticmethod
    def _score_text(text: str, tokens: list[str]) -> int:
        if not tokens:
            return 0
        score = 0
        for token in tokens:
            if token and token in text:
                score += max(1, text.count(token))
        return score

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        lowered = query.lower().strip()
        tokens = [token for token in re.split(r"[\s,，。！？：:\-_/]+", lowered) if token]
        if any("\u4e00" <= char <= "\u9fff" for char in lowered):
            compact = "".join(tokens)
            char_tokens = [char for char in compact if "\u4e00" <= char <= "\u9fff"]
            tokens.extend(char_tokens)
        deduped = list(dict.fromkeys(tokens))
        return [token for token in deduped if token not in RetrievalService.STOP_TOKENS]

    @staticmethod
    def _normalize_query_for_fallback(query: str) -> str:
        text = query
        for token in RetrievalService.STOP_TOKENS:
            text = text.replace(token, " ")
        normalized = " ".join(text.split())
        return normalized or query
