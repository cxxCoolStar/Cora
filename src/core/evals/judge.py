from __future__ import annotations

from typing import Any

from core.clawbot.schemas import TurnResponse
from core.evals.models import EvalAssertionFailure, EvalCase, EvalObservedState, EvalStep, EvalStepResult


def evaluate_step(
    *,
    case: EvalCase,
    step: EvalStep,
    index: int,
    response: TurnResponse,
    observed_state: EvalObservedState | None = None,
) -> EvalStepResult:
    failures: list[EvalAssertionFailure] = []
    tool_names = tool_names_from_trace(response.trace)

    if step.expect.status and response.status != step.expect.status:
        failures.append(EvalAssertionFailure(f"expected status={step.expect.status!r}, got {response.status!r}"))
    if step.expect.disposition and response.disposition != step.expect.disposition:
        failures.append(EvalAssertionFailure(f"expected disposition={step.expect.disposition!r}, got {response.disposition!r}"))
    if step.expect.action and response.action != step.expect.action:
        failures.append(EvalAssertionFailure(f"expected action={step.expect.action!r}, got {response.action!r}"))
    if step.expect.max_trace_messages is not None and len(response.trace) > step.expect.max_trace_messages:
        failures.append(
            EvalAssertionFailure(
                f"expected at most {step.expect.max_trace_messages} trace messages, got {len(response.trace)}"
            )
        )
    missing_any_tools = [name for name in step.expect.tool_names_any if name not in tool_names]
    if missing_any_tools and len(missing_any_tools) == len(step.expect.tool_names_any):
        failures.append(
            EvalAssertionFailure(
                f"expected at least one tool from {step.expect.tool_names_any!r}, got {tool_names!r}"
            )
        )
    missing_all_tools = [name for name in step.expect.tool_names_all if name not in tool_names]
    if missing_all_tools:
        failures.append(EvalAssertionFailure(f"missing required tools: {missing_all_tools!r}; got {tool_names!r}"))
    for token in step.expect.reply_contains_all:
        if token not in response.reply:
            failures.append(EvalAssertionFailure(f"reply missing required text: {token!r}"))
    if step.expect.reply_contains_any and not any(token in response.reply for token in step.expect.reply_contains_any):
        failures.append(
            EvalAssertionFailure(f"reply must include at least one of {step.expect.reply_contains_any!r}")
        )
    for token in step.expect.reply_not_contains:
        if token in response.reply:
            failures.append(EvalAssertionFailure(f"reply unexpectedly contains text: {token!r}"))
    if step.expect.artifact_ref_contains_any:
        refs = [str(artifact.get("ref") or "") for artifact in response.artifacts if isinstance(artifact, dict)]
        if not any(any(token in ref for token in step.expect.artifact_ref_contains_any) for ref in refs):
            failures.append(
                EvalAssertionFailure(
                    f"artifact refs must include one of {step.expect.artifact_ref_contains_any!r}; got {refs!r}"
                )
            )
    state_expect = step.expect.state
    if state_expect.item_count is not None:
        actual = observed_state.item_count if observed_state is not None else None
        if actual != state_expect.item_count:
            failures.append(EvalAssertionFailure(f"expected state.item_count={state_expect.item_count!r}, got {actual!r}"))
    if state_expect.deleted_item_count is not None:
        actual = observed_state.deleted_item_count if observed_state is not None else None
        if actual != state_expect.deleted_item_count:
            failures.append(
                EvalAssertionFailure(
                    f"expected state.deleted_item_count={state_expect.deleted_item_count!r}, got {actual!r}"
                )
            )
    if state_expect.pending_exists is not None:
        actual = observed_state.pending_exists if observed_state is not None else None
        if actual != state_expect.pending_exists:
            failures.append(EvalAssertionFailure(f"expected state.pending_exists={state_expect.pending_exists!r}, got {actual!r}"))
    if state_expect.pending_kind is not None:
        actual = observed_state.pending_kind if observed_state is not None else None
        if actual != state_expect.pending_kind:
            failures.append(EvalAssertionFailure(f"expected state.pending_kind={state_expect.pending_kind!r}, got {actual!r}"))
    user_memory_text = observed_state.user_memory_text if observed_state is not None else ""
    for token in state_expect.user_memory_contains_all:
        if token not in user_memory_text:
            failures.append(EvalAssertionFailure(f"user memory missing required text: {token!r}"))
    if state_expect.user_memory_contains_any and not any(token in user_memory_text for token in state_expect.user_memory_contains_any):
        failures.append(
            EvalAssertionFailure(f"user memory must include at least one of {state_expect.user_memory_contains_any!r}")
        )
    for token in state_expect.user_memory_not_contains:
        if token in user_memory_text:
            failures.append(EvalAssertionFailure(f"user memory unexpectedly contains text: {token!r}"))

    return EvalStepResult(
        index=index,
        label=step.label or f"{case.id}#{index}",
        ok=not failures,
        failures=failures,
        response=response.model_dump(),
        tool_names=tool_names,
        failure_category=None if not failures else "assertion_failure",
        observed_state=observed_state,
    )


def tool_names_from_trace(trace: list[dict[str, Any]]) -> list[str]:
    tool_names: list[str] = []
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") == "tool" and isinstance(entry.get("name"), str):
            name = str(entry["name"]).strip()
            if name:
                tool_names.append(name)
    return tool_names
