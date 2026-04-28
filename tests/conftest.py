from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.agent.agent import CoreAgent
from core.agent.config import CoreSettings
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall
from core.tools.builtin.echo import EchoTool
from core.tools.registry import ToolRegistry


class FakeModelClient(ModelClient):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls: list[list[Message]] = []

    def generate(self, *, messages: list[Message], tools):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeModelClient ran out of responses.")
        return self.responses.pop(0)


@pytest.fixture()
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "core.db"


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


@pytest.fixture()
def agent_factory(temp_db_path: Path, tool_registry: ToolRegistry):
    def _build(model_client: ModelClient) -> CoreAgent:
        settings = CoreSettings(database_path=temp_db_path, summary_trigger_messages=4)
        return CoreAgent(settings=settings, model_client=model_client, tool_registry=tool_registry)

    return _build


__all__ = ["FakeModelClient", "ToolCall"]
