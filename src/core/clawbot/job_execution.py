from __future__ import annotations

JOB_EXECUTION_ALLOWED_TOOL_NAMES = frozenset(
    {
        "list_files",
        "search_files",
        "read_file",
        "web_search",
        "web_fetch",
        "skills_list",
        "skill_view",
        "skill_run",
        "search_sessions",
    }
)


def is_job_execution_allowed_tool_name(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in JOB_EXECUTION_ALLOWED_TOOL_NAMES


__all__ = [
    "JOB_EXECUTION_ALLOWED_TOOL_NAMES",
    "is_job_execution_allowed_tool_name",
]
