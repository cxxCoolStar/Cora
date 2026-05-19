from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_AGENT_PROMPT_MODES = {"", "agent", "prompt", "agent_prompt"}
_SKILL_MODES = {"skill", "skill_run", "skill_job"}
_SCRIPT_MODES = {"script", "script_job", "no_agent"}


@dataclass(slots=True)
class ScheduledTaskExecution:
    mode: str = "agent_prompt"
    skill_name: str | None = None
    script_path: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    attached_skills: list[str] = field(default_factory=list)

    @classmethod
    def from_metadata(
        cls,
        *,
        prompt_text: str,
        metadata: dict[str, Any] | None,
    ) -> "ScheduledTaskExecution":
        payload = dict(metadata or {})
        raw_execution = payload.get("execution")
        execution = dict(raw_execution) if isinstance(raw_execution, dict) else {}

        mode = cls._normalize_mode(execution.get("mode"))
        skill_name = cls._normalize_optional_text(execution.get("skill_name"))
        script_path = cls._normalize_optional_text(execution.get("script_path"))
        input_payload = cls._normalize_object(execution.get("input"), field_name="execution.input")
        attached_skills = cls._normalize_string_list(execution.get("skills"))

        if mode == "agent_prompt":
            if not str(prompt_text or "").strip():
                raise ValueError("Prompt-based scheduled tasks require prompt text.")
            return cls(
                mode=mode,
                skill_name=None,
                script_path=None,
                input_payload=input_payload,
                attached_skills=attached_skills,
            )

        if mode == "skill":
            if not skill_name:
                raise ValueError("Skill scheduled tasks require execution.skill_name.")
            if not script_path:
                raise ValueError("Skill scheduled tasks require execution.script_path.")
            return cls(
                mode=mode,
                skill_name=skill_name,
                script_path=script_path,
                input_payload=input_payload,
                attached_skills=attached_skills,
            )

        if not script_path:
            raise ValueError("Script scheduled tasks require execution.script_path.")
        return cls(
            mode="script",
            skill_name=None,
            script_path=script_path,
            input_payload=input_payload,
            attached_skills=attached_skills,
        )

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": self.mode}
        if self.skill_name:
            payload["skill_name"] = self.skill_name
        if self.script_path:
            payload["script_path"] = self.script_path
        if self.input_payload:
            payload["input"] = dict(self.input_payload)
        if self.attached_skills:
            payload["skills"] = list(self.attached_skills)
        return payload

    @staticmethod
    def summarize(metadata: dict[str, Any] | None) -> str:
        try:
            execution = ScheduledTaskExecution.from_metadata(prompt_text="__summary__", metadata=metadata)
        except ValueError:
            execution = ScheduledTaskExecution(mode="agent_prompt")
        if execution.mode == "skill":
            return f"skill:{execution.skill_name or '?'} / {execution.script_path or '?'}"
        if execution.mode == "script":
            return f"script:{execution.script_path or '?'}"
        if execution.attached_skills:
            return f"prompt + skills({', '.join(execution.attached_skills)})"
        return "prompt"

    @staticmethod
    def _normalize_mode(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in _AGENT_PROMPT_MODES:
            return "agent_prompt"
        if text in _SKILL_MODES:
            return "skill"
        if text in _SCRIPT_MODES:
            return "script"
        raise ValueError("Unsupported execution.mode. Use agent_prompt, skill, or script.")

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalize_object(value: Any, *, field_name: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be an object.")
        return dict(value)

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_values = [part.strip() for part in value.split(",")]
        else:
            raw_values = list(value)
        normalized: list[str] = []
        for raw in raw_values:
            text = str(raw or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized


def normalize_task_metadata(
    *,
    prompt_text: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = dict(metadata or {})
    execution = ScheduledTaskExecution.from_metadata(
        prompt_text=prompt_text,
        metadata=normalized,
    )
    normalized["execution"] = execution.to_metadata()
    return normalized

