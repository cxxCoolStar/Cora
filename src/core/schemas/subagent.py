from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.schemas.harness import RunBudget

SPAWN_ORCHESTRATOR_AGENT_ROLE = "spawn_orchestrator"
SUBAGENT_WORKER_ROLE = "subagent"
SUBAGENT_SESSION_KIND = "subagent"
DEFAULT_SUBAGENT_POLICY_PROFILE = "background_readonly"


@dataclass(slots=True)
class SpawnWorkerTaskSpec:
    instruction: str
    tool_names: list[str] = field(default_factory=list)
    context_mode: str | None = None


@dataclass(slots=True)
class SpawnWorkersResult:
    parent_run_id: str
    results: list[SpawnWorkerResult] = field(default_factory=list)
    reply: str = ""
    status: str = "completed"
    disposition: str = "respond"
    denied: bool = False
    denial_reason: str | None = None


@dataclass(slots=True)
class SpawnWorkerRequest:
    parent_session_id: str
    source_message_id: str
    instruction: str
    allowed_tool_names: list[str]
    parent_budget: RunBudget | None = None
    parent_run_id: str | None = None
    parent_spawn_depth: int = 0
    parent_max_spawn_depth: int | None = None
    parent_max_child_runs: int | None = None
    policy_profile: str | None = None
    context_mode: str = "isolated"
    run_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubagentResultSpec:
    child_run_id: str
    child_session_id: str
    status: str
    summary: str
    tool_trace_count: int = 0
    allowed_tool_names: list[str] = field(default_factory=list)
    confidence: str = "medium"
    next_action: str = "accept"

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_run_id": self.child_run_id,
            "child_session_id": self.child_session_id,
            "status": self.status,
            "summary": self.summary,
            "tool_trace_count": self.tool_trace_count,
            "allowed_tool_names": list(self.allowed_tool_names),
            "confidence": self.confidence,
            "next_action": self.next_action,
        }


@dataclass(slots=True)
class SpawnWorkerResult:
    parent_run_id: str
    child_session_id: str
    child_run_id: str | None
    reply: str
    status: str
    disposition: str
    parent_trace_events: list[str] = field(default_factory=list)
    child_status: str | None = None
    child_result: SubagentResultSpec | None = None
    denied: bool = False
    denial_reason: str | None = None


def build_subagent_user_text(*, instruction: str, tool_names: list[str]) -> str:
    tools = ", ".join(tool_names)
    return (
        f"{instruction.strip()}\n\n"
        f"[Subagent task]\n"
        f"Tool scope: {tools}"
    )


def parse_spawn_instruction(text: str) -> str:
    normalized = str(text or "").strip()
    if normalized.lower().startswith("/spawn"):
        normalized = normalized[6:].strip()
    return normalized or "Complete the delegated subagent task."


def subagent_result_from_turn(
    *,
    child_run_id: str,
    child_session_id: str,
    allowed_tool_names: list[str],
    turn: Any,
) -> SubagentResultSpec:
    status = str(getattr(turn, "status", "") or "failed")
    summary = str(getattr(turn, "reply", "") or "").strip() or "No subagent summary."
    tool_trace = list(getattr(turn, "tool_trace", []) or [])
    confidence = str(getattr(turn, "confidence", "") or "medium")
    next_action = "accept" if status == "completed" else "review"
    return SubagentResultSpec(
        child_run_id=child_run_id,
        child_session_id=child_session_id,
        status=status,
        summary=summary,
        tool_trace_count=len(tool_trace),
        allowed_tool_names=list(allowed_tool_names),
        confidence=confidence,
        next_action=next_action,
    )


def format_spawn_workers_reply(*, results: list[SpawnWorkerResult]) -> str:
    if not results:
        return "No subagent tasks were spawned."
    if len(results) == 1:
        return results[0].reply
    lines = [f"Spawned {len(results)} subagents in parallel."]
    for index, result in enumerate(results, start=1):
        preview = str(result.reply or "").strip().splitlines()[0][:120]
        lines.append(f"{index}. {preview}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SUBAGENT_POLICY_PROFILE",
    "SPAWN_ORCHESTRATOR_AGENT_ROLE",
    "SUBAGENT_SESSION_KIND",
    "SUBAGENT_WORKER_ROLE",
    "SpawnWorkerRequest",
    "SpawnWorkerResult",
    "SpawnWorkerTaskSpec",
    "SpawnWorkersResult",
    "SubagentResultSpec",
    "build_subagent_user_text",
    "format_spawn_workers_reply",
    "parse_spawn_instruction",
    "subagent_result_from_turn",
]
