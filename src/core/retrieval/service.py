from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from core.retrieval.query_rewriter import QueryRewriter, RewrittenQuery
from core.storage.models import ItemChunkRecord, ItemRecord
from core.storage.repositories import ItemChunkRepository, ItemRepository


@dataclass(slots=True)
class RetrievalResult:
    item: ItemRecord
    matched_chunk: ItemChunkRecord | None
    score: int


@dataclass(slots=True)
class ItemScoreDebug:
    item_id: str
    title: str
    score: int
    matched_text_preview: str


@dataclass(slots=True)
class RetrievalDebugResult:
    original_query: str
    rewritten_keywords: list[str] | None
    rewrite_reasoning: str | None
    tokens_used_for_search: list[str]
    items_scored: list[ItemScoreDebug]
    selected_item: ItemRecord | None
    selected_chunk: ItemChunkRecord | None
    final_score: int | None
    error: str | None = None


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

    def __init__(
        self,
        *,
        item_repository: ItemRepository,
        item_chunk_repository: ItemChunkRepository,
        query_rewriter: QueryRewriter | None = None,
    ) -> None:
        self.item_repository = item_repository
        self.item_chunk_repository = item_chunk_repository
        self.query_rewriter = query_rewriter

    def search(self, *, session_id: str, query: str) -> RetrievalResult | None:
        if self.query_rewriter is None:
            raise RuntimeError("QueryRewriter is required but not configured")

        rewritten = self.query_rewriter.rewrite(query=query)
        if rewritten is None or not rewritten.keywords:
            raise RuntimeError("Failed to extract search keywords from query")

        tokens = [k for k in rewritten.keywords if k not in self.STOP_TOKENS]
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

    def search_debug(self, *, session_id: str, query: str) -> RetrievalDebugResult:
        """Debug version of search that returns detailed trace of the retrieval pipeline."""
        result = RetrievalDebugResult(
            original_query=query,
            rewritten_keywords=None,
            rewrite_reasoning=None,
            tokens_used_for_search=[],
            items_scored=[],
            selected_item=None,
            selected_chunk=None,
            final_score=None,
        )

        if self.query_rewriter is None:
            result.error = "QueryRewriter is required but not configured"
            return result

        # Step 1: Query Rewriting
        rewritten = self.query_rewriter.rewrite(query=query)
        if rewritten is None:
            result.error = "Failed to rewrite query - LLM returned None"
            return result

        result.rewritten_keywords = rewritten.keywords
        result.rewrite_reasoning = rewritten.reasoning

        if not rewritten.keywords:
            result.error = "QueryRewriter returned empty keywords"
            return result

        tokens = [k for k in rewritten.keywords if k not in self.STOP_TOKENS]
        result.tokens_used_for_search = tokens

        if not tokens:
            result.error = "No valid tokens after filtering stop words"
            return result

        # Step 2: Retrieve and score all items
        items = self.item_repository.list_all()
        if not items:
            result.error = "No items in the database"
            return result

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
            preview = haystack[:200] + "..." if len(haystack) > 200 else haystack
            result.items_scored.append(
                ItemScoreDebug(
                    item_id=item.id,
                    title=item.title or "(no title)",
                    score=score,
                    matched_text_preview=preview,
                )
            )
            if score > 0:
                item_scores.append((item, score))

        # Sort by score descending
        result.items_scored.sort(key=lambda x: x.score, reverse=True)

        if not item_scores:
            result.error = "No items matched the search tokens"
            return result

        # Step 3: Select best item
        item_scores.sort(key=lambda pair: pair[1], reverse=True)
        best_item, best_score = item_scores[0]
        result.selected_item = best_item
        result.final_score = best_score

        # Step 4: Find best chunk
        matched_chunk = self._best_chunk_for_item(item_id=best_item.id, query=query)
        result.selected_chunk = matched_chunk

        return result

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
        matched_tokens = 0
        for token in tokens:
            if token and token in text:
                matched_tokens += 1
                weight = len(token) ** 2
                score += weight * text.count(token)
        if matched_tokens == 0:
            return 0
        return score

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        lowered = query.lower().strip()
        raw_tokens = [t for t in re.split(r"[\s,，。！？：:\-_/]+", lowered) if t]
        tokens: list[str] = []
        for token in raw_tokens:
            if any("\u4e00" <= ch <= "\u9fff" for ch in token):
                # Keep the original token (likely a word) + extract meaningful n-grams
                tokens.append(token)
                # Add 2-char and 3-grams for better Chinese matching
                for length in (3, 2):
                    for i in range(len(token) - length + 1):
                        ngram = token[i : i + length]
                        if not all(ch in RetrievalService.STOP_TOKENS or len(ch) == 1 and ch in "的去是来了一有了在和到也么而且但是这不就出就会对说不能为" for ch in ngram):
                            tokens.append(ngram)
            else:
                tokens.append(token)
        deduped = list(dict.fromkeys(tokens))
        return [t for t in deduped if t not in RetrievalService.STOP_TOKENS and len(t) >= 2]

    @staticmethod
    def _normalize_query_for_fallback(query: str) -> str:
        text = query
        for token in RetrievalService.STOP_TOKENS:
            text = text.replace(token, " ")
        normalized = " ".join(text.split())
        return normalized or query
