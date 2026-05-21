from __future__ import annotations

from core.agent.execution_policy import JOB_EXECUTION_ALLOWED_TOOL_NAMES


def is_job_execution_allowed_tool_name(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in JOB_EXECUTION_ALLOWED_TOOL_NAMES


__all__ = [
    "JOB_EXECUTION_ALLOWED_TOOL_NAMES",
    "is_job_execution_allowed_tool_name",
]
