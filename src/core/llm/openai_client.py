from __future__ import annotations

import json
from typing import Any

import httpx

from core.llm.base import ModelClient
from core.llm.http_utils import build_httpx_client, post_json_with_retries
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolSpec


class OpenAIChatModelClient(ModelClient):
    """OpenAI-compatible Chat Completions adapter with tool calling support."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
        trust_env: bool = False,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("`api_key` is required for OpenAIChatModelClient.")
        if not model:
            raise ValueError("`model` is required for OpenAIChatModelClient.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self._client = http_client or build_httpx_client(timeout=timeout, trust_env=trust_env)

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": [self._message_to_payload(message) for message in messages],
            "tools": [self._tool_to_payload(tool) for tool in tools],
            "tool_choice": "auto",
        }
        response = post_json_with_retries(
            self._client,
            url=f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json_payload=payload,
            max_attempts=self.max_attempts,
        )
        data = response.json()
        return self._parse_response(data)

    @staticmethod
    def _message_to_payload(message: Message) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = message.tool_calls
        if message.role == "tool":
            payload["tool_call_id"] = message.tool_call_id
        if message.name:
            payload["name"] = message.name
        return payload

    @staticmethod
    def _tool_to_payload(tool: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ModelResponse:
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Model response did not contain any choices.")
        message = choices[0].get("message", {})
        raw_tool_calls = message.get("tool_calls", []) or []
        tool_calls = []
        for item in raw_tool_calls:
            function = item.get("function", {})
            arguments_text = function.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError:
                arguments = {"raw_arguments": arguments_text}
            tool_calls.append(
                ToolCall(
                    id=item.get("id") or "",
                    tool_name=function.get("name") or "",
                    arguments=arguments,
                )
            )
        return ModelResponse(
            assistant_text=message.get("content"),
            tool_calls=tool_calls,
            raw_response=data,
            usage=data.get("usage", {}),
        )
