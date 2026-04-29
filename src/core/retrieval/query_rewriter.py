from __future__ import annotations

import json
from dataclasses import dataclass

from core.llm.base import ModelClient
from core.schemas.message import Message


@dataclass(slots=True)
class RewrittenQuery:
    keywords: list[str]
    reasoning: str


class QueryRewriter:
    """Uses LLM to rewrite user queries into search keywords."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def rewrite(self, *, query: str) -> RewrittenQuery | None:
        """Extract search keywords from user query using LLM."""
        prompt = (
            "You are helping to search a personal archive. "
            "Extract key search terms from the user's query that would help find relevant content.\n\n"
            "Rules:\n"
            "1. Extract only concrete keywords that would appear in stored documents\n"
            "2. Remove filler words like '请', '帮我', '告诉我', '的信息'\n"
            "3. Keep technical terms, names, topics as-is\n"
            "4. Return 1-5 most relevant keywords\n\n"
            "Examples:\n"
            '- "帮我找一下linux服务器的信息" -> ["linux", "服务器"]\n'
            '- "我之前保存的面试题" -> ["面试题"]\n'
            '- "关于Agent和RAG的资料" -> ["Agent", "RAG"]\n\n'
            "Respond with strict JSON using keys: keywords (array of strings), reasoning (string)."
        )
        response = self.model_client.generate(
            messages=[
                Message.system(session_id="query-rewriter", content=prompt),
                Message.user(session_id="query-rewriter", content=query),
            ],
            tools=[],
        )
        content = (response.assistant_text or "").strip()
        if not content:
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            fenced = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                payload = json.loads(fenced)
            except json.JSONDecodeError:
                return None
        keywords = payload.get("keywords", [])
        if not isinstance(keywords, list):
            return None
        keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        if not keywords:
            return None
        return RewrittenQuery(
            keywords=keywords,
            reasoning=str(payload.get("reasoning", "")),
        )
