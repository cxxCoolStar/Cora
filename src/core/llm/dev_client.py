from __future__ import annotations

import json
import uuid

from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolSpec


class DevelopmentModelClient(ModelClient):
    """Simple local model stub for early development and manual testing."""

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None

        if latest_tool is not None:
            tool_name = latest_tool.name or "tool"
            return ModelResponse(assistant_text=f"Tool `{tool_name}` returned: {latest_tool.content}")

        if latest_user is None:
            return ModelResponse(assistant_text="How can I help?")

        text = latest_user.content.strip()
        if text.startswith("/tool "):
            _, _, remainder = text.partition("/tool ")
            name, _, payload = remainder.partition(" ")
            arguments = {}
            if payload.strip():
                arguments = json.loads(payload)
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=str(uuid.uuid4()),
                        tool_name=name,
                        arguments=arguments,
                    )
                ]
            )

        return ModelResponse(assistant_text=f"You said: {text}")
