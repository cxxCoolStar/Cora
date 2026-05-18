from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import StringIO

from typer.testing import CliRunner

from core.cli.main import app
from core.cli.tui import InteractiveChatShell
from core.clawbot.schemas import (
    DecisionDebugResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
    SessionDebugResponse,
)
from core.clawbot.service_runtime import AssistantTurnOutcome


runner = CliRunner()


@dataclass
class FakeSession:
    id: str


@dataclass
class FakeService:
    toolset_preset: str = "cora-cli"
    replies: list[AssistantTurnOutcome] = field(default_factory=list)
    created_count: int = 0
    reply_calls: list[tuple[str, str]] = field(default_factory=list)

    def create_session(self) -> FakeSession:
        self.created_count += 1
        return FakeSession(id=f"session-{self.created_count}")

    async def reply_outcome(self, *, session_id: str, text: str) -> AssistantTurnOutcome:
        self.reply_calls.append((session_id, text))
        if not self.replies:
            raise AssertionError("No fake replies left")
        return self.replies.pop(0)

    def list_tool_names(self) -> list[str]:
        return ["search_files", "read_file", "shell_exec"]

    def list_items(self, *, session_id: str):
        return [
            ItemSummaryResponse(
                id="item-1",
                item_type="document",
                title="notes.md",
                summary="saved notes",
                created_at=datetime.now(UTC),
            )
        ]

    def get_session_debug(self, *, session_id: str) -> SessionDebugResponse:
        return SessionDebugResponse(
            session_id=session_id,
            created_at=datetime.now(UTC),
            messages=[],
            items=[
                ItemDetailResponse(
                    id="item-1",
                    item_type="document",
                    title="notes.md",
                    summary="saved notes",
                    normalized_text="saved notes",
                    locator_hint=None,
                    created_at=datetime.now(UTC),
                )
            ],
            user_signals=[],
            user_profile=[],
            topics=[],
            recent_decisions=[
                DecisionDebugResponse(
                    action="execute",
                    confidence="high",
                    reason="Used shell_exec",
                    source="llm_tool_call",
                )
            ],
        )


def _fake_outcome() -> AssistantTurnOutcome:
    return AssistantTurnOutcome(
        reply="Done.",
        action="execute",
        disposition="respond",
        status="completed",
        tool_name="shell_exec",
        tool_arguments={"command": "python --version"},
        context={},
        confidence="high",
        reason="Used a terminal tool.",
        artifacts=[],
        trace=[],
        tool_trace=[
            {
                "tool_name": "shell_exec",
                "arguments": {"command": "python --version"},
                "action": "execute",
                "status": "completed",
                "disposition": "respond",
                "artifacts": [],
                "metadata": {"exit_code": 0},
            }
        ],
        item_id=None,
    )


def test_interactive_chat_shell_runs_single_turn_and_renders_trace() -> None:
    service = FakeService(replies=[_fake_outcome()])
    output = StringIO()
    prompts: list[str] = []
    inputs = iter(["hello", "/exit"])

    shell = InteractiveChatShell(
        clawbot_service=service,
        input_func=lambda prompt: (prompts.append(prompt), next(inputs))[1],
        output=output,
    )

    exit_code = shell.run()

    rendered = output.getvalue()
    assert exit_code == 0
    assert "Cora interactive shell" in rendered
    assert "Assistant: Done." in rendered
    assert "Tool trace:" in rendered
    assert "shell_exec [completed]" in rendered
    assert prompts[0].startswith("cora[session-")
    assert service.reply_calls == [("session-1", "hello")]


def test_interactive_chat_shell_handles_debug_and_items_commands() -> None:
    service = FakeService()
    output = StringIO()
    inputs = iter(["/items", "/debug", "/trace off", "/exit"])
    shell = InteractiveChatShell(
        clawbot_service=service,
        input_func=lambda prompt: next(inputs),
        output=output,
    )

    exit_code = shell.run()

    rendered = output.getvalue()
    assert exit_code == 0
    assert "Saved items:" in rendered
    assert "- item-1: notes.md [document]" in rendered
    assert "Debug: messages=0 items=1 topics=0 decisions=1" in rendered
    assert "Trace disabled." in rendered


def test_cli_no_args_launches_tui(monkeypatch) -> None:
    calls: list[tuple[str | None, bool]] = []

    def _fake_launch_tui(*, session_id: str | None = None, trace: bool = True) -> int:
        calls.append((session_id, trace))
        return 0

    monkeypatch.setattr("core.cli.main.launch_tui", _fake_launch_tui)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert calls == [(None, True)]


def test_cli_tui_command_launches_tui_with_options(monkeypatch) -> None:
    calls: list[tuple[str | None, bool]] = []

    def _fake_launch_tui(*, session_id: str | None = None, trace: bool = True) -> int:
        calls.append((session_id, trace))
        return 0

    monkeypatch.setattr("core.cli.main.launch_tui", _fake_launch_tui)

    result = runner.invoke(app, ["tui", "--session", "session-9", "--no-trace"])

    assert result.exit_code == 0
    assert calls == [("session-9", False)]
