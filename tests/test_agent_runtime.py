from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile

from core.agent.context_budget import ContextBudgetManager
from core.agent.context_manager import SessionContextManager
from core.agent.loop import AgentLoop
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, EventSnapshot, RuntimeContextSnapshot, RuntimeStateDelta
from core.agent.session_runtime import SessionRuntimeSnapshotLoader
from core.agent.skill_loader import SkillLoader
from core.clawbot import RuntimeToolExecutor
from core.clawbot.source_events import SourceEventManager
from core.clawbot.tools import ToolExecutionResult
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolResult, ToolSpec
from core.storage.db import DatabaseManager
from core.storage.repositories import ItemRepository, MessageRepository, PendingStateRepository, UserSignalRepository


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

    async def execute_tool_call(self, *, session_id: str, tool_call: ToolCall, runtime: ConversationRuntimeState) -> ToolResult:
        if not self.results:
            raise AssertionError("No stub tool results left")
        return self.results.pop(0)


class StubPendingStateRepository:
    def get_latest_pending(self, *, session_id: str):
        return None


@dataclass
class StubMessageRepository:
    context: dict

    def get_latest_assistant_context(self, *, session_id: str) -> dict:
        return dict(self.context)


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
    )

    snapshot = loader.load_context_snapshot(session_id="session-1")

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
    assert result.metadata is not None
    next_runtime = result.metadata["runtime_state"]
    assert next_runtime.last_action == "capture"
    assert next_runtime.current_source_event_id == "event-2"


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
