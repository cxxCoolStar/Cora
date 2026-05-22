from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile

from core.agent.context_budget import ContextBudgetManager
from core.agent.context_manager import SessionContextManager
from core.agent.execution_policy import DIRECT_TOOL_PLAN_MODE, ExecutionPolicyResolver
from core.agent.harness import DefaultAgentHarness, new_run_input
from core.agent.loop import AgentLoop, LoopResult, ToolExecutionTrace
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.run_records import InMemoryAgentRunRecordRepository
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, RuntimeContextSnapshot, RuntimeStateDelta
from core.agent.skill_protocol import PendingRequest, PendingStateDelta, SkillExecutionResult
from core.agent.session_runtime import SessionRuntimeSnapshotLoader
from core.agent.skill_loader import SkillLoader
from core.agent.tool_policy import allow_tool_policy_decision, deny_tool_policy_decision
from core.agent.hitl_store import InMemoryHitlStore
from core.agent.tool_policy_engine import (
    ToolPolicyEngine,
    effective_allowed_tool_names,
    effective_denied_tool_names,
    has_runtime_tool_governance,
    requires_hitl_confirmation,
    requires_sandbox_execution,
    resolve_platform_name,
)
from core.schemas.tool_policy import ToolPolicyContext
from core.agent.turn_runner import AgentTurnRunner
from core.clawbot import RuntimeToolExecutor
from core.clawbot.planner import ToolPlan
from core.clawbot.source_events import SourceEventManager
from core.clawbot.tools import ToolExecutionResult
from core.schemas.execution import ExecutionHints
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolResult, ToolSpec
from core.schemas.harness import HarnessRunInput, HarnessTraceEventType, RunBudget, RunTraceEvent
from core.storage.db import DatabaseManager
from core.storage.repositories import ItemRepository, MessageRepository, PendingStateRepository, SessionRepository, SqlAgentRunRecordRepository, UserSignalRepository
from core.tools import ToolInvocation


class StubModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []
        self.tools_seen: list[list[str]] = []

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.calls.append(list(messages))
        self.tools_seen.append([tool.name for tool in tools])
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0)


@dataclass
class StubExecutor:
    results: list[ToolResult]

    async def execute_tool_call(self, *, session_id: str, tool_call: ToolCall, runtime: ConversationRuntimeState) -> ToolResult:
        if not self.results:
            raise AssertionError("No stub tool results left")
        return self.results.pop(0)


class SandboxCapturingExecutor:
    captured_metadata: list[dict[str, object]]

    def __init__(self) -> None:
        self.captured_metadata = []

    async def execute_tool_call(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        runtime: ConversationRuntimeState,
    ) -> ToolResult:
        self.captured_metadata.append(dict(runtime.metadata))
        return ToolResult(success=True, content="sandbox ok")


class SlowExecutor:
    async def execute_tool_call(self, *, session_id: str, tool_call: ToolCall, runtime: ConversationRuntimeState) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(success=True, content="Too late.")


class StubPendingStateRepository:
    def get_latest_pending(self, *, session_id: str):
        return None


@dataclass
class SpyHarness:
    result: LoopResult
    inputs: list[HarnessRunInput]
    execution_policy: Any = None
    id: str = "spy-harness"

    async def run(self, *, run_input: HarnessRunInput) -> LoopResult:
        self.inputs.append(run_input)
        return self.result


@dataclass
class StubMessageRepository:
    context: dict

    def get_latest_assistant_context(self, *, session_id: str) -> dict:
        return dict(self.context)


@dataclass
class StubSessionRecord:
    session_kind: str = "conversation"
    metadata_json: dict[str, Any] | None = None


@dataclass
class StubSessionRepository:
    session: StubSessionRecord

    def get(self, session_id: str) -> StubSessionRecord:
        return self.session


@dataclass
class StubSourceEventRecord:
    id: str
    event_type: str
    channel: str
    raw_text: str
    original_file_name: str | None
    mime_type: str | None
    created_at: Any


@dataclass
class StubSourceEventRepository:
    events: list[StubSourceEventRecord]
    created_payloads: list[dict] | None = None

    def __post_init__(self) -> None:
        if self.created_payloads is None:
            self.created_payloads = []

    def list_by_session(self, *, session_id: str, limit: int = 5) -> list[StubSourceEventRecord]:
        return self.events[:limit]

    def create(self, **kwargs):
        assert self.created_payloads is not None
        self.created_payloads.append(kwargs)
        return type("CreatedSourceEvent", (), {"id": "evt-created"})()


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
    runtime = ConversationRuntimeState(session_id="session-1", last_action="capture")
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
    assert "Execution guidance:" in messages[0].content
    assert "Memory guidance:" in messages[0].content
    assert "Skills guidance:" in messages[0].content
    assert "Runtime summary:" in messages[0].content
    assert "Shared skills summary:" in messages[0].content
    assert "must load it with skill_view(name)" in messages[0].content
    assert "archive-core" in messages[0].content
    assert "coding, local workspace tasks, research, and reusable domain workflows" in messages[0].content
    assert "If the user asks you to create or update a text file and write_file is available" in messages[0].content
    assert "If the user asks you to run a shell or terminal command and shell_exec is available" in messages[0].content
    assert "do not leave them blank" in messages[0].content
    assert messages[-1].content == "save this photo"


def test_prompt_builder_uses_explicit_hermes_lite_section_order(tmp_path: Path) -> None:
    runtime = ConversationRuntimeState(
        session_id="session-ordered",
        last_action="retrieve",
        recent_events=[
            EventSnapshot(
                source_event_id="event-1",
                event_type="message",
                channel="wechat",
                raw_text="把上次那份简历发我",
            )
        ],
    )
    memory_path = tmp_path / "user-memory" / "USER.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("# User Memory\n\n- 用户偏好中文回复。", encoding="utf-8")
    builder = AgentPromptBuilder(user_memory_path=memory_path)

    messages = builder.build_messages(
        session_id="session-ordered",
        user_text="把上次那份简历发我",
        runtime=runtime,
        skills=SkillLoader().list_skills(),
        history=[],
        upload_name="resume.pdf",
        delivery_available=True,
    )

    content = messages[0].content
    section_order = [
        "Execution guidance:",
        "Memory guidance:",
        "Skills guidance:",
        "Platform hints:",
        "Runtime summary:",
        "User memory snapshot:",
        "Shared skills summary:",
        "Upload hint:",
        "Structured conversation state:",
    ]
    positions = [content.index(section) for section in section_order]
    assert positions == sorted(positions)
    assert "Conversation state:\n" in content
    assert '"pending_state": {}' in content
    assert "Do not invent script names, file paths, alternate entrypoints, or unavailable tools." in content
    assert "When a loaded skill defines required input fields or an intent-like action selector" in content
    assert "If the loaded skill defines required input fields, pass them explicitly and do not leave intent-like fields blank." in content
    assert "Prefer generic tools for generic tasks and skills for domain workflows." in content


def test_prompt_builder_includes_wechat_platform_hint_when_delivery_available() -> None:
    runtime = ConversationRuntimeState(
        session_id="session-wechat",
        recent_events=[
            EventSnapshot(
                source_event_id="event-1",
                event_type="message",
                channel="wechat",
                raw_text="把照片发我",
            )
        ],
    )
    builder = AgentPromptBuilder()

    messages = builder.build_messages(
        session_id="session-wechat",
        user_text="把照片发我",
        runtime=runtime,
        skills=[],
        history=[],
        delivery_available=True,
    )

    assert "Platform hints:" in messages[0].content
    assert "You are on WeChat." in messages[0].content
    assert "Do not tell the user that file sending is impossible" in messages[0].content


def test_prompt_builder_includes_background_execution_policy_for_job_sessions() -> None:
    runtime = ConversationRuntimeState(
        session_id="job-session-1",
        session_kind="job_execution",
        session_metadata={"scheduled_task_id": "task-1"},
    )
    builder = AgentPromptBuilder()

    messages = builder.build_messages(
        session_id="job-session-1",
        user_text="Check the latest build status.",
        runtime=runtime,
        skills=[],
        history=[],
    )

    assert "Background execution policy:" in messages[0].content
    assert "This turn is a background scheduled execution" in messages[0].content
    assert "reply exactly with `[SILENT]`" in messages[0].content
    assert "- execution_mode=job_execution" in messages[0].content
    assert "- background_execution=true" in messages[0].content
    assert "- allow_clarification=false" in messages[0].content


def test_execution_policy_resolver_supports_direct_tool_plan_mode_for_job_sessions() -> None:
    resolver = ExecutionPolicyResolver()

    policy = resolver.for_context(
        {
            "session_kind": "job_execution",
            "execution_mode": DIRECT_TOOL_PLAN_MODE,
        }
    )

    assert policy.mode == DIRECT_TOOL_PLAN_MODE
    assert policy.background_execution is True
    assert policy.allow_clarification is False
    assert policy.allows_tool("skill_run") is True
    assert policy.allows_tool("scheduled_tasks") is False
    assert policy.clarify_suppressed_reply == "[SILENT]"


def test_runtime_manager_context_includes_execution_policy_metadata_for_direct_tool_plan() -> None:
    manager = AgentRuntimeManager(pending_state_repository=StubPendingStateRepository())
    runtime = manager.build_runtime_state(
        session_id="job-session-ctx",
        context_snapshot=RuntimeContextSnapshot(session_kind="job_execution"),
        source_message_id="msg-ctx-1",
        raw_text="check the latest item",
        upload=None,
        execution_mode=DIRECT_TOOL_PLAN_MODE,
    )

    context = manager.runtime_to_context(runtime)

    assert runtime.execution_mode == DIRECT_TOOL_PLAN_MODE
    assert context["execution_mode"] == DIRECT_TOOL_PLAN_MODE
    assert context["background_execution"] is True
    assert context["allow_clarification"] is False


def test_prompt_builder_includes_user_memory_when_present(tmp_path: Path) -> None:
    loader = SkillLoader()
    skills = loader.list_skills()
    runtime = ConversationRuntimeState(session_id="session-memory")
    memory_path = tmp_path / "user-memory" / "USER.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("# User Memory\n\n- 用户常买的布洛芬品牌是芬必得。", encoding="utf-8")
    builder = AgentPromptBuilder(user_memory_path=memory_path)

    messages = builder.build_messages(
        session_id="session-memory",
        user_text="我上次买的药叫什么？",
        runtime=runtime,
        skills=skills,
        history=[],
    )

    assert "User memory snapshot:" in messages[0].content
    assert "用户常买的布洛芬品牌是芬必得" in messages[0].content


def test_prompt_builder_skips_empty_user_memory(tmp_path: Path) -> None:
    runtime = ConversationRuntimeState(session_id="session-empty-memory")
    memory_path = tmp_path / "user-memory" / "USER.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(" \n\n", encoding="utf-8")
    builder = AgentPromptBuilder(user_memory_path=memory_path)

    messages = builder.build_messages(
        session_id="session-empty-memory",
        user_text="hello",
        runtime=runtime,
        skills=[],
        history=[],
    )

    assert "User memory snapshot:" not in messages[0].content


def test_session_summary_message_marks_boundary_from_long_term_memory() -> None:
    payload = {
        "covered_message_count": 8,
        "summary": {
            "active_task": "send resume back to user",
            "user_facts": ["User stores job materials in WeChat."],
            "open_loops": ["Need to confirm which resume version to send."],
            "resolved_requests": [],
            "recent_decisions": ["Use archive retrieval before answering."],
            "critical_context": ["Latest candidate item title is resume-v2.pdf."],
        },
    }

    rendered = SessionContextManager._format_summary_message(  # type: ignore[attr-defined]
        payload,
        decision=type("Decision", (), {"tail_budget_tokens": 1024})(),
    )

    assert "This is temporary session context, not long-term user memory." in rendered
    assert "Treat this as background context" in rendered


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
async def test_orchestrator_passes_per_turn_tool_specs_to_loop() -> None:
    runtime = ConversationRuntimeState(session_id="session-2", session_kind="job_execution")
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="scheduled_tasks", description="automation", input_schema={})],
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
            tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
        )
    )

    assert result.final_response == "Ready."
    assert model.tools_seen == [["read_file"]]


@pytest.mark.anyio
async def test_default_agent_harness_runs_single_agent_lifecycle() -> None:
    runtime_manager = AgentRuntimeManager(pending_state_repository=StubPendingStateRepository())
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=runtime_manager,
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner)

    result = await harness.run(
        run_input=new_run_input(
            session_id="session-harness",
            source_message_id="msg-harness",
            user_text="hello",
            raw_text="hello",
            upload=None,
            context_snapshot=RuntimeContextSnapshot(),
        )
    )

    assert result.final_response == "Ready."
    assert [event.event_type for event in harness.trace_events] == [
        HarnessTraceEventType.RUN_STARTED,
        HarnessTraceEventType.PREPARE_COMPLETED,
        HarnessTraceEventType.TOOL_POLICY_APPLIED,
        HarnessTraceEventType.START_COMPLETED,
        HarnessTraceEventType.RESOLVE_COMPLETED,
        HarnessTraceEventType.CLEANUP_COMPLETED,
    ]
    assert [event.sequence for event in harness.trace_events] == [1, 2, 3, 4, 5, 6]
    assert harness.trace_events[1].metadata["tool_count"] == 1
    assert harness.trace_events[2].metadata["tool_surface"] == "full"
    assert harness.trace_events[2].metadata["exposed_tool_names"] == ["read_file"]
    assert harness.execution_policy is not None
    assert harness.execution_policy.mode == "conversation"


@pytest.mark.anyio
async def test_default_agent_harness_records_completed_run() -> None:
    repository = InMemoryAgentRunRecordRepository()
    runtime_manager = AgentRuntimeManager(pending_state_repository=StubPendingStateRepository())
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=runtime_manager,
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-recorded",
        source_message_id="msg-recorded",
        user_text="hello",
        raw_text="hello",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.final_response == "Ready."
    assert record.status == "completed"
    assert record.outcome == "assistant_text"
    assert record.steps == 1
    assert record.completed_at is not None
    assert record.input_metadata == {"has_upload": False, "raw_text_present": True}
    assert [event.event_type for event in record.trace_events] == [
        HarnessTraceEventType.RUN_STARTED,
        HarnessTraceEventType.PREPARE_COMPLETED,
        HarnessTraceEventType.TOOL_POLICY_APPLIED,
        HarnessTraceEventType.START_COMPLETED,
        HarnessTraceEventType.RESOLVE_COMPLETED,
        HarnessTraceEventType.CLEANUP_COMPLETED,
    ]
    assert repository.list_by_session(session_id="session-recorded") == [record]


@pytest.mark.anyio
async def test_default_agent_harness_records_incomplete_outcome() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="read_file", arguments={})],
            )
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="read",
                status="completed",
                disposition="continue",
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
        max_steps=1,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-incomplete",
        source_message_id="msg-incomplete",
        user_text="read",
        raw_text="read",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
    )

    await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert record.status == "incomplete"
    assert record.outcome == "max_steps"
    assert record.steps == 1
    assert record.metadata["tool_trace_count"] == 1
    tool_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_COMPLETED)
    assert tool_event.metadata["tool_name"] == "read_file"
    assert tool_event.metadata["status"] == "completed"
    assert tool_event.metadata["tool_index"] == 1
    assert tool_event.metadata["policy_decision"]["decision"] == "allow"
    assert tool_event.metadata["policy_decision"]["tool_name"] == "read_file"


@pytest.mark.anyio
async def test_default_agent_harness_applies_run_budget_max_steps() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="read_file", arguments={})],
            )
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="read",
                status="completed",
                disposition="continue",
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
        max_steps=6,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-budget",
        source_message_id="msg-budget",
        user_text="read",
        raw_text="read",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(max_steps=1),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.status == "incomplete"
    assert result.exit_reason == "max_steps"
    assert record.outcome == "max_steps"
    assert record.steps == 1
    assert record.trace_events[1].metadata["budget_max_steps"] == 1
    assert loop.max_steps == 6


@pytest.mark.anyio
async def test_default_agent_harness_denies_tool_calls_over_budget() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="read_file", arguments={})],
            ),
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="read_file", arguments={})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="read",
                status="completed",
                disposition="continue",
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
        max_steps=3,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-tool-budget",
        source_message_id="msg-tool-budget",
        user_text="read twice",
        raw_text="read twice",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(max_tool_calls=1),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.status == "completed"
    assert result.exit_reason == "assistant_text"
    assert len(result.tool_trace) == 2
    assert result.tool_trace[0].status == "completed"
    assert result.tool_trace[1].status == "failed"
    assert result.tool_trace[1].action == "policy_denied"
    assert result.tool_trace[1].metadata["policy_reason"] == "max_tool_calls_exceeded"
    assert len(executor.results) == 0
    assert HarnessTraceEventType.TOOL_DENIED in [event.event_type for event in record.trace_events]
    denied_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_DENIED)
    assert denied_event.metadata["attempted_tool_name"] == "read_file"
    assert denied_event.metadata["max_tool_calls"] == 1


@pytest.mark.anyio
async def test_default_agent_harness_filters_denied_tools_from_run_policy() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(name="read_file", description="file reader", input_schema={}),
            ToolSpec(name="shell_exec", description="shell", input_schema={}),
        ],
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-denied-surface",
        source_message_id="msg-denied-surface",
        user_text="hello",
        raw_text="hello",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(denied_tool_names=["shell_exec"]),
    )

    await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    policy_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_POLICY_APPLIED)
    assert policy_event.metadata["original_tool_names"] == ["read_file", "shell_exec"]
    assert policy_event.metadata["filtered_tool_names"] == ["shell_exec"]
    assert policy_event.metadata["exposed_tool_names"] == ["read_file"]
    assert policy_event.metadata["run_denied_tool_names"] == ["shell_exec"]


@pytest.mark.anyio
async def test_default_agent_harness_denies_disallowed_tool_execution() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="shell_exec", arguments={})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="should not run",
                status="completed",
                disposition="continue",
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(name="read_file", description="file reader", input_schema={}),
            ToolSpec(name="shell_exec", description="shell", input_schema={}),
        ],
        max_steps=2,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-disallowed-exec",
        source_message_id="msg-disallowed-exec",
        user_text="run shell",
        raw_text="run shell",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(allowed_tool_names=["read_file"]),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.tool_trace[0].tool_name == "shell_exec"
    assert result.tool_trace[0].action == "policy_denied"
    assert result.tool_trace[0].metadata["policy_reason"] == "tool_not_allowed"
    assert len(executor.results) == 1
    denied_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_DENIED)
    assert denied_event.metadata["reason"] == "tool_not_allowed"
    assert denied_event.metadata["attempted_tool_name"] == "shell_exec"


@pytest.mark.anyio
async def test_default_agent_harness_applies_policy_profile() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="write_file", arguments={})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(
                success=True,
                content="should not run",
                status="completed",
                disposition="continue",
            )
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(name="read_file", description="file reader", input_schema={}),
            ToolSpec(name="write_file", description="file writer", input_schema={}),
            ToolSpec(name="web_search", description="web", input_schema={}),
            ToolSpec(name="skill_run", description="skill runner", input_schema={}),
        ],
        max_steps=2,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-policy-profile",
        source_message_id="msg-policy-profile",
        user_text="write",
        raw_text="write",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(policy_profile="background_readonly"),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    policy_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_POLICY_APPLIED)
    denied_event = next(event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_DENIED)
    assert policy_event.metadata["policy_profile"] == "background_readonly"
    assert policy_event.metadata["exposed_tool_names"] == ["read_file", "skill_run", "web_search"]
    assert policy_event.metadata["filtered_tool_names"] == ["write_file"]
    assert result.tool_trace[0].action == "policy_denied"
    assert result.tool_trace[0].metadata["policy_reason"] == "tool_not_allowed"
    assert denied_event.metadata["attempted_tool_name"] == "write_file"
    assert len(executor.results) == 1


def test_tool_policy_decision_serializes_deny_metadata() -> None:
    decision = deny_tool_policy_decision(
        tool_name="shell_exec",
        reason="tool_denied",
        policy_profile="wechat_safe",
        audit_metadata={"channel": "wechat"},
    )

    assert decision.decision == "deny"
    assert decision.tool_name == "shell_exec"
    assert decision.safe_user_message == "Tool `shell_exec` is not allowed by this run's harness policy."
    assert decision.to_dict()["audit_metadata"] == {"channel": "wechat"}


def test_tool_policy_decision_serializes_allow_metadata() -> None:
    decision = allow_tool_policy_decision(
        tool_name="read_file",
        policy_profile="wechat_safe",
        risk="low",
        audit_metadata={"channel": "wechat"},
    )

    assert decision.decision == "allow"
    assert decision.risk == "low"
    assert decision.requires_confirmation is False
    assert decision.to_dict()["policy_profile"] == "wechat_safe"
    assert decision.to_dict()["audit_metadata"] == {"channel": "wechat"}


def test_tool_policy_engine_denies_unlisted_tool() -> None:
    engine = ToolPolicyEngine()
    decision = engine.evaluate(
        ToolPolicyContext(
            tool_name="write_file",
            allowed_tool_names=frozenset({"read_file"}),
        )
    )

    assert decision.decision == "deny"
    assert decision.reason == "tool_not_allowed"


def test_tool_policy_engine_denies_exceeded_tool_budget() -> None:
    engine = ToolPolicyEngine()
    decision = engine.evaluate(
        ToolPolicyContext(
            tool_name="read_file",
            max_tool_calls=1,
            tool_calls_so_far=1,
        )
    )

    assert decision.decision == "deny"
    assert decision.reason == "max_tool_calls_exceeded"
    assert "Tool call budget exceeded" in decision.safe_user_message


def test_tool_policy_engine_denies_disallowed_role() -> None:
    engine = ToolPolicyEngine()
    decision = engine.evaluate(
        ToolPolicyContext(
            tool_name="shell_exec",
            agent_role="worker",
            allowed_roles=frozenset({"primary"}),
        )
    )

    assert decision.decision == "deny"
    assert decision.reason == "role_not_allowed"


def test_tool_policy_engine_allows_when_unrestricted() -> None:
    engine = ToolPolicyEngine()
    decision = engine.evaluate(ToolPolicyContext(tool_name="read_file"))

    assert decision.decision == "allow"
    assert decision.reason == "tool_allowed"


def test_effective_allowed_tool_names_intersects_profile_and_budget() -> None:
    budget = RunBudget(
        policy_profile="background_readonly",
        allowed_tool_names=["read_file", "write_file"],
    )

    assert effective_allowed_tool_names(budget) == frozenset({"read_file"})


def test_has_runtime_tool_governance_detects_budget_only() -> None:
    assert has_runtime_tool_governance(RunBudget(max_tool_calls=2)) is True
    assert has_runtime_tool_governance(RunBudget()) is False


def test_resolve_platform_name_normalizes_wechat_channel() -> None:
    assert resolve_platform_name("weixin") == "wechat"
    assert resolve_platform_name("cli") == "cli"


def test_tool_policy_engine_asks_for_confirmation() -> None:
    context = ToolPolicyContext(
        tool_name="scheduled_tasks",
        requires_confirmation=True,
        tool_risk="high",
        platform="api",
    )

    assert requires_hitl_confirmation(context) is True
    decision = ToolPolicyEngine().evaluate(context)
    assert decision.decision == "ask"
    assert decision.reason == "confirmation_required"
    assert "confirmation" in decision.safe_user_message


def test_tool_policy_engine_skips_confirmation_on_cli() -> None:
    context = ToolPolicyContext(
        tool_name="scheduled_tasks",
        requires_confirmation=True,
        tool_risk="high",
        platform="cli",
    )

    assert requires_hitl_confirmation(context) is False
    decision = ToolPolicyEngine().evaluate(context)
    assert decision.decision == "allow"


def test_tool_policy_engine_routes_shell_exec_to_sandbox_on_cli() -> None:
    context = ToolPolicyContext(
        tool_name="shell_exec",
        requires_sandbox=True,
        platform="cli",
    )

    assert requires_sandbox_execution(context) is True
    decision = ToolPolicyEngine().evaluate(context)
    assert decision.decision == "sandbox"
    assert decision.reason == "sandbox_required"


def test_tool_policy_engine_routes_wechat_write_file_to_sandbox() -> None:
    context = ToolPolicyContext(
        tool_name="write_file",
        platform="wechat",
    )

    assert requires_sandbox_execution(context) is True
    decision = ToolPolicyEngine().evaluate(context)
    assert decision.decision == "sandbox"


@pytest.mark.anyio
async def test_default_agent_harness_applies_sandbox_before_tool_execution(tmp_path) -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="shell_exec", arguments={"command": "pwd"})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = SandboxCapturingExecutor()
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(
                name="shell_exec",
                description="shell",
                input_schema={},
                risk="high",
                requires_confirmation=True,
                requires_sandbox=True,
            ),
        ],
        max_steps=2,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    cora_home = tmp_path / ".cora"
    run_input = new_run_input(
        session_id="session-sandbox",
        source_message_id="msg-sandbox",
        user_text="/tool shell_exec {\"command\":\"pwd\"}",
        raw_text="/tool shell_exec {\"command\":\"pwd\"}",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(policy_profile="coding_full"),
        metadata={"platform": "cli", "cora_home_dir": str(cora_home)},
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    sandbox_event = next(
        event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_SANDBOX_APPLIED
    )
    assert sandbox_event.metadata["attempted_tool_name"] == "shell_exec"
    assert sandbox_event.metadata["sandbox"]["run_id"] == run_input.run_id
    assert executor.captured_metadata
    assert str(executor.captured_metadata[0]["sandbox_workspace_root"]).endswith("workspace")
    assert result.status == "completed"
    assert not (cora_home / "sandboxes" / run_input.run_id).exists()


def test_sql_hitl_store_persists_and_resolves(tmp_path) -> None:
    from core.storage.db import DatabaseManager
    from core.storage.repositories import SqlHitlStore

    database = DatabaseManager(f"sqlite:///{tmp_path / 'hitl.db'}")
    database.create_all()
    store = SqlHitlStore(database)
    request = store.create_pending(
        run_id="run-1",
        session_id="session-1",
        tool_name="scheduled_tasks",
        reason="confirmation_required",
        tool_arguments={"action": "list"},
    )
    assert request.status == "pending"
    loaded = store.get(hitl_id=request.hitl_id)
    assert loaded is not None
    assert loaded.tool_arguments == {"action": "list"}
    approved = store.approve(hitl_id=request.hitl_id)
    assert approved.status == "approved"
    assert approved.resolved_at is not None
    with pytest.raises(ValueError):
        store.approve(hitl_id=request.hitl_id)


@pytest.mark.anyio
async def test_default_agent_harness_executes_after_hitl_approval() -> None:
    repository = InMemoryAgentRunRecordRepository()
    hitl_store = InMemoryHitlStore()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="scheduled_tasks", arguments={"action": "list"})],
            ),
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="scheduled_tasks", arguments={"action": "list"})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = StubExecutor(
        results=[
            ToolResult(success=True, content="tasks listed", action="automation"),
        ]
    )
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(
                name="scheduled_tasks",
                description="scheduled tasks",
                input_schema={},
                risk="high",
                requires_confirmation=True,
            ),
        ],
        max_steps=2,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(
        runner=runner,
        run_record_repository=repository,
        hitl_store=hitl_store,
    )
    ask_run = new_run_input(
        session_id="session-hitl-resume",
        source_message_id="msg-hitl-resume-1",
        user_text="/tool scheduled_tasks {\"action\":\"list\"}",
        raw_text="/tool scheduled_tasks {\"action\":\"list\"}",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(policy_profile="coding_full"),
        metadata={"platform": "api"},
    )
    ask_result = await harness.run(run_input=ask_run)
    assert ask_result.tool_trace[0].action == "policy_ask"
    pending = hitl_store.get(hitl_id=ask_result.tool_trace[0].metadata["hitl_id"])
    assert pending is not None
    hitl_store.approve(hitl_id=pending.hitl_id)
    resume_run = new_run_input(
        session_id="session-hitl-resume",
        source_message_id="msg-hitl-resume-2",
        user_text="/tool scheduled_tasks {\"action\":\"list\"}",
        raw_text="/tool scheduled_tasks {\"action\":\"list\"}",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(
            policy_profile="coding_full",
            approved_tool_names=["scheduled_tasks"],
        ),
        metadata={"platform": "api", "resume_hitl_id": pending.hitl_id, "parent_run_id": ask_run.run_id},
        parent_run_id=ask_run.run_id,
    )
    resume_result = await harness.run(run_input=resume_run)
    assert resume_result.tool_trace
    assert resume_result.tool_trace[0].action != "policy_ask"
    assert len(executor.results) == 0
    resume_record = repository.get(run_id=resume_run.run_id)
    assert "tool.hitl.approved" in [event.event_type for event in resume_record.trace_events]


@pytest.mark.anyio
async def test_default_agent_harness_asks_for_confirmation_before_execution() -> None:
    repository = InMemoryAgentRunRecordRepository()
    hitl_store = InMemoryHitlStore()
    model = StubModelClient(
        responses=[
            ModelResponse(
                assistant_text="",
                tool_calls=[ToolCall(tool_name="scheduled_tasks", arguments={"action": "list"})],
            ),
            ModelResponse(assistant_text="done"),
        ]
    )
    executor = StubExecutor(results=[])
    loop = AgentLoop(
        model_client=model,
        tool_executor=executor,
        tool_specs=[
            ToolSpec(
                name="scheduled_tasks",
                description="scheduled tasks",
                input_schema={},
                risk="high",
                requires_confirmation=True,
            ),
        ],
        max_steps=2,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(
        runner=runner,
        run_record_repository=repository,
        hitl_store=hitl_store,
    )
    run_input = new_run_input(
        session_id="session-hitl-ask",
        source_message_id="msg-hitl-ask",
        user_text="/tool scheduled_tasks {\"action\":\"list\"}",
        raw_text="/tool scheduled_tasks {\"action\":\"list\"}",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(policy_profile="coding_full"),
        metadata={"platform": "api"},
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.disposition == "clarify"
    assert result.exit_reason == "needs_clarification"
    assert result.tool_trace[0].action == "policy_ask"
    assert result.tool_trace[0].metadata["hitl_id"]
    assert len(executor.results) == 0
    requested_event = next(
        event for event in record.trace_events if event.event_type == HarnessTraceEventType.TOOL_REQUESTED
    )
    assert requested_event.metadata["attempted_tool_name"] == "scheduled_tasks"
    assert record.metadata["failure_category"] == "needs_confirmation"


@pytest.mark.anyio
async def test_default_agent_harness_applies_timeout_budget() -> None:
    repository = InMemoryAgentRunRecordRepository()
    loop = AgentLoop(
        model_client=StubModelClient(
            responses=[
                ModelResponse(
                    assistant_text="",
                    tool_calls=[ToolCall(tool_name="read_file", arguments={})],
                )
            ]
        ),
        tool_executor=SlowExecutor(),
        tool_specs=[ToolSpec(name="read_file", description="file reader", input_schema={})],
        max_steps=4,
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-timeout",
        source_message_id="msg-timeout",
        user_text="hello",
        raw_text="hello",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
        budget=RunBudget(timeout_seconds=0.001),
    )

    result = await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert result.status == "incomplete"
    assert result.exit_reason == "timeout"
    assert record.status == "incomplete"
    assert record.outcome == "timeout"
    assert record.completed_at is not None
    assert [event.event_type for event in record.trace_events][-1] == HarnessTraceEventType.BUDGET_TIMEOUT
    assert record.trace_events[-1].severity == "warning"
    assert loop.max_steps == 4


@pytest.mark.anyio
async def test_default_agent_harness_records_failed_run_and_reraises() -> None:
    repository = InMemoryAgentRunRecordRepository()
    model = StubModelClient(responses=[ModelResponse(assistant_text="Ready.", tool_calls=[])])
    loop = AgentLoop(
        model_client=model,
        tool_executor=StubExecutor(results=[]),
        tool_specs=[],
    )
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(loop=loop),
        loop=loop,
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("history unavailable")),
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: loop.tool_specs,
        execution_policy_resolver=ExecutionPolicyResolver(),
    )
    harness = DefaultAgentHarness(runner=runner, run_record_repository=repository)
    run_input = new_run_input(
        session_id="session-failed",
        source_message_id="msg-failed",
        user_text="hello",
        raw_text="hello",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
    )

    with pytest.raises(RuntimeError, match="history unavailable"):
        await harness.run(run_input=run_input)

    record = repository.get(run_id=run_input.run_id)
    assert record.status == "failed"
    assert record.outcome == "error"
    assert record.completed_at is not None
    assert "RuntimeError: history unavailable" == record.error
    assert [event.event_type for event in record.trace_events] == [
        HarnessTraceEventType.RUN_STARTED,
        HarnessTraceEventType.RUN_FAILED,
    ]
    assert record.trace_events[-1].severity == "error"


def test_sql_agent_run_record_repository_roundtrip(tmp_path: Path) -> None:
    database = DatabaseManager(f"sqlite:///{tmp_path / 'runs.db'}")
    database.create_all()
    session = SessionRepository(database).create()
    repository = SqlAgentRunRecordRepository(database)
    run_input = new_run_input(
        session_id=session.id,
        source_message_id="msg-sql",
        user_text="hello",
        raw_text="hello",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
    )

    created = repository.create_started(
        run_input=run_input,
        harness_id="default-single-agent",
        input_metadata={"channel": "wechat"},
    )
    completed = repository.mark_completed(
        run_id=created.run_id,
        status="completed",
        outcome="assistant_text",
        steps=1,
        trace_events=[
            RunTraceEvent(
                event_type=HarnessTraceEventType.CLEANUP_COMPLETED,
                run_id=created.run_id,
                session_id=session.id,
                sequence=1,
                metadata={"harness_id": "default-single-agent"},
            )
        ],
        metadata={"tool_trace_count": 0},
    )

    fetched = repository.get(run_id=created.run_id)
    assert completed.run_id == created.run_id
    assert fetched.status == "completed"
    assert fetched.outcome == "assistant_text"
    assert fetched.input_metadata["channel"] == "wechat"
    assert fetched.trace_events[0].event_type == HarnessTraceEventType.CLEANUP_COMPLETED
    assert fetched.trace_events[0].sequence == 1
    assert repository.list_by_session(session_id=session.id)[0].run_id == created.run_id


@pytest.mark.anyio
async def test_agent_turn_runner_delegates_to_harness() -> None:
    runtime = ConversationRuntimeState(session_id="session-spy")
    loop_result = LoopResult(
        final_response="Harness reply.",
        trace=[Message.assistant(session_id="session-spy", content="Harness reply.")],
        runtime=runtime,
        exit_reason="assistant_text",
        steps=1,
    )
    spy_harness = SpyHarness(result=loop_result, inputs=[])
    runner = AgentTurnRunner(
        orchestrator=AgentOrchestrator(
            loop=AgentLoop(
                model_client=StubModelClient(responses=[]),
                tool_executor=StubExecutor(results=[]),
                tool_specs=[],
            )
        ),
        loop=AgentLoop(
            model_client=StubModelClient(responses=[]),
            tool_executor=StubExecutor(results=[]),
            tool_specs=[],
        ),
        runtime_manager=AgentRuntimeManager(pending_state_repository=StubPendingStateRepository()),
        skill_loader=SkillLoader(),
        history_loader=lambda **kwargs: [],
        delivery_available=lambda: False,
        media_kind_resolver=lambda upload: None,
        tool_specs_resolver=lambda runtime: [],
        execution_policy_resolver=ExecutionPolicyResolver(),
        harness=spy_harness,
    )

    result = await runner.run_turn(
        session_id="session-spy",
        source_message_id="msg-spy",
        user_text="hello",
        raw_text="hello raw",
        upload=None,
        context_snapshot=RuntimeContextSnapshot(),
    )

    assert result.reply == "Harness reply."
    assert len(spy_harness.inputs) == 1
    assert spy_harness.inputs[0].session_id == "session-spy"
    assert spy_harness.inputs[0].source_message_id == "msg-spy"
    assert spy_harness.inputs[0].user_text == "hello"
    assert spy_harness.inputs[0].raw_text == "hello raw"


def test_session_runtime_snapshot_loader_builds_context_from_history_and_events() -> None:
    source_events = StubSourceEventRepository(
        events=[
            StubSourceEventRecord(
                id="evt-1",
                event_type="image",
                channel="wechat",
                raw_text="",
                original_file_name="photo.jpg",
                mime_type="image/jpeg",
                created_at=type("T", (), {"isoformat": lambda self: "2025-01-01T00:00:00"})(),
            )
        ]
    )
    loader = SessionRuntimeSnapshotLoader(
        message_repository=StubMessageRepository(
            context={
                "current_source_event_id": "evt-1",
                "last_action": "retrieve",
            }
        ),
        source_event_repository=source_events,
        session_repository=StubSessionRepository(
            session=StubSessionRecord(
                session_kind="job_execution",
                metadata_json={"scheduled_task_id": "task-1"},
            )
        ),
    )

    snapshot = loader.load_context_snapshot(session_id="session-1")

    assert snapshot.session_kind == "job_execution"
    assert snapshot.session_metadata["scheduled_task_id"] == "task-1"
    assert snapshot.current_source_event_id == "evt-1"
    assert snapshot.last_action == "retrieve"
    assert len(snapshot.recent_events) == 1
    assert snapshot.recent_events[0].metadata["mime_type"] == "image/jpeg"


def test_source_event_manager_classifies_links_and_images() -> None:
    repository = StubSourceEventRepository(events=[])
    manager = SourceEventManager(source_event_repository=repository)

    image_event = manager.create_source_event(
        session_id="session-1",
        source_message_id="msg-1",
        text=None,
        upload=UploadFile(filename="wechat_image.jpg", file=BytesIO(b"img")),
    )
    link_event = manager.create_source_event(
        session_id="session-1",
        source_message_id="msg-2",
        text="https://example.com/file.pdf",
        upload=None,
    )

    assert image_event.id == "evt-created"
    assert repository.created_payloads is not None
    assert repository.created_payloads[0]["event_type"] == "image"
    assert repository.created_payloads[1]["event_type"] == "link"


@pytest.mark.anyio
async def test_runtime_tool_executor_executes_native_tool_calls_and_updates_runtime() -> None:
    database = DatabaseManager("sqlite:///:memory:")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    user_signal_repository = UserSignalRepository(database)
    pending_state_repository = PendingStateRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=Path.cwd() / ".tmp-test-files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
    )
    calls: list[dict[str, Any]] = []

    async def _fake_execute(**kwargs) -> ToolExecutionResult:
        calls.append(kwargs)
        return ToolExecutionResult(
            reply="saved",
            action="capture",
            item_id="item-1",
            state_delta=RuntimeStateDelta(
                last_action="capture",
                current_source_event_id="event-2",
            ),
        )

    executor.execute = _fake_execute  # type: ignore[method-assign]
    runtime = ConversationRuntimeState(
        session_id="session-bridge",
        current_source_event_id="event-1",
        last_action="retrieve",
        metadata={
            "source_message_id": "msg-1",
            "raw_text": "save this",
            "upload": None,
        },
    )

    result = await executor.execute_tool_call(
        session_id="session-bridge",
        tool_call=ToolCall(tool_name="archive", arguments={"action": "save"}),
        runtime=runtime,
    )

    assert calls[0]["plan"].tool == "archive"
    assert calls[0]["context"]["current_source_event_id"] == "event-1"
    assert calls[0]["context"]["execution_mode"] == "conversation"
    assert result.metadata is not None
    next_runtime = result.metadata["runtime_state"]
    assert next_runtime.last_action == "capture"
    assert next_runtime.current_source_event_id == "event-2"


@pytest.mark.anyio
async def test_runtime_tool_executor_blocks_scheduled_tasks_inside_job_execution_sessions() -> None:
    database = DatabaseManager("sqlite:///:memory:")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=Path.cwd() / ".tmp-test-files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
    )

    result = await executor.execute(
        session_id="job-session-1",
        source_message_id="msg-1",
        plan=ToolPlan(tool="scheduled_tasks", arguments={"action": "list"}, reason="test", source="test"),
        text="List my reminders.",
        upload=None,
        context={"session_kind": "job_execution"},
    )

    assert result.status == "failed"
    assert result.hints.blocked_tool_name == "scheduled_tasks"
    assert result.hints.policy_tag == "restricted_tools"
    assert result.metadata["job_execution_blocked_tool"] == "scheduled_tasks"
    assert "background scheduled execution" in result.reply


@pytest.mark.anyio
async def test_runtime_tool_executor_suppresses_pending_clarification_for_job_execution_skill_runs() -> None:
    database = DatabaseManager("sqlite:///:memory:")
    database.create_all()
    item_repository = ItemRepository(database)
    message_repository = MessageRepository(database)
    pending_state_repository = PendingStateRepository(database)
    user_signal_repository = UserSignalRepository(database)
    ingestion_service = IngestionService(
        item_repository=item_repository,
        message_repository=message_repository,
        user_signal_repository=user_signal_repository,
        storage_dir=Path.cwd() / ".tmp-test-files",
    )
    executor = RuntimeToolExecutor(
        ingestion_service=ingestion_service,
        item_repository=item_repository,
        pending_state_repository=pending_state_repository,
    )
    executor.skill_script_executor.run = lambda request: SkillExecutionResult(
        message="Which workspace should I inspect?",
        action="skill",
        pending_state_delta=PendingStateDelta(
            request=PendingRequest(
                kind="choice",
                question="Which workspace should I inspect?",
                choices=["prod", "staging"],
            )
        ),
    )

    result = await executor._tool_skill_run(
        ToolInvocation(
            session_id="job-session-1",
            source_message_id="msg-1",
            plan=ToolPlan(
                tool="skill_run",
                arguments={
                    "name": "archive-core",
                    "script_path": "scripts/archive_dispatch.py",
                    "input": {"query": "check the latest item"},
                },
                reason="test",
                source="test",
            ),
            text="check the latest item",
            upload=None,
            context={
                "session_kind": "job_execution",
                "current_source_event_id": "evt-1",
            },
        )
    )

    assert result.status == "failed"
    assert result.disposition == "clarify"
    assert result.needs_clarification is True
    assert result.reply == "[SILENT]"
    assert result.hints.override_reply == "[SILENT]"
    assert result.hints.policy_tag == "no_clarify"
    assert result.hints.suppressed_pending is not None
    assert result.hints.suppressed_pending.choices == ["prod", "staging"]
    assert result.metadata["background_policy"] == "no_clarify"
    assert result.metadata["suppressed_pending"]["choices"] == ["prod", "staging"]
    assert pending_state_repository.get_latest_pending(session_id="job-session-1") is None


def test_loop_result_to_turn_result_converts_background_clarify_into_respond() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.runtime_manager = AgentRuntimeManager(pending_state_repository=StubPendingStateRepository())

    result = LoopResult(
        final_response="[SILENT]",
        trace=[],
        runtime=ConversationRuntimeState(session_id="job-session-1", session_kind="job_execution"),
        exit_reason="needs_clarification",
        steps=1,
        status="failed",
        disposition="clarify",
        tool_trace=[
            ToolExecutionTrace(
                tool_name="skill_run",
                arguments={},
                action="skill",
                status="failed",
                disposition="clarify",
                content="[SILENT]",
                hints=ExecutionHints(override_reply="[SILENT]"),
            )
        ],
    )

    turn_result = AgentTurnRunner.loop_result_to_turn_result(runner, result)

    assert turn_result.status == "failed"
    assert turn_result.disposition == "respond"
    assert turn_result.reply == "[SILENT]"
    assert "without leaving a pending question" in turn_result.reason


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
