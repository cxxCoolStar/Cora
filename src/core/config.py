from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    agent_name: str = "core"
    clawbot_database_path: Path = Field(default=Path(".cora/clawbot.db"))
    files_storage_dir: Path = Field(default=Path(".cora/files"))
    archive_root_dir: Path = Field(default=Path(".cora/archive"))
    model_provider: str = "dev"
    model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    auxiliary_vision_provider: str | None = None
    auxiliary_vision_model: str | None = None
    auxiliary_vision_api_key: str | None = None
    auxiliary_vision_base_url: str | None = None
    auxiliary_vision_timeout_seconds: int = 60
    debug: bool = False
    wechat_enabled: bool = False
    wechat_token: str | None = None
    wechat_account_id: str | None = None
    wechat_account_name: str = "default"
    wechat_base_url: str = "https://ilinkai.weixin.qq.com"
    wechat_poll_timeout_seconds: int = 35

    model_config = SettingsConfigDict(
        env_prefix="CORA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def clawbot_database_url(self) -> str:
        return f"sqlite:///{self.clawbot_database_path.as_posix()}"

    @property
    def cora_home_dir(self) -> Path:
        return self.clawbot_database_path.parent

    @property
    def wechat_accounts_dir(self) -> Path:
        return self.cora_home_dir / "wechat" / "accounts"
