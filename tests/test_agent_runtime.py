from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.agent.context_budget import ContextBudgetManager
from core.agent.loop import AgentLoop
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_state import ConversationRuntimeState
from core.agent.skill_loader import SkillLoader
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolResult, ToolSpec


class StubModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0)


@dataclass
class StubExecutor:
    results: list[ToolResult]

    async def execute(self, *, session_id: str, tool_call: ToolCall, runtime: ConversationRuntimeState) -> ToolResult:
        if not self.results:
            raise AssertionError("No stub tool results left")
        return self.results.pop(0)


def test_skill_loader_reads_archive_core_skill() -> None:
    loader = SkillLoader()
    skill = loader.find_skill("archive-core")
    assert skill is not None
    assert skill.name == "archive-core"
    assert "archive contract" in skill.description.lower()
    assert skill.path.name == "archive-core"


def test_prompt_builder_includes_runtime_and_skill_summary() -> None:
    loader = SkillLoader()
    skills = loader.list_skills()
    runtime = ConversationRuntimeState(session_id="session-1", last_action="archive.save")
    builder = AgentPromptBuilder()

    messages = builder.build_messages(
        session_id="session-1",
        user_text="save this photo",
        runtime=runtime,
        skills=skills,
        history=[],
        upload_name="wechat_image.jpg",
    )

    assert messages[0].role == "system"
    assert "Runtime state:" in messages[0].content
    assert "archive-core" in messages[0].content
    assert messages[-1].content == "save this photo"


@pytest.mark.anyio
async def test_agent_loop_executes_tool_call_and_returns_final_text() -> None:
    runtime = ConversationRuntimeState(session_id="session-1")
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="archive", arguments={"action": "save"})],
            ),
            ModelResponse(assistant_text="Saved to the archive.", tool_calls=[]),
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="Saved wechat_image.jpg under personal-photos.",
                metadata={"action": "capture"},
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="archive", description="archive tool", input_schema={})],
    )

    result = await loop.run(
        session_id="session-1",
        initial_messages=[Message.user(session_id="session-1", content="save this photo")],
        runtime=runtime,
    )

    assert result.final_response == "Saved to the archive."
    assert result.exit_reason == "assistant_text"
    assert [message.role for message in result.trace] == ["assistant", "tool", "assistant"]


def test_context_budget_manager_calibrates_against_actual_prompt_tokens() -> None:
    manager = ContextBudgetManager(
        context_length=8192,
        compression_threshold=0.25,
        summary_target_ratio=0.10,
        protect_last_n_min=2,
    )
    messages = [
        Message.user(session_id="budget-session", content=("历史内容" * 120) + str(index))
        for index in range(8)
    ]
    tools = [ToolSpec(name="archive", description="archive tool", input_schema={"type": "object", "properties": {"action": {"type": "string"}}})]

    decision_before = manager.choose_recent_slice(messages=messages)
    estimated_prompt_tokens = manager.estimate_prompt_tokens(messages=messages, tools=tools, calibrated=False)
    manager.observe_prompt_usage(
        estimated_prompt_tokens=estimated_prompt_tokens,
        actual_prompt_tokens=estimated_prompt_tokens * 3,
    )
    decision_after = manager.choose_recent_slice(messages=messages)

    assert manager.prompt_token_scale > 1.0
    assert manager.last_estimated_prompt_tokens == estimated_prompt_tokens
    assert manager.last_actual_prompt_tokens == estimated_prompt_tokens * 3
    assert decision_after.tail_budget_tokens < decision_before.tail_budget_tokens
    assert decision_after.recent_start_index >= decision_before.recent_start_index


@pytest.mark.anyio
async def test_orchestrator_builds_messages_and_runs_loop() -> None:
    runtime = ConversationRuntimeState(session_id="session-2")
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[],
    )
    orchestrator = AgentOrchestrator(
        loop=loop,
        prompt_builder=AgentPromptBuilder(),
        skill_loader=SkillLoader(skill_roots=[Path(__file__).resolve().parents[1] / "skills"]),
    )

    result = await orchestrator.handle_turn(
        OrchestratorInput(
            session_id="session-2",
            user_text="hello",
            runtime=runtime,
        )
    )

    assert result.final_response == "Ready."
    first_call_messages = model.calls[0]
    assert first_call_messages[0].role == "system"
    assert "archive-core" in first_call_messages[0].content


@pytest.mark.anyio
async def test_agent_loop_updates_budget_manager_from_response_usage() -> None:
    runtime = ConversationRuntimeState(session_id="session-usage")
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="Ready.",
                tool_calls=[],
                usage={"prompt_tokens": 900},
            )
        ]
    )
    executor = StubExecutor(results=[])
    budget_manager = ContextBudgetManager(
        context_length=8192,
        compression_threshold=0.25,
        summary_target_ratio=0.10,
        protect_last_n_min=2,
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="archive", description="archive tool", input_schema={"type": "object"})],
        context_budget_manager=budget_manager,
    )

    await loop.run(
        session_id="session-usage",
        initial_messages=[Message.user(session_id="session-usage", content="请帮我保存这份很长很长的材料" * 40)],
        runtime=runtime,
    )

    assert budget_manager.last_actual_prompt_tokens == 900
    assert budget_manager.last_estimated_prompt_tokens > 0
    assert budget_manager.prompt_token_scale > 1.0
