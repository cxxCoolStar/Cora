from __future__ import annotations

from pathlib import Path

from core.clawbot import dependencies as deps
from core.clawbot.dependencies import get_clawbot_container
from core.llm.dev_client import DevelopmentModelClient
from core.llm.openai_client import OpenAIChatModelClient


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


def test_get_clawbot_container_uses_configured_openai_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("CORA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CORA_MODEL", "gpt-test")
    monkeypatch.setenv("CORA_OPENAI_TIMEOUT_SECONDS", "123")
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

    assert isinstance(container.clawbot_service.model_client, OpenAIChatModelClient)
    assert container.clawbot_service.model_client.timeout == 123.0


def test_get_clawbot_container_debug_does_not_override_openai_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("CORA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CORA_MODEL", "gpt-test")
    monkeypatch.setenv("CORA_DEBUG", "true")
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

    assert isinstance(container.clawbot_service.model_client, OpenAIChatModelClient)


def test_get_clawbot_container_uses_configured_toolset_preset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "dev")
    monkeypatch.setenv("CORA_TOOLSET_PRESET", "cora-cli")
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

    assert container.clawbot_service.toolset_preset == "cora-cli"


def test_get_clawbot_container_uses_configured_harness_profiles(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORA_MODEL_PROVIDER", "dev")
    monkeypatch.setenv("CORA_HARNESS_POLICY_PROFILE", "coding_full")
    monkeypatch.setenv("CORA_WECHAT_HARNESS_POLICY_PROFILE", "wechat_safe")
    monkeypatch.setenv("CORA_JOB_HARNESS_POLICY_PROFILE", "background_readonly")
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

    assert container.clawbot_service.harness_policy_profile == "coding_full"
    assert container.clawbot_service.wechat_harness_policy_profile == "wechat_safe"
    assert container.clawbot_service.job_harness_policy_profile == "background_readonly"
