from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, Field


class SuppressedPendingRequest(BaseModel):
    question: str = ""
    choices: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionHints(BaseModel):
    override_reply: str | None = None
    policy_tag: str | None = None
    blocked_tool_name: str | None = None
    suppressed_pending: SuppressedPendingRequest | None = None

    def is_empty(self) -> bool:
        return (
            self.override_reply is None
            and self.policy_tag is None
            and self.blocked_tool_name is None
            and self.suppressed_pending is None
        )

    def to_legacy_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if self.override_reply:
            metadata["background_execution_reply"] = self.override_reply
        if self.policy_tag:
            metadata["background_policy"] = self.policy_tag
        if self.blocked_tool_name:
            metadata["job_execution_blocked_tool"] = self.blocked_tool_name
        if self.suppressed_pending is not None:
            metadata["suppressed_pending"] = self.suppressed_pending.model_dump()
        return metadata

    @classmethod
    def from_legacy_metadata(cls, metadata: Mapping[str, Any] | None) -> "ExecutionHints":
        if metadata is None:
            return cls()
        override_reply = str(metadata.get("background_execution_reply") or "").strip() or None
        policy_tag = str(metadata.get("background_policy") or "").strip() or None
        blocked_tool_name = str(metadata.get("job_execution_blocked_tool") or "").strip() or None
        raw_suppressed_pending = metadata.get("suppressed_pending")
        suppressed_pending = None
        if isinstance(raw_suppressed_pending, Mapping):
            raw_choices = raw_suppressed_pending.get("choices")
            choices = (
                [str(choice) for choice in raw_choices if str(choice or "").strip()]
                if isinstance(raw_choices, list)
                else []
            )
            raw_payload = raw_suppressed_pending.get("payload")
            suppressed_pending = SuppressedPendingRequest(
                question=str(raw_suppressed_pending.get("question") or "").strip(),
                choices=choices,
                payload=dict(raw_payload) if isinstance(raw_payload, Mapping) else {},
            )
        return cls(
            override_reply=override_reply,
            policy_tag=policy_tag,
            blocked_tool_name=blocked_tool_name,
            suppressed_pending=suppressed_pending,
        )


__all__ = [
    "ExecutionHints",
    "SuppressedPendingRequest",
]
