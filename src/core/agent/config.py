from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    agent_name: str = "core"
    database_path: Path = Field(default=Path(".cora/core.db"))
    clawbot_database_path: Path = Field(default=Path(".cora/clawbot.db"))
    files_storage_dir: Path = Field(default=Path(".cora/files"))
    model_provider: str = "dev"
    model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    max_history_messages: int = 12
    max_tool_rounds: int = 3
    max_total_tool_calls: int = 5
    summary_trigger_messages: int = 20
    debug: bool = False

    model_config = SettingsConfigDict(
        env_prefix="CORA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def clawbot_database_url(self) -> str:
        return f"sqlite:///{self.clawbot_database_path.as_posix()}"
