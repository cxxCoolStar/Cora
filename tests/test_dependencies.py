from __future__ import annotations

from pathlib import Path

from core.clawbot import dependencies as deps
from core.clawbot.dependencies import get_clawbot_container
from core.llm.dev_client import DevelopmentModelClient


def test_get_clawbot_container_prefers_dev_provider_over_openai_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "dev")
    monkeypatch.setenv("CORA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CORA_MODEL", "gpt-test")
    monkeypatch.setenv("CORA_CLAWBOT_DATABASE_PATH", str(tmp_path / "clawbot.db"))
    monkeypatch.setenv("CORA_FILES_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("CORA_ARCHIVE_ROOT_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("CORA_USER_MEMORY_PATH", str(tmp_path / "user-memory" / "USER.md"))
    monkeypatch.setenv("CORA_FILE_TOOL_ROOT", str(tmp_path / "workspace"))

    deps._container = None
    try:
        container = get_clawbot_container()
    finally:
        deps._container = None

    assert isinstance(container.clawbot_service.model_client, DevelopmentModelClient)
