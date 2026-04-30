from __future__ import annotations

import json
from dataclasses import dataclass

from core.llm.base import ModelClient
from core.schemas.message import Message


@dataclass(slots=True)
class RewrittenQuery:
    keywords: list[str]
    reasoning: str
    core_entities: list[str]
    supporting_terms: list[str]
    negative_terms: list[str]


class QueryRewriter:
    """Uses LLM to rewrite user queries into search keywords."""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def rewrite(self, *, query: str) -> RewrittenQuery | None:
        """Extract search keywords from user query using LLM."""
        prompt = (
            "You are helping to search a personal archive. "
            "Rewrite the user's query into structured retrieval hints.\n\n"
            "Goal:\n"
            "- Extract the core entities the user really wants.\n"
            "- Add light Chinese/English expansion to improve recall.\n"
            "- Avoid over-general words that match everything.\n\n"
            "Rules:\n"
            "1. Remove filler words like '请', '帮我', '告诉我', '一下', '能不能', '的信息'\n"
            "2. Keep concrete entities and technical terms (product names, topics, protocols, IDs, file names)\n"
            "3. Expand bilingual variants when helpful:\n"
            "   - If the query contains Chinese terms, add common English equivalents (e.g. 内网 -> intranet)\n"
            "   - If the query contains English terms, add common Chinese equivalents (e.g. server -> 服务器)\n"
            "   - Include common abbreviations/aliases if they are standard (e.g. Retrieval-Augmented Generation -> RAG)\n"
            "4. Do NOT add unrelated concepts; only expansions that preserve the same intent\n"
            "5. Prefer precise entities over generic words like 文件/资料/内容/data/document\n"
            "6. Return JSON with these keys:\n"
            "   - core_entities: 1-4 strong entity terms that best represent user intent\n"
            "   - supporting_terms: 0-6 secondary useful terms/synonyms\n"
            "   - negative_terms: 0-4 terms that are likely unrelated themes to down-rank (not hard block)\n"
            "   - reasoning: short explanation\n\n"
            "Examples:\n"
            '- query: "帮我找一下linux服务器的信息"\n'
            '  core_entities: ["linux", "服务器"]\n'
            '  supporting_terms: ["server"]\n'
            '  negative_terms: []\n'
            '- query: "帮我找一下内网的信息"\n'
            '  core_entities: ["内网"]\n'
            '  supporting_terms: ["intranet"]\n'
            '  negative_terms: ["面试"]\n\n'
            "Output strict JSON only."
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
        core_entities = payload.get("core_entities", [])
        supporting_terms = payload.get("supporting_terms", [])
        negative_terms = payload.get("negative_terms", [])
        legacy_keywords = payload.get("keywords", [])
        if not isinstance(core_entities, list):
            core_entities = []
        if not isinstance(supporting_terms, list):
            supporting_terms = []
        if not isinstance(negative_terms, list):
            negative_terms = []
        if not core_entities and not supporting_terms and isinstance(legacy_keywords, list):
            supporting_terms = legacy_keywords

        core_entities = [str(k).strip().lower() for k in core_entities if str(k).strip()]
        supporting_terms = [str(k).strip().lower() for k in supporting_terms if str(k).strip()]
        negative_terms = [str(k).strip().lower() for k in negative_terms if str(k).strip()]
        keywords = list(dict.fromkeys([*core_entities, *supporting_terms]))
        if not keywords and not negative_terms:
            return None
        return RewrittenQuery(
            keywords=keywords,
            reasoning=str(payload.get("reasoning", "")),
            core_entities=core_entities,
            supporting_terms=supporting_terms,
            negative_terms=negative_terms,
        )
