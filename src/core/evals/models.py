from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.schemas.harness import RunBudget


@dataclass(slots=True)
class EvalStateExpectation:
    item_count: int | None = None
    deleted_item_count: int | None = None
    pending_exists: bool | None = None
    pending_kind: str | None = None
    agent_run_count: int | None = None
    latest_agent_run_status: str | None = None
    latest_agent_run_outcome: str | None = None
    latest_agent_run_failure_category: str | None = None
    latest_agent_run_cleanup_status: str | None = None
    latest_agent_run_input_metadata_contains: dict[str, str] = field(default_factory=dict)
    latest_agent_run_error_contains_all: list[str] = field(default_factory=list)
    latest_agent_run_trace_contains_all: list[str] = field(default_factory=list)
    user_memory_contains_all: list[str] = field(default_factory=list)
    user_memory_contains_any: list[str] = field(default_factory=list)
    user_memory_not_contains: list[str] = field(default_factory=list)
    workspace_files_exist: list[str] = field(default_factory=list)
    workspace_files_not_exist: list[str] = field(default_factory=list)
    workspace_file_contains_all: dict[str, list[str]] = field(default_factory=dict)
    workspace_file_not_contains: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EvalStateExpectation":
        payload = payload or {}
        return cls(
            item_count=_maybe_int(payload.get("item_count")),
            deleted_item_count=_maybe_int(payload.get("deleted_item_count")),
            pending_exists=_maybe_bool(payload.get("pending_exists")),
            pending_kind=_maybe_str(payload.get("pending_kind")),
            agent_run_count=_maybe_int(payload.get("agent_run_count")),
            latest_agent_run_status=_maybe_str(payload.get("latest_agent_run_status")),
            latest_agent_run_outcome=_maybe_str(payload.get("latest_agent_run_outcome")),
            latest_agent_run_failure_category=_maybe_str(payload.get("latest_agent_run_failure_category")),
            latest_agent_run_cleanup_status=_maybe_str(payload.get("latest_agent_run_cleanup_status")),
            latest_agent_run_input_metadata_contains=_string_map(payload.get("latest_agent_run_input_metadata_contains")),
            latest_agent_run_error_contains_all=_string_list(payload.get("latest_agent_run_error_contains_all")),
            latest_agent_run_trace_contains_all=_string_list(payload.get("latest_agent_run_trace_contains_all")),
            user_memory_contains_all=_string_list(payload.get("user_memory_contains_all")),
            user_memory_contains_any=_string_list(payload.get("user_memory_contains_any")),
            user_memory_not_contains=_string_list(payload.get("user_memory_not_contains")),
            workspace_files_exist=_string_list(payload.get("workspace_files_exist")),
            workspace_files_not_exist=_string_list(payload.get("workspace_files_not_exist")),
            workspace_file_contains_all=_string_list_map(payload.get("workspace_file_contains_all")),
            workspace_file_not_contains=_string_list_map(payload.get("workspace_file_not_contains")),
        )


@dataclass(slots=True)
class EvalExpectation:
    status: str | None = None
    disposition: str | None = None
    action: str | None = None
    error_contains_all: list[str] = field(default_factory=list)
    tool_names_any: list[str] = field(default_factory=list)
    tool_names_all: list[str] = field(default_factory=list)
    reply_contains_all: list[str] = field(default_factory=list)
    reply_contains_any: list[str] = field(default_factory=list)
    reply_not_contains: list[str] = field(default_factory=list)
    artifact_ref_contains_any: list[str] = field(default_factory=list)
    max_trace_messages: int | None = None
    state: EvalStateExpectation = field(default_factory=EvalStateExpectation)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EvalExpectation":
        payload = payload or {}
        return cls(
            status=_maybe_str(payload.get("status")),
            disposition=_maybe_str(payload.get("disposition")),
            action=_maybe_str(payload.get("action")),
            error_contains_all=_string_list(payload.get("error_contains_all")),
            tool_names_any=_string_list(payload.get("tool_names_any")),
            tool_names_all=_string_list(payload.get("tool_names_all")),
            reply_contains_all=_string_list(payload.get("reply_contains_all")),
            reply_contains_any=_string_list(payload.get("reply_contains_any")),
            reply_not_contains=_string_list(payload.get("reply_not_contains")),
            artifact_ref_contains_any=_string_list(payload.get("artifact_ref_contains_any")),
            max_trace_messages=_maybe_int(payload.get("max_trace_messages")),
            state=EvalStateExpectation.from_dict(payload.get("state")),
        )


@dataclass(slots=True)
class EvalInput:
    text: str = ""
    channel: str | None = None
    platform: str | None = None
    hitl_action: str | None = None
    hitl_id: str | None = None
    agent_role: str | None = None
    external_user_id: str | None = None
    external_event_id: str | None = None
    run_budget: RunBudget = field(default_factory=RunBudget)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalInput":
        text = str(payload.get("text") or "").strip()
        hitl_action = _maybe_str(payload.get("hitl_action"))
        if not text and not hitl_action:
            raise ValueError("Eval step input.text or input.hitl_action is required.")
        return cls(
            text=text,
            channel=_maybe_str(payload.get("channel")),
            platform=_maybe_str(payload.get("platform")),
            hitl_action=hitl_action,
            hitl_id=_maybe_str(payload.get("hitl_id")),
            agent_role=_maybe_str(payload.get("agent_role")),
            external_user_id=_maybe_str(payload.get("external_user_id")),
            external_event_id=_maybe_str(payload.get("external_event_id")),
            run_budget=_run_budget(payload.get("run_budget") or payload.get("budget")),
        )


@dataclass(slots=True)
class EvalWebSearchHit:
    title: str
    url: str
    snippet: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalWebSearchHit":
        title = _maybe_str(payload.get("title"))
        url = _maybe_str(payload.get("url"))
        if not title or not url:
            raise ValueError("Eval web search hits require both `title` and `url`.")
        return cls(
            title=title,
            url=url,
            snippet=_maybe_str(payload.get("snippet")) or "",
        )


@dataclass(slots=True)
class EvalWebPage:
    content: str
    title: str | None = None
    final_url: str | None = None
    content_type: str = "text/html; charset=utf-8"
    provider: str = "direct"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalWebPage":
        content = str(payload.get("content") or "")
        if not content:
            raise ValueError("Eval web pages require non-empty `content`.")
        provider = (_maybe_str(payload.get("provider")) or "direct").strip().lower()
        if provider not in {"direct", "tavily_extract"}:
            raise ValueError("Eval web page `provider` must be `direct` or `tavily_extract`.")
        return cls(
            content=content,
            title=_maybe_str(payload.get("title")),
            final_url=_maybe_str(payload.get("final_url")),
            content_type=_maybe_str(payload.get("content_type")) or "text/html; charset=utf-8",
            provider=provider,
        )


@dataclass(slots=True)
class EvalStep:
    input: EvalInput
    expect: EvalExpectation
    label: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalStep":
        return cls(
            input=EvalInput.from_dict(dict(payload.get("input") or {})),
            expect=EvalExpectation.from_dict(dict(payload.get("expect") or {})),
            label=_maybe_str(payload.get("label")),
        )


@dataclass(slots=True)
class EvalSetup:
    user_memory_markdown: str | None = None
    workspace_files: dict[str, str] = field(default_factory=dict)
    harness_tool_delay_seconds: float | None = None
    harness_prepare_failure_message: str | None = None
    planner_stub_mode: str | None = None
    web_search_results: dict[str, list[EvalWebSearchHit]] = field(default_factory=dict)
    web_pages: dict[str, EvalWebPage] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EvalSetup":
        payload = payload or {}
        return cls(
            user_memory_markdown=_maybe_str(payload.get("user_memory_markdown")),
            workspace_files=_string_map(payload.get("workspace_files")),
            harness_tool_delay_seconds=_maybe_float(payload.get("harness_tool_delay_seconds")),
            harness_prepare_failure_message=_maybe_str(payload.get("harness_prepare_failure_message")),
            planner_stub_mode=_maybe_str(payload.get("planner_stub_mode")),
            web_search_results=_web_search_results_map(payload.get("web_search_results")),
            web_pages=_web_pages_map(payload.get("web_pages")),
        )


@dataclass(slots=True)
class EvalCase:
    id: str
    case_type: str
    description: str
    tags: list[str]
    setup: EvalSetup
    steps: list[EvalStep]
    source_path: Path

    @classmethod
    def from_path(cls, path: Path) -> "EvalCase":
        from core.evals.loader import load_case

        return load_case(path)


@dataclass(slots=True)
class EvalAssertionFailure:
    message: str


@dataclass(slots=True)
class EvalObservedState:
    item_count: int
    deleted_item_count: int
    pending_exists: bool
    pending_kind: str | None = None
    agent_run_count: int = 0
    latest_agent_run_status: str | None = None
    latest_agent_run_outcome: str | None = None
    latest_agent_run_failure_category: str | None = None
    latest_agent_run_cleanup_status: str | None = None
    latest_agent_run_input_metadata: dict[str, Any] = field(default_factory=dict)
    latest_agent_run_error: str | None = None
    latest_agent_run_trace_events: list[str] = field(default_factory=list)
    user_memory_text: str = ""
    workspace_root: str = ""


@dataclass(slots=True)
class EvalStepResult:
    index: int
    label: str
    ok: bool
    failures: list[EvalAssertionFailure]
    response: dict[str, Any]
    tool_names: list[str]
    failure_category: str | None = None
    observed_state: EvalObservedState | None = None


@dataclass(slots=True)
class EvalCaseResult:
    case_id: str
    description: str
    ok: bool
    tags: list[str]
    step_results: list[EvalStepResult]
    duration_seconds: float
    source_path: str
    failure_category: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class EvalRunResult:
    created_at: str
    project_root: str
    cases_dir: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    total_steps: int
    passed_steps: int
    failed_steps: int
    case_results: list[EvalCaseResult]
    report_path: str | None = None
    html_report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from core.evals.report import run_result_to_dict

        return run_result_to_dict(self)

    def to_text(self) -> str:
        from core.evals.report import run_result_to_text

        return run_result_to_text(self)


def string_list(value: Any) -> list[str]:
    return _string_list(value)


def maybe_str(value: Any) -> str | None:
    return _maybe_str(value)


def maybe_int(value: Any) -> int | None:
    return _maybe_int(value)


def _run_budget(value: Any) -> RunBudget:
    if not isinstance(value, dict):
        return RunBudget()
    return RunBudget(
        policy_profile=_maybe_str(value.get("policy_profile")),
        max_steps=_maybe_int(value.get("max_steps")),
        timeout_seconds=_maybe_float(value.get("timeout_seconds")),
        max_tool_calls=_maybe_int(value.get("max_tool_calls")),
        allowed_tool_names=_string_list(value.get("allowed_tool_names")),
        denied_tool_names=_string_list(value.get("denied_tool_names")),
        approved_tool_names=_string_list(value.get("approved_tool_names")),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        normalized[key] = str(raw_value)
    return normalized


def _string_list_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        tokens = _string_list(raw_value)
        if tokens:
            normalized[key] = tokens
    return normalized


def _web_search_results_map(value: Any) -> dict[str, list[EvalWebSearchHit]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[EvalWebSearchHit]] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_value, list):
            continue
        hits: list[EvalWebSearchHit] = []
        for item in raw_value:
            if isinstance(item, dict):
                hits.append(EvalWebSearchHit.from_dict(item))
        if hits:
            normalized[key] = hits
    return normalized


def _web_pages_map(value: Any) -> dict[str, EvalWebPage]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, EvalWebPage] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_value, dict):
            continue
        normalized[key] = EvalWebPage.from_dict(raw_value)
    return normalized


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _maybe_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"Cannot parse bool from value: {value!r}")
