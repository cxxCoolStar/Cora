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
            "Rewrite the user's query into a small set of search keywords that maximize recall.\n\n"
            "Goal:\n"
            "- Produce keywords that are likely to literally appear in stored notes/files.\n"
            "- Do light expansion with Chinese/English synonyms so the search can match both languages.\n\n"
            "Rules:\n"
            "1. Remove filler words like '请', '帮我', '告诉我', '一下', '能不能', '的信息'\n"
            "2. Keep concrete entities and technical terms (product names, topics, protocols, IDs, file names)\n"
            "3. Expand bilingual variants when helpful:\n"
            "   - If the query contains Chinese terms, add common English equivalents (e.g. 内网 -> intranet)\n"
            "   - If the query contains English terms, add common Chinese equivalents (e.g. server -> 服务器)\n"
            "   - Include common abbreviations/aliases if they are standard (e.g. Retrieval-Augmented Generation -> RAG)\n"
            "4. Do NOT add unrelated concepts; only expansions that preserve the same intent\n"
            "5. Return 2-8 keywords (deduplicated). Prefer shorter keyword tokens\n\n"
            "Examples:\n"
            '- "帮我找一下linux服务器的信息" -> ["linux", "服务器", "server"]\n'
            '- "我之前保存的面试题" -> ["面试题", "interview questions"]\n'
            '- "关于Agent和RAG的资料" -> ["agent", "智能体", "rag", "检索增强生成"]\n'
            '- "帮我找一下内网的信息" -> ["内网", "intranet"]\n\n'
            "Respond with strict JSON only using keys: keywords (array of strings), reasoning (string)."
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
