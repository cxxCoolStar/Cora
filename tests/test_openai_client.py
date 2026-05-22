from __future__ import annotations

import httpx

from core.llm.openai_client import OpenAIChatModelClient
from core.schemas.message import Message
from core.schemas.tool import ToolSpec


def test_openai_client_parses_direct_response():
    response_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                }
            }
        ],
        "usage": {"total_tokens": 12},
    }

    parsed = OpenAIChatModelClient._parse_response(response_data)

    assert parsed.assistant_text == "hello"
    assert parsed.tool_calls == []
    assert parsed.usage["total_tokens"] == 12


def test_openai_client_parses_tool_calls():
    response_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": "{\"text\":\"ping\"}",
                            },
                        }
                    ],
                }
            }
        ]
    }

    parsed = OpenAIChatModelClient._parse_response(response_data)

    assert parsed.assistant_text is None
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].id == "call_123"
    assert parsed.tool_calls[0].tool_name == "echo"
    assert parsed.tool_calls[0].arguments == {"text": "ping"}


def test_openai_client_sends_tools_and_messages():
    captured_request: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        captured_request["headers"] = dict(request.headers)
        captured_request["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "done",
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    model = OpenAIChatModelClient(
        api_key="test-key",
        model="gpt-test",
        base_url="https://example.com/v1",
        http_client=client,
    )

    result = model.generate(
        messages=[Message.user(session_id="session-1", content="hello")],
        tools=[
            ToolSpec(
                name="echo",
                description="Echo input",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            )
        ],
    )

    assert result.assistant_text == "done"
    assert captured_request["url"] == "https://example.com/v1/chat/completions"
    assert captured_request["json"]["model"] == "gpt-test"
    assert captured_request["json"]["tool_choice"] == "auto"
    assert captured_request["json"]["messages"][0]["role"] == "user"
    assert captured_request["json"]["tools"][0]["function"]["name"] == "echo"


def test_openai_client_sends_assistant_tool_call_messages():
    captured_request: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "done",
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    model = OpenAIChatModelClient(
        api_key="test-key",
        model="gpt-test",
        base_url="https://example.com/v1",
        http_client=client,
    )

    result = model.generate(
        messages=[
            Message.assistant_tool_calls(
                session_id="session-1",
                content="Opening topic",
                tool_calls=[
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "archive",
                            "arguments": "{\"action\":\"open\",\"query\":\"内网\"}",
                        },
                    }
                ],
            ),
            Message.tool(
                session_id="session-1",
                name="archive",
                tool_call_id="call_123",
                content='{"reply":"ok"}',
            ),
        ],
        tools=[],
    )

    assert result.assistant_text == "done"
    assert captured_request["json"]["messages"][0]["tool_calls"][0]["function"]["name"] == "archive"
    assert captured_request["json"]["messages"][1]["tool_call_id"] == "call_123"


def test_openai_client_sends_json_response_format_without_tools():
    captured_request: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "{\"plan_id\":\"plan-1\"}",
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    model = OpenAIChatModelClient(
        api_key="test-key",
        model="gpt-test",
        base_url="https://example.com/v1",
        http_client=client,
    )

    result = model.generate(
        messages=[Message.user(session_id="session-1", content="[Planner mode]")],
        tools=[],
        response_format="json_object",
    )

    assert result.assistant_text == "{\"plan_id\":\"plan-1\"}"
    assert captured_request["json"]["response_format"] == {"type": "json_object"}
    assert "tools" not in captured_request["json"]
