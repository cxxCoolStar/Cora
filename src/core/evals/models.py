from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvalStateExpectation:
    item_count: int | None = None
    deleted_item_count: int | None = None
    pending_exists: bool | None = None
    pending_kind: str | None = None
    user_memory_contains_all: list[str] = field(default_factory=list)
    user_memory_contains_any: list[str] = field(default_factory=list)
    user_memory_not_contains: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EvalStateExpectation":
        payload = payload or {}
        return cls(
            item_count=_maybe_int(payload.get("item_count")),
            deleted_item_count=_maybe_int(payload.get("deleted_item_count")),
            pending_exists=_maybe_bool(payload.get("pending_exists")),
            pending_kind=_maybe_str(payload.get("pending_kind")),
            user_memory_contains_all=_string_list(payload.get("user_memory_contains_all")),
            user_memory_contains_any=_string_list(payload.get("user_memory_contains_any")),
            user_memory_not_contains=_string_list(payload.get("user_memory_not_contains")),
        )


@dataclass(slots=True)
class EvalExpectation:
    status: str | None = None
    disposition: str | None = None
    action: str | None = None
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
    text: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalInput":
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("Eval step input.text is required.")
        return cls(text=text)


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "EvalSetup":
        payload = payload or {}
        return cls(user_memory_markdown=_maybe_str(payload.get("user_memory_markdown")))


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
    user_memory_text: str = ""


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


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
