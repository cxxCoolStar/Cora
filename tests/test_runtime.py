from __future__ import annotations

from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall

from conftest import FakeModelClient


def test_runtime_returns_direct_response(agent_factory):
    model = FakeModelClient([ModelResponse(assistant_text="hello from core")])
    agent = agent_factory(model)
    session_id = agent.start_session()

    result = agent.run_turn(session_id=session_id, user_input="hello")

    assert result.response_text == "hello from core"
    assert result.tool_calls == 0
    assert len(model.calls) == 1


def test_runtime_executes_tool_call(agent_factory):
    model = FakeModelClient(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(tool_name="echo", arguments={"text": "ping"}),
                ]
            ),
            ModelResponse(assistant_text="tool completed"),
        ]
    )
    agent = agent_factory(model)
    session_id = agent.start_session()

    result = agent.run_turn(session_id=session_id, user_input="use a tool")

    assert result.response_text == "tool completed"
    assert result.tool_calls == 1
    assert len(model.calls) == 2
    tool_messages = [message for message in model.calls[-1] if message.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "ping"


def test_runtime_recovers_from_unknown_tool(agent_factory):
    model = FakeModelClient(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(tool_name="missing_tool", arguments={}),
                ]
            ),
            ModelResponse(assistant_text="I could not use that tool."),
        ]
    )
    agent = agent_factory(model)
    session_id = agent.start_session()

    result = agent.run_turn(session_id=session_id, user_input="call the missing tool")

    assert result.response_text == "I could not use that tool."
    assert result.tool_calls == 1


def test_summary_memory_is_created_after_threshold(agent_factory):
    model = FakeModelClient(
        [
            ModelResponse(assistant_text="turn 1"),
            ModelResponse(assistant_text="turn 2"),
        ]
    )
    agent = agent_factory(model)
    session_id = agent.start_session()

    agent.run_turn(session_id=session_id, user_input="hello")
    agent.run_turn(session_id=session_id, user_input="hello again")

    summary = agent.runtime.summary_memory.get_summary(session_id=session_id)
    assert summary is not None
    assert "user: hello" in summary
