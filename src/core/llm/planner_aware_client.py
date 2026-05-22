from __future__ import annotations

from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolSpec


class PlannerAwareModelClient(ModelClient):
    """Forwards planner JSON mode to delegates that support ``response_format``."""

    def __init__(self, delegate: ModelClient) -> None:
        self.delegate = delegate

    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        response_format: str | None = None,
    ) -> ModelResponse:
        if response_format:
            try:
                return self.delegate.generate(
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                )
            except TypeError:
                pass
        return self.delegate.generate(messages=messages, tools=tools)
