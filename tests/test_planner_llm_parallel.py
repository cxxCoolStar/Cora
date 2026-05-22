from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from core.agent.plan_planner import build_planner_user_text
from core.clawbot import dependencies as deps
from core.clawbot.dependencies import get_clawbot_container
from core.llm.base import ModelClient
from core.llm.dev_client import DevelopmentModelClient
from core.llm.planner_aware_client import PlannerAwareModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolSpec


class SimulatedLlmPlannerModelClient(ModelClient):
    """Acts like a real LLM on planner turns: JSON plan with parallel_subagents."""

    def __init__(self) -> None:
        self._worker_client = DevelopmentModelClient()
        self.last_response_format: str | None = None

    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        response_format: str | None = None,
    ) -> ModelResponse:
        self.last_response_format = response_format
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        if latest_user is not None and "[Planner mode]" in latest_user.content:
            session_id = "session-unknown"
            match = re.search(r'"session_id":\s*"([^"]+)"', latest_user.content)
            if match is not None:
                session_id = match.group(1)
            plan = {
                "plan_id": "plan-simulated-llm-parallel",
                "session_id": session_id,
                "goal": "Parallel workspace search before edits",
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-search",
                        "title": "Parallel workspace discovery",
                        "tool_names": [],
                        "instruction": "Discover hello_agent references across the repo.",
                        "parallel_subagents": [
                            {
                                "instruction": "Find hello_agent under src/.",
                                "tool_names": ["search_files"],
                            },
                            {
                                "instruction": "Search for hello_agent under tests/.",
                                "tool_names": ["search_files"],
                            },
                        ],
                    }
                ],
            }
            return ModelResponse(assistant_text=json.dumps(plan, ensure_ascii=False))
        return self._worker_client.generate(
            messages=messages,
            tools=tools,
            response_format=response_format,
        )


@pytest.fixture
def clawbot_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "example.py").write_text("def hello_agent():\n    return 'ok'\n", encoding="utf-8")
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "dev")
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(tmp_path / "clawbot.db"))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CORA_USER_MEMORY_PATH", str(tmp_path / "user-memory" / "USER.md"))
    monkeypatch.setenv("CORA_FILE_TOOL_ROOT", str(workspace))
    deps._container = None
    yield tmp_path
    deps._container = None


def test_simulated_llm_planner_produces_parallel_subagents(clawbot_env: Path) -> None:
    container = get_clawbot_container()
    container.initialize()
    simulated = SimulatedLlmPlannerModelClient()
    container.clawbot_service.model_client = PlannerAwareModelClient(simulated)

    session = container.clawbot_service.create_session()
    response = asyncio.run(
        container.clawbot_service.plan_turn(
            session_id=session.id,
            text="Search hello_agent in parallel across src and tests before editing.",
        )
    )

    assert response.status == "completed"
    assert response.disposition == "respond"
    assert "parallel_subagents" in response.reply
    assert simulated.last_response_format == "json_object"

    stored = container.clawbot_service.plan_store.get_latest(session_id=session.id)
    assert stored is not None
    assert stored.plan.tasks[0].uses_parallel_subagents()
    assert len(stored.plan.tasks[0].parallel_subagents) == 2


def test_build_planner_user_text_lists_parallel_shape() -> None:
    text = build_planner_user_text(
        user_text="Search in parallel across src and tests",
        session_id="session-live-1",
    )
    assert "parallel_subagents" in text
    assert "session-live-1" in text


@pytest.mark.live
def test_live_openai_planner_parallel_subagents(clawbot_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api_key = str(os.environ.get("CORA_OPENAI_API_KEY") or "").strip()
    model = str(os.environ.get("CORA_MODEL") or "").strip()
    if not api_key or not model:
        pytest.skip("CORA_OPENAI_API_KEY and CORA_MODEL required for live planner test")

    monkeypatch.setenv("CORA_MODEL_PROVIDER", "openai")
    deps._container = None
    container = get_clawbot_container()
    container.initialize()

    session = container.clawbot_service.create_session()
    response = asyncio.run(
        container.clawbot_service.plan_turn(
            session_id=session.id,
            text=(
                "Before any edits, search the workspace for hello_agent in parallel across "
                "src and tests using separate focused parallel_subagents searches."
            ),
        )
    )

    assert response.status == "completed"
    assert response.disposition == "respond"
    stored = container.clawbot_service.plan_store.get_latest(session_id=session.id)
    assert stored is not None
    assert any(task.uses_parallel_subagents() for task in stored.plan.tasks), stored.plan.to_dict()
