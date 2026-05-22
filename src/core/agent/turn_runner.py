from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from fastapi import UploadFile

from core.agent.execution_policy import ExecutionPolicy, ExecutionPolicyResolver
from core.agent.harness import AgentHarness, DefaultAgentHarness, new_run_input
from core.agent.loop import AgentLoop, LoopResult, ToolExecutionTrace
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.turn_policies import (
    ForcedToolSelection,
    NativeToolRoute,
    RetryDirective,
    SkillIntentRoute,
    TurnDecisionPolicy,
    TurnHeuristicDecision,
    ToolReplyPolicy,
    ToolRoutingPolicy,
)
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, RuntimeContextSnapshot
from core.agent.skill_loader import SkillLoader
from core.llm.base import ModelClient
from core.schemas.harness import RunBudget
from core.schemas.message import Message
from core.schemas.execution import ExecutionHints
from core.schemas.tool import ToolCall, ToolResult
from core.schemas.tool import ToolSpec as ModelToolSpec


@dataclass(slots=True)
class PreparedTurn:
    context_snapshot: RuntimeContextSnapshot
    runtime: ConversationRuntimeState
    execution_policy: ExecutionPolicy
    decision_policy: TurnDecisionPolicy
    history: list[Message]
    tool_specs: list[ModelToolSpec]
    tool_names: set[str]


@dataclass(slots=True)
class ToolExecutionSummary:
    tool_name: str
    arguments: dict[str, Any]
    action: str
    status: str
    disposition: str
    artifacts: list[dict[str, Any]]
    hints: ExecutionHints
    metadata: dict[str, Any]


@dataclass(slots=True)
class AgentTurnResult:
    reply: str
    status: str
    disposition: str
    confidence: str
    reason: str
    runtime_context: dict[str, Any]
    tool_trace: list[ToolExecutionSummary]
    trace: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]

    @property
    def primary_tool(self) -> ToolExecutionSummary | None:
        if not self.tool_trace:
            return None
        return self.tool_trace[-1]


@dataclass(slots=True)
class TurnExecutionPlan:
    session_id: str
    user_text: str
    raw_text: str | None
    upload: UploadFile | None
    prepared_turn: PreparedTurn
    initial_loop_result: LoopResult
    turn_decision: TurnHeuristicDecision


@dataclass(slots=True)
class AgentTurnRunner:
    orchestrator: AgentOrchestrator
    loop: AgentLoop
    runtime_manager: AgentRuntimeManager
    skill_loader: SkillLoader
    history_loader: Callable[..., list[Message]]
    delivery_available: Callable[[], bool]
    media_kind_resolver: Callable[[UploadFile | None], str | None]
    tool_specs_resolver: Callable[[ConversationRuntimeState], list[ModelToolSpec]]
    execution_policy_resolver: ExecutionPolicyResolver
    harness: AgentHarness | None = None

    def sync_model_client(self, model_client: ModelClient) -> None:
        self.loop.model_client = model_client

    def build_agent_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        context_snapshot: RuntimeContextSnapshot,
        tool_messages: list[Message],
    ) -> list[Message]:
        prepared_turn = self.prepare_turn(
            session_id=session_id,
            user_text=user_text,
            source_message_id="",
            raw_text=user_text,
            upload=None,
            context_snapshot=context_snapshot,
        )
        messages = self.orchestrator.prompt_builder.build_messages(
            session_id=session_id,
            user_text=user_text,
            runtime=prepared_turn.runtime,
            skills=self.skill_loader.list_skills(),
            history=prepared_turn.history,
            delivery_available=self.delivery_available(),
        )
        messages.extend(tool_messages)
        return messages

    async def run_turn(
        self,
        *,
        session_id: str,
        source_message_id: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        context_snapshot: RuntimeContextSnapshot,
        run_budget: RunBudget | None = None,
        run_metadata: dict[str, Any] | None = None,
    ) -> AgentTurnResult:
        run_metadata_payload = dict(run_metadata or {})
        parent_run_id = str(run_metadata_payload.get("parent_run_id") or "").strip() or None
        agent_role = str(run_metadata_payload.get("agent_role") or "primary").strip() or "primary"
        run_input = new_run_input(
            session_id=session_id,
            source_message_id=source_message_id,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            context_snapshot=context_snapshot,
            budget=run_budget,
            metadata=run_metadata_payload,
            parent_run_id=parent_run_id,
            agent_role=agent_role,
        )
        harness = self.harness or DefaultAgentHarness(runner=self)
        loop_result = await harness.run(run_input=run_input)
        execution_policy = getattr(harness, "execution_policy", None) or self._policy_resolver().for_runtime(
            loop_result.runtime
        )
        return self.loop_result_to_turn_result(
            loop_result,
            execution_policy=execution_policy,
        )

    async def _build_turn_execution_plan(
        self,
        *,
        session_id: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        prepared_turn: PreparedTurn,
        planner_mode: bool = False,
    ) -> TurnExecutionPlan:
        initial_loop_result = await self.orchestrator.handle_turn(
            OrchestratorInput(
                session_id=session_id,
                user_text=user_text,
                runtime=prepared_turn.runtime,
                upload_name=upload.filename if upload is not None else None,
                delivery_available=self.delivery_available(),
                history=prepared_turn.history,
                tool_specs=prepared_turn.tool_specs,
                planner_mode=planner_mode,
            )
        )
        turn_decision = prepared_turn.decision_policy.initial_decision(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=initial_loop_result,
        )
        return TurnExecutionPlan(
            session_id=session_id,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            prepared_turn=prepared_turn,
            initial_loop_result=initial_loop_result,
            turn_decision=turn_decision,
        )

    async def _execute_turn_plan(self, plan: TurnExecutionPlan) -> LoopResult:
        decision = plan.turn_decision
        if decision.forced_tool_selection is not None:
            return await self._forced_tool_loop_result(
                selection=decision.forced_tool_selection,
                session_id=plan.session_id,
                prepared_turn=plan.prepared_turn,
            )
        if decision.retry is None:
            return plan.initial_loop_result
        retry_loop_result = await self._run_retry_loop(
            retry=decision.retry,
            plan=plan,
        )
        fallback_selection = plan.prepared_turn.decision_policy.fallback_tool_selection(
            retry_category=decision.retry.category,
            user_text=plan.user_text,
            raw_text=plan.raw_text,
            upload=plan.upload,
            loop_result=retry_loop_result,
        )
        if fallback_selection is not None:
            return await self._forced_tool_loop_result(
                selection=fallback_selection,
                session_id=plan.session_id,
                prepared_turn=plan.prepared_turn,
            )
        return retry_loop_result

    async def _run_retry_loop(
        self,
        *,
        retry: RetryDirective,
        plan: TurnExecutionPlan,
    ) -> LoopResult:
        retry_messages = self.orchestrator.prompt_builder.build_messages(
            session_id=plan.session_id,
            user_text=plan.user_text,
            runtime=plan.prepared_turn.runtime,
            skills=self.skill_loader.list_skills(),
            history=plan.prepared_turn.history,
            upload_name=plan.upload.filename if plan.upload is not None else None,
            delivery_available=self.delivery_available(),
        )
        retry_messages.insert(
            1,
            Message.system(session_id=plan.session_id, content=retry.instruction),
        )
        return await self.loop.run(
            session_id=plan.session_id,
            initial_messages=retry_messages,
            runtime=plan.prepared_turn.runtime,
            tool_specs=plan.prepared_turn.tool_specs,
        )

    def prepare_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        source_message_id: str,
        raw_text: str | None,
        upload: UploadFile | None,
        context_snapshot: RuntimeContextSnapshot,
    ) -> PreparedTurn:
        runtime = self.runtime_manager.build_runtime_state(
            session_id=session_id,
            context_snapshot=context_snapshot,
            source_message_id=source_message_id,
            raw_text=raw_text,
            upload=upload,
        )
        execution_policy = self._policy_resolver().for_runtime(runtime)
        tool_specs = list(self.tool_specs_resolver(runtime))
        tool_names = {str(spec.name).strip() for spec in tool_specs if str(spec.name or "").strip()}
        return PreparedTurn(
            context_snapshot=context_snapshot,
            runtime=runtime,
            execution_policy=execution_policy,
            decision_policy=self._decision_policy(tool_names=tool_names),
            history=self.history_loader(session_id=session_id, user_text=user_text),
            tool_specs=tool_specs,
            tool_names=tool_names,
        )

    async def _forced_tool_loop_result(
        self,
        *,
        selection: ForcedToolSelection,
        session_id: str,
        prepared_turn: PreparedTurn,
    ) -> LoopResult | None:
        result = await self.loop.tool_executor.execute_tool_call(
            session_id=session_id,
            tool_call=selection.tool_call,
            runtime=prepared_turn.runtime,
        )
        return self._loop_result_from_forced_tool(
            session_id=session_id,
            runtime=prepared_turn.runtime,
            tool_call=selection.tool_call,
            result=result,
            category=selection.category,
        )

    @staticmethod
    def _loop_result_from_forced_tool(
        *,
        session_id: str,
        runtime: ConversationRuntimeState,
        tool_call: ToolCall,
        result: ToolResult,
        category: str,
    ) -> LoopResult:
        next_runtime = result.metadata.get("runtime_state")
        final_runtime = next_runtime if isinstance(next_runtime, ConversationRuntimeState) else runtime
        execution_hints = result.hints.model_copy(deep=True)
        if execution_hints.is_empty():
            execution_hints = ExecutionHints.from_legacy_metadata(result.metadata)
        assistant_message = Message.assistant_tool_calls(
            session_id=session_id,
            content="",
            tool_calls=[
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }
            ],
            metadata={"turn_type": "forced_tool_call", "category": category},
        )
        tool_message = Message.tool(
            session_id=session_id,
            name=tool_call.tool_name,
            tool_call_id=tool_call.id,
            content=AgentLoop._tool_result_content(result=result),
            metadata=result.metadata,
        )
        execution = ToolExecutionTrace(
            tool_name=tool_call.tool_name,
            arguments=dict(tool_call.arguments),
            action=str(result.action or result.metadata.get("action") or "chat"),
            status=result.status,
            disposition=result.disposition,
            content=result.content,
            artifacts=list(result.artifacts),
            hints=execution_hints,
            metadata=dict(result.metadata),
        )
        return LoopResult(
            final_response=result.content,
            trace=[assistant_message, tool_message],
            runtime=final_runtime,
            exit_reason="forced_tool_call",
            steps=1,
            status=result.status if result.success else "failed",
            disposition="clarify" if result.disposition == "clarify" else "respond",
            tool_trace=[execution],
            assistant_response=None,
            artifacts=list(result.artifacts),
        )

    def _tool_routing_policy(self, *, tool_names: set[str] | None = None) -> ToolRoutingPolicy:
        return self._decision_policy(tool_names=tool_names).routing_policy

    def _decision_policy(self, *, tool_names: set[str] | None = None) -> TurnDecisionPolicy:
        return TurnDecisionPolicy.from_runner(self, tool_names=tool_names)

    def forced_tool_selection(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> ForcedToolSelection | None:
        return self._tool_routing_policy().forced_tool_selection(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )

    def fallback_tool_selection(
        self,
        *,
        retry_category: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> ForcedToolSelection | None:
        return self._tool_routing_policy().fallback_tool_selection(
            retry_category=retry_category,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )

    def tool_retry_category(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> str | None:
        return self._tool_routing_policy().tool_retry_category(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )

    def tool_retry_instruction(self, category: str) -> str:
        return self._tool_routing_policy().tool_retry_instruction(category)

    def loop_result_to_turn_result(
        self,
        result: LoopResult,
        *,
        execution_policy: ExecutionPolicy | None = None,
    ) -> AgentTurnResult:
        runtime_context = self.runtime_manager.runtime_to_context(result.runtime)
        active_execution_policy = execution_policy or self._policy_resolver().for_runtime(result.runtime)
        tool_trace = [self._tool_summary_from_loop_trace(entry) for entry in result.tool_trace]
        if result.exit_reason in {"plan_validated", "plan_validation_failed"}:
            reply = result.final_response or "计划处理未完成，请重试 /plan。"
            reason = "Planner produced a structured plan outcome."
            confidence = "high"
        elif not result.tool_trace:
            reply = result.final_response or "我暂时还不能理解这个请求，你可以换一种说法试试。"
            reason = "The model responded directly." if result.exit_reason == "assistant_text" else "Tool-calling loop produced no final answer."
            confidence = "medium" if result.exit_reason == "assistant_text" else "low"
        else:
            primary_tool = result.tool_trace[-1]
            reply = self._decision_policy().select_final_agent_reply(
                last_execution=primary_tool,
                assistant_text=result.assistant_response,
            )
            if result.disposition == "clarify":
                reason = "The model requested clarification through a tool call."
            elif result.exit_reason == "assistant_text":
                reason = "The model used one or more tools before answering."
            else:
                reason = "The model completed after repeated tool use."
            confidence = "high"
        disposition = result.disposition
        if active_execution_policy.normalize_disposition(disposition=disposition) != disposition:
            disposition = active_execution_policy.normalize_disposition(disposition=disposition)
            reply = self._suppressed_clarification_reply(
                result=result,
                fallback_reply=reply,
                execution_policy=active_execution_policy,
            )
            reason = "Background execution could not wait for clarification, so it stopped without leaving a pending question."
        return AgentTurnResult(
            reply=reply,
            status=result.status,
            disposition=disposition,
            confidence=confidence,
            reason=reason,
            runtime_context=runtime_context,
            tool_trace=tool_trace,
            trace=[
                {
                    "role": message.role,
                    "content": message.content,
                    "name": getattr(message, "name", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "metadata": self._sanitize_json(dict(message.metadata or {})),
                }
                for message in result.trace
            ],
            artifacts=self._sanitize_json(list(result.artifacts)),
        )

    @staticmethod
    def _suppressed_clarification_reply(
        *,
        result: LoopResult,
        fallback_reply: str,
        execution_policy: ExecutionPolicy,
    ) -> str:
        if result.tool_trace:
            hints = result.tool_trace[-1].hints
            metadata = dict(result.tool_trace[-1].metadata or {})
            return execution_policy.suppressed_clarification_reply(
                hints=hints,
                metadata=metadata,
                fallback_reply=fallback_reply,
            )
        return execution_policy.suppressed_clarification_reply(
            fallback_reply=fallback_reply,
        )

    def _policy_resolver(self) -> ExecutionPolicyResolver:
        return getattr(self, "execution_policy_resolver", ExecutionPolicyResolver())

    @staticmethod
    def _tool_summary_from_loop_trace(entry: ToolExecutionTrace) -> ToolExecutionSummary:
        return ToolExecutionSummary(
            tool_name=entry.tool_name,
            arguments=dict(entry.arguments),
            action=entry.action,
            status=entry.status,
            disposition=entry.disposition,
            artifacts=list(entry.artifacts),
            hints=entry.hints.model_copy(deep=True),
            metadata=dict(entry.metadata),
        )

    @staticmethod
    def select_final_agent_reply(*, last_execution: ToolExecutionTrace, assistant_text: str | None) -> str:
        return TurnDecisionPolicy(
            routing_policy=ToolRoutingPolicy(),
        ).select_final_agent_reply(
            last_execution=last_execution,
            assistant_text=assistant_text,
        )

    @staticmethod
    def _should_prefer_tool_reply(*, last_execution: ToolExecutionTrace) -> bool:
        return ToolReplyPolicy._should_prefer_tool_reply(last_execution=last_execution)

    @classmethod
    def _natural_tool_reply(cls, *, last_execution: ToolExecutionTrace) -> str | None:
        return ToolReplyPolicy._natural_tool_reply(last_execution=last_execution)

    @staticmethod
    def _session_search_hit_snippet(hit: dict[str, Any]) -> str:
        return ToolReplyPolicy._session_search_hit_snippet(hit)

    def _forced_skill_selection_for_intent(
        self,
        *,
        intent: str,
        query: str,
        category: str,
    ) -> ForcedToolSelection | None:
        return self._tool_routing_policy()._forced_skill_selection_for_intent(
            intent=intent,
            query=query,
            category=category,
        )
    def _available_tool_names(self) -> set[str]:
        policy = self._tool_routing_policy()
        return policy.tool_names_provider() if policy.tool_names_provider is not None else set()
    def _tool_is_available(self, tool_name: str) -> bool:
        return self._tool_routing_policy()._tool_is_available(tool_name)
    def _native_tool_routes(self) -> list[NativeToolRoute]:
        return self._tool_routing_policy()._native_tool_routes()
    def _matching_native_tool_route(
        self,
        *,
        text: str,
        lowered: str,
    ) -> NativeToolRoute | None:
        return self._tool_routing_policy()._matching_native_tool_route(
            text=text,
            lowered=lowered,
        )
    def _native_tool_route_for_category(self, category: str) -> NativeToolRoute | None:
        return self._tool_routing_policy()._native_tool_route_for_category(category)
    def _forced_native_tool_selection_for_category(
        self,
        *,
        category: str,
        text: str,
    ) -> ForcedToolSelection | None:
        return self._tool_routing_policy()._forced_native_tool_selection_for_category(
            category=category,
            text=text,
        )
    def _skill_intent_routes(self) -> list[SkillIntentRoute]:
        return self._tool_routing_policy()._skill_intent_routes()
    def _find_skill_intent_route(self, *, intent: str) -> SkillIntentRoute | None:
        return self._tool_routing_policy()._find_skill_intent_route(intent=intent)
    def _matches_skill_intent_for_text(
        self,
        *,
        text: str,
        lowered: str,
        intent: str,
    ) -> bool:
        return self._tool_routing_policy()._matches_skill_intent_for_text(
            text=text,
            lowered=lowered,
            intent=intent,
        )
    @staticmethod
    def _looks_like_delivery_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_delivery_request(text=text, lowered=lowered)
    @staticmethod
    def _looks_like_delete_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_delete_request(text=text, lowered=lowered)
    @staticmethod
    def _looks_like_user_memory_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_user_memory_request(text=text, lowered=lowered)
    @staticmethod
    def _looks_like_scheduled_task_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_scheduled_task_request(text=text, lowered=lowered)
    @staticmethod
    def _looks_like_session_search_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_session_search_request(text=text, lowered=lowered)
    @classmethod
    def _search_sessions_fallback_arguments(cls, text: str) -> dict[str, Any] | None:
        return ToolRoutingPolicy._search_sessions_fallback_arguments(text)
    @classmethod
    def _refined_session_search_query(cls, text: str) -> str:
        return ToolRoutingPolicy._refined_session_search_query(text)
    @staticmethod
    def _cleanup_session_search_candidate(text: str) -> str:
        return ToolRoutingPolicy._cleanup_session_search_candidate(text)
    @classmethod
    def _keyword_focused_session_search_query(cls, text: str) -> str:
        return ToolRoutingPolicy._keyword_focused_session_search_query(text)
    @classmethod
    def _forced_file_tool_selection(cls, *, text: str) -> ForcedToolSelection | None:
        return ToolRoutingPolicy._forced_file_tool_selection(text=text)
    @staticmethod
    def _normalized_forced_write_content(content: str) -> str:
        return ToolRoutingPolicy._normalized_forced_write_content(content)
    @classmethod
    def _forced_web_tool_selection(cls, *, text: str, lowered: str) -> ForcedToolSelection | None:
        return ToolRoutingPolicy._forced_web_tool_selection(text=text, lowered=lowered)
    @staticmethod
    def _looks_like_current_info_request(*, text: str, lowered: str) -> bool:
        return ToolRoutingPolicy._looks_like_current_info_request(text=text, lowered=lowered)
    @staticmethod
    def _normalized_web_search_query(text: str) -> str:
        return ToolRoutingPolicy._normalized_web_search_query(text)
    @staticmethod
    def _sanitize_json(value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                if key == "runtime_state":
                    continue
                sanitized[str(key)] = AgentTurnRunner._sanitize_json(item)
            return sanitized
        if isinstance(value, list):
            return [AgentTurnRunner._sanitize_json(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
