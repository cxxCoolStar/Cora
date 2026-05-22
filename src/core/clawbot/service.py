from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.agent.execution_policy import DIRECT_TOOL_PLAN_MODE, ExecutionPolicyResolver
from core.agent.harness import DefaultAgentHarness
from core.agent.plan_executor import (
    PlanExecutionResult,
    PlanExecutor,
    format_plan_execution_reply,
    plan_subagent_run_budget,
)
from core.schemas.plan import PlanSpec, TaskResultSpec, TaskSpec
from core.agent.plan_planner import PLANNER_AGENT_ROLE, planner_run_budget
from core.agent.plan_reviewer import (
    REVIEWER_AGENT_ROLE,
    PlanReviewVerdict,
    build_reviewer_user_text,
    reviewer_run_budget,
    verdict_from_turn_reply,
)
from core.agent.plan_execution_state import StoredPlanExecution
from core.agent.plan_store import InMemoryPlanStore, PlanStore, StoredValidatedPlan, stored_plan_from_metadata
from core.agent.subagent_spawner import SubagentSpawner
from core.schemas.subagent import (
    SpawnWorkerRequest,
    SpawnWorkerTaskSpec,
    SpawnWorkersResult,
    parse_spawn_instruction,
)
from core.agent.hitl_store import HitlStore, InMemoryHitlStore
from core.agent.loop import AgentLoop
from core.agent.context_manager import SessionContextManager
from core.agent.context_budget import ContextBudgetManager
from core.agent.orchestrator import AgentOrchestrator
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import RuntimeContextSnapshot
from core.agent.session_runtime import SessionRuntimeSnapshotLoader
from core.clawbot.schemas import (
    AgentRunDetailResponse,
    AgentRunSummaryResponse,
    AgentRunTraceEventResponse,
    DeleteItemResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
    SessionDebugResponse,
    TurnResponse,
)
from core.clawbot.service_runtime import (
    AssistantTurnOutcome,
    ClawBotSessionShell,
    SessionDebugAssembler,
)
from core.agent.skill_loader import SkillLoader
from core.agent.turn_runner import AgentTurnRunner
from core.agent.run_records import AgentRunRecordRepository
from core.clawbot.planner import ToolPlan
from core.clawbot.source_events import SourceEventManager
from core.clawbot.tools import RuntimeToolExecutor
from core.clawbot.user_profile import UserProfileAggregator
from core.ingestion.service import IngestionService
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.harness import RunBudget
from core.schemas.tool import ToolSpec as ModelToolSpec
from core.storage.models import SessionRecord
from core.storage.repositories import (
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    SessionRepository,
    SessionSummaryRepository,
    SourceEventRepository,
    TopicRepository,
    UserSignalRepository,
)
from core.tools import ToolManager
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)


class ClawBotService:
    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        session_summary_repository: SessionSummaryRepository,
        source_event_repository: SourceEventRepository,
        item_repository: ItemRepository,
        ingestion_service: IngestionService,
        pending_state_repository: PendingStateRepository,
        user_signal_repository: UserSignalRepository,
        topic_repository: TopicRepository,
        model_client: ModelClient,
        tool_executor: RuntimeToolExecutor | None = None,
        topic_organizer: TopicOrganizerService | None = None,
        context_budget_manager: ContextBudgetManager | None = None,
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        tool_manager: ToolManager | None = None,
        toolset_preset: str = "cora-wechat",
        harness_policy_profile: str | None = None,
        wechat_harness_policy_profile: str | None = "wechat_safe",
        job_harness_policy_profile: str | None = "background_readonly",
        agent_run_record_repository: AgentRunRecordRepository | None = None,
        hitl_store: HitlStore | None = None,
        plan_store: PlanStore | None = None,
        harness_max_spawn_depth: int = 1,
        harness_max_child_runs: int = 4,
        harness_max_parallel_spawns: int = 3,
        plan_review_mode: str = "high_risk_only",
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.session_summary_repository = session_summary_repository
        self.source_event_repository = source_event_repository
        self.item_repository = item_repository
        self.ingestion_service = ingestion_service
        self.pending_state_repository = pending_state_repository
        self.user_signal_repository = user_signal_repository
        self.topic_repository = topic_repository
        self.model_client = model_client
        self.user_memory_path = user_memory_path or Path("user-memory/USER.md")
        self.file_tool_root = file_tool_root or Path(".")
        self.tool_manager = tool_manager or ToolManager()
        self.toolset_preset = toolset_preset or "cora-wechat"
        self.harness_policy_profile = str(harness_policy_profile or "").strip() or None
        self.wechat_harness_policy_profile = str(wechat_harness_policy_profile or "").strip() or None
        self.job_harness_policy_profile = str(job_harness_policy_profile or "").strip() or None
        self.harness_max_spawn_depth = max(0, int(harness_max_spawn_depth))
        self.harness_max_child_runs = max(0, int(harness_max_child_runs))
        self.harness_max_parallel_spawns = max(1, int(harness_max_parallel_spawns))
        self.plan_review_mode = str(plan_review_mode or "high_risk_only").strip() or "high_risk_only"
        self.skill_loader = SkillLoader()
        self.user_profile_aggregator = UserProfileAggregator()
        preconfigured_policy_resolver = (
            getattr(tool_executor, "execution_policy_resolver", None)
            if tool_executor is not None
            else None
        )
        self.execution_policy_resolver = preconfigured_policy_resolver or ExecutionPolicyResolver()
        self.runtime_manager = AgentRuntimeManager(
            pending_state_repository=pending_state_repository,
            execution_policy_resolver=self.execution_policy_resolver,
        )
        self.tool_executor = tool_executor or RuntimeToolExecutor(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            pending_state_repository=pending_state_repository,
            message_repository=message_repository,
            session_repository=session_repository,
            session_summary_repository=session_summary_repository,
            source_event_repository=source_event_repository,
            user_memory_path=self.user_memory_path,
            file_tool_root=self.file_tool_root,
            skill_roots=self.skill_loader.skill_roots,
            runtime_manager=self.runtime_manager,
            execution_policy_resolver=self.execution_policy_resolver,
        )
        self.topic_organizer = topic_organizer
        self._tool_specs = self._build_tool_specs()
        self._agent_loop = AgentLoop(
            model_client=self.model_client,
            tool_executor=self.tool_executor,
            tool_specs=self._tool_specs,
            context_budget_manager=context_budget_manager,
        )
        self._agent_orchestrator = AgentOrchestrator(
            loop=self._agent_loop,
            prompt_builder=AgentPromptBuilder(
                user_memory_path=self.user_memory_path,
                execution_policy_resolver=self.execution_policy_resolver,
            ),
            skill_loader=self.skill_loader,
        )
        self._context_manager = SessionContextManager(
            message_repository=message_repository,
            summary_repository=session_summary_repository,
            model_client=model_client,
            budget_manager=context_budget_manager,
        )
        self._runtime_snapshot_loader = SessionRuntimeSnapshotLoader(
            message_repository=message_repository,
            source_event_repository=source_event_repository,
            session_repository=session_repository,
        )
        self._source_event_manager = SourceEventManager(
            source_event_repository=source_event_repository,
        )
        self._session_shell = ClawBotSessionShell(
            message_repository=message_repository,
            pending_state_repository=pending_state_repository,
            tool_executor=self.tool_executor,
            source_event_manager=self._source_event_manager,
        )
        self._debug_assembler = SessionDebugAssembler(
            session_repository=session_repository,
            message_repository=message_repository,
            item_repository=item_repository,
            user_signal_repository=user_signal_repository,
            topic_repository=topic_repository,
            user_profile_aggregator=self.user_profile_aggregator,
        )
        self._agent_turn_runner = AgentTurnRunner(
            orchestrator=self._agent_orchestrator,
            loop=self._agent_loop,
            runtime_manager=self.runtime_manager,
            skill_loader=self.skill_loader,
            history_loader=self._load_agent_history,
            delivery_available=self.tool_executor.can_send_files_to_user,
            media_kind_resolver=self._source_event_manager.detect_media_kind,
            tool_specs_resolver=self._tool_specs_for_runtime,
            execution_policy_resolver=self.execution_policy_resolver,
        )
        self.agent_run_record_repository = agent_run_record_repository
        self.hitl_store = hitl_store or InMemoryHitlStore()
        self.plan_store = plan_store or InMemoryPlanStore()
        self._agent_turn_runner.harness = DefaultAgentHarness(
            runner=self._agent_turn_runner,
            run_record_repository=agent_run_record_repository,
            hitl_store=self.hitl_store,
            default_max_spawn_depth=self.harness_max_spawn_depth,
        )

    def create_session(
        self,
        *,
        session_kind: str = "conversation",
        parent_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        return self.session_repository.create(
            session_kind=session_kind,
            parent_session_id=parent_session_id,
            metadata=metadata,
        )

    def create_job_execution_session(
        self,
        *,
        origin_session_id: str,
        scheduled_task_id: str,
        task_name: str,
        execution_mode: str,
        owner_external_user_id: str | None = None,
    ) -> SessionRecord:
        self.session_repository.get(origin_session_id)
        return self.create_session(
            session_kind="job_execution",
            parent_session_id=origin_session_id,
            metadata={
                "scheduled_task_id": scheduled_task_id,
                "scheduled_task_name": task_name,
                "scheduled_task_execution_mode": execution_mode,
                "origin_session_id": origin_session_id,
                "owner_external_user_id": str(owner_external_user_id or "").strip() or None,
            },
        )

    def _build_tool_specs(self) -> list[ModelToolSpec]:
        return self.tool_manager.build_model_tool_specs(toolset_preset=self.toolset_preset)

    def _tool_specs_for_runtime(self, runtime) -> list[ModelToolSpec]:
        policy = self.execution_policy_resolver.for_runtime(runtime)
        return policy.filter_tool_specs(self._tool_specs)

    def refresh_tool_specs(self) -> None:
        self._tool_specs = self._build_tool_specs()
        self._agent_loop.tool_specs = self._tool_specs

    def build_agent_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        context_snapshot: RuntimeContextSnapshot,
        tool_messages: list[Message],
    ) -> list[Message]:
        return self._agent_turn_runner.build_agent_messages(
            session_id=session_id,
            user_text=user_text,
            context_snapshot=context_snapshot,
            tool_messages=tool_messages,
        )

    async def run_agent_loop(
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
    ):
        self._agent_turn_runner.sync_model_client(self.model_client)
        return await self._agent_turn_runner.run_turn(
            session_id=session_id,
            source_message_id=source_message_id,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            context_snapshot=context_snapshot,
            run_budget=run_budget,
            run_metadata=run_metadata,
        )

    def _load_agent_history(self, *, session_id: str, user_text: str) -> list[Message]:
        from core.agent.subagent_context import resolve_subagent_history_session_id

        history_session_id = resolve_subagent_history_session_id(
            session_id=session_id,
            session_repository=self.session_repository,
        )
        return self._context_manager.build_history(
            session_id=history_session_id,
            current_user_text=user_text,
        ).as_messages()

    def _run_budget_for_turn(
        self,
        *,
        run_budget: RunBudget | None,
        context_snapshot: RuntimeContextSnapshot,
        source_metadata: dict[str, Any] | None,
    ) -> RunBudget:
        budget = run_budget or RunBudget()
        if str(budget.policy_profile or "").strip():
            return budget
        default_profile = self._default_harness_policy_profile(
            context_snapshot=context_snapshot,
            source_metadata=source_metadata,
        )
        if not default_profile:
            return budget
        return RunBudget(
            policy_profile=default_profile,
            max_steps=budget.max_steps,
            timeout_seconds=budget.timeout_seconds,
            max_tool_calls=budget.max_tool_calls,
            max_spawn_depth=budget.max_spawn_depth,
            max_child_runs=budget.max_child_runs,
            allowed_tool_names=list(budget.allowed_tool_names),
            denied_tool_names=list(budget.denied_tool_names),
            approved_tool_names=list(budget.approved_tool_names),
        )

    def _merge_resume_run_budget(
        self,
        *,
        run_budget: RunBudget | None,
        approved_tool_name: str,
    ) -> RunBudget:
        budget = run_budget or RunBudget()
        approved_names = list(budget.approved_tool_names)
        if approved_tool_name not in approved_names:
            approved_names.append(approved_tool_name)
        return RunBudget(
            policy_profile=budget.policy_profile,
            max_steps=budget.max_steps,
            timeout_seconds=budget.timeout_seconds,
            max_tool_calls=budget.max_tool_calls,
            max_spawn_depth=budget.max_spawn_depth,
            max_child_runs=budget.max_child_runs,
            allowed_tool_names=list(budget.allowed_tool_names),
            denied_tool_names=list(budget.denied_tool_names),
            approved_tool_names=approved_names,
        )

    async def approve_hitl_and_resume(
        self,
        *,
        session_id: str,
        hitl_id: str,
        text: str | None = None,
        run_budget: RunBudget | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> TurnResponse:
        execution_state = self.plan_store.get_execution(session_id=session_id)
        if execution_state is not None and (
            not hitl_id or execution_state.pending_hitl_id == hitl_id
        ):
            outcome = await self.approve_plan_execution_hitl_and_resume(
                session_id=session_id,
                hitl_id=hitl_id or execution_state.pending_hitl_id,
                text=text,
                source_metadata=source_metadata,
            )
            return self._session_shell.to_turn_response(outcome=outcome)
        request = self.hitl_store.approve(hitl_id=hitl_id)
        if request.session_id != session_id:
            raise ValueError(
                f"HITL request {hitl_id} belongs to session {request.session_id}, not {session_id}"
            )
        resume_text = text
        if not resume_text:
            payload = json.dumps(request.tool_arguments, ensure_ascii=False)
            resume_text = f"/tool {request.tool_name} {payload}"
        metadata = dict(source_metadata or {})
        metadata.setdefault(
            "platform",
            request.metadata.get("platform"),
        )
        metadata["resume_hitl_id"] = hitl_id
        metadata["parent_run_id"] = request.run_id
        return await self.reply(
            session_id=session_id,
            text=resume_text,
            source_metadata=metadata,
            run_budget=self._merge_resume_run_budget(
                run_budget=run_budget,
                approved_tool_name=request.tool_name,
            ),
        )

    def reject_hitl(self, *, session_id: str, hitl_id: str):
        request = self.hitl_store.reject(hitl_id=hitl_id)
        if request.session_id != session_id:
            raise ValueError(
                f"HITL request {hitl_id} belongs to session {request.session_id}, not {session_id}"
            )
        self.plan_store.clear_execution(session_id=session_id)
        return request

    def get_latest_pending_hitl(self, *, session_id: str):
        return self.hitl_store.get_latest_pending_for_session(session_id=session_id)

    async def approve_latest_hitl_and_resume(
        self,
        *,
        session_id: str,
        source_metadata: dict[str, Any] | None = None,
        run_budget: RunBudget | None = None,
    ) -> TurnResponse:
        pending = self.get_latest_pending_hitl(session_id=session_id)
        if pending is None:
            raise KeyError(f"No pending HITL request for session {session_id}")
        return await self.approve_hitl_and_resume(
            session_id=session_id,
            hitl_id=pending.hitl_id,
            source_metadata=source_metadata,
            run_budget=run_budget,
        )

    def reject_latest_hitl(self, *, session_id: str):
        pending = self.get_latest_pending_hitl(session_id=session_id)
        if pending is None:
            raise KeyError(f"No pending HITL request for session {session_id}")
        return self.reject_hitl(session_id=session_id, hitl_id=pending.hitl_id)

    def _default_harness_policy_profile(
        self,
        *,
        context_snapshot: RuntimeContextSnapshot,
        source_metadata: dict[str, Any] | None,
    ) -> str | None:
        if context_snapshot.session_kind == "job_execution":
            return self.job_harness_policy_profile or self.harness_policy_profile
        metadata = dict(source_metadata or {})
        if str(metadata.get("channel") or "").strip() == "wechat":
            return self.wechat_harness_policy_profile or self.harness_policy_profile
        return self.harness_policy_profile

    async def ingest(
        self,
        *,
        session_id: str,
        text: str | None,
        upload: UploadFile | None,
        source_metadata: dict[str, Any] | None = None,
    ) -> TurnResponse:
        logger.info(
            "clawbot ingest_start session_id=%s has_text=%s has_upload=%s",
            session_id,
            bool(text and text.strip()),
            bool(upload and (upload.filename or "").strip()),
        )
        self.session_repository.get(session_id)
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=text,
            upload=upload,
            source_metadata=source_metadata,
        )
        if inbound_turn.buffered_response is not None:
            logger.info(
                "clawbot upload_buffered session_id=%s source_event_id=%s filename=%s",
                session_id,
                inbound_turn.source_event_id,
                upload.filename if upload is not None else "",
            )
            return inbound_turn.buffered_response

        context_snapshot = self.load_context_snapshot(session_id=session_id)
        context_snapshot.current_source_event_id = inbound_turn.source_event_id
        turn_result = await self.run_agent_loop(
            session_id=session_id,
            source_message_id=inbound_turn.source_message_id,
            user_text=inbound_turn.model_text,
            raw_text=text,
            upload=upload,
            context_snapshot=context_snapshot,
            run_budget=self._run_budget_for_turn(
                run_budget=None,
                context_snapshot=context_snapshot,
                source_metadata=source_metadata,
            ),
            run_metadata=source_metadata,
        )
        outcome = self._session_shell.outcome_from_turn_result(turn_result)
        self._session_shell.persist_assistant_turn(
            session_id=session_id,
            outcome=outcome,
        )
        logger.info(
            "clawbot execution_done session_id=%s action=%s disposition=%s tool=%s",
            session_id,
            outcome.action,
            outcome.disposition,
            outcome.tool_name,
        )
        return self._session_shell.to_turn_response(outcome=outcome)

    async def reply(
        self,
        *,
        session_id: str,
        text: str,
        source_metadata: dict[str, Any] | None = None,
        run_budget: RunBudget | None = None,
    ) -> TurnResponse:
        outcome = await self.reply_outcome(
            session_id=session_id,
            text=text,
            source_metadata=source_metadata,
            run_budget=run_budget,
        )
        return self._session_shell.to_turn_response(outcome=outcome)

    async def plan_turn(
        self,
        *,
        session_id: str,
        text: str,
        source_metadata: dict[str, Any] | None = None,
        run_budget: RunBudget | None = None,
    ) -> TurnResponse:
        metadata = dict(source_metadata or {})
        metadata["agent_role"] = PLANNER_AGENT_ROLE
        outcome = await self.reply_outcome(
            session_id=session_id,
            text=text if str(text or "").strip().startswith("/plan") else f"/plan {text}".strip(),
            source_metadata=metadata,
            run_budget=run_budget or planner_run_budget(),
        )
        self._persist_validated_plan(session_id=session_id)
        return self._session_shell.to_turn_response(outcome=outcome)

    async def execute_plan_turn(
        self,
        *,
        session_id: str,
        text: str | None = None,
        plan_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> TurnResponse:
        outcome = await self.execute_plan_outcome(
            session_id=session_id,
            text=text,
            plan_id=plan_id,
            source_metadata=source_metadata,
        )
        return self._session_shell.to_turn_response(outcome=outcome)

    async def spawn_worker_turn(
        self,
        *,
        session_id: str,
        text: str,
        source_metadata: dict[str, Any] | None = None,
        run_budget: RunBudget | None = None,
        parent_run_id: str | None = None,
    ) -> TurnResponse:
        self.session_repository.get(session_id)
        inbound_text = str(text or "").strip()
        if not inbound_text.lower().startswith("/spawn"):
            inbound_text = f"/spawn {inbound_text}".strip()
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=inbound_text,
            upload=None,
            source_metadata=source_metadata,
        )
        budget = self._run_budget_for_turn(
            run_budget=run_budget,
            context_snapshot=self.load_context_snapshot(session_id=session_id),
            source_metadata=source_metadata,
        )
        tool_names = list(budget.allowed_tool_names)
        if not tool_names:
            tool_names = ["search_files"]
        metadata = dict(source_metadata or {})
        resolved_parent_run_id = str(parent_run_id or metadata.get("parent_run_id") or "").strip() or None
        spawn_result = await self.spawn_worker_for_tool(
            session_id=session_id,
            source_message_id=inbound_turn.source_message_id,
            parent_run_id=resolved_parent_run_id or "",
            parent_spawn_depth=int(metadata.get("spawn_depth") or 0),
            parent_budget=budget,
            instruction=parse_spawn_instruction(inbound_text),
            tool_names=tool_names,
            run_metadata=metadata,
        )
        outcome = self._outcome_from_spawn_result(spawn_result)
        self._session_shell.persist_assistant_turn(session_id=session_id, outcome=outcome)
        return self._session_shell.to_turn_response(outcome=outcome)

    async def execute_plan_outcome(
        self,
        *,
        session_id: str,
        text: str | None = None,
        plan_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ):
        from core.clawbot.service_runtime import AssistantTurnOutcome

        self.session_repository.get(session_id)
        stored = self.plan_store.get_latest(session_id=session_id, plan_id=plan_id)
        if stored is None:
            return AssistantTurnOutcome(
                reply="No validated plan is available for this session. Run /plan first.",
                action="plan_execute",
                disposition="clarify",
                status="failed",
                tool_name="none",
                tool_arguments={},
                context=None,
                confidence="high",
                reason="Plan execution requested without a stored validated plan.",
                artifacts=[],
                trace=[],
                tool_trace=[],
            )
        inbound_text = str(text or "").strip() or "/execute"
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=inbound_text,
            upload=None,
            source_metadata=source_metadata,
        )
        context_snapshot = self.load_context_snapshot(session_id=session_id)
        context_snapshot.current_source_event_id = inbound_turn.source_event_id
        run_metadata = dict(source_metadata or {})
        execution = await self._plan_executor().execute(
            session_id=session_id,
            plan=stored.plan,
            planner_run_id=stored.planner_run_id,
            source_message_id=inbound_turn.source_message_id,
            context_snapshot=context_snapshot,
            run_metadata=run_metadata,
        )
        if execution.waiting_hitl:
            pending = self.hitl_store.get_latest_pending_for_session(session_id=session_id)
            if pending is not None and execution.paused_task_index is not None:
                self.plan_store.save_execution(
                    execution=StoredPlanExecution(
                        session_id=session_id,
                        plan=stored.plan,
                        planner_run_id=stored.planner_run_id,
                        source_message_id=inbound_turn.source_message_id,
                        task_index=execution.paused_task_index,
                        task_results=list(execution.plan_run.task_results),
                        pending_hitl_id=pending.hitl_id,
                        run_metadata=run_metadata,
                    )
                )
                execution.pending_hitl_id = pending.hitl_id
        else:
            self.plan_store.clear_execution(session_id=session_id)
        outcome = self._outcome_from_plan_execution(execution)
        self._session_shell.persist_assistant_turn(session_id=session_id, outcome=outcome)
        logger.info(
            "clawbot execute_plan_outcome_done session_id=%s plan_id=%s status=%s disposition=%s",
            session_id,
            stored.plan.plan_id,
            outcome.status,
            outcome.disposition,
        )
        return outcome

    def _plan_executor(self) -> PlanExecutor:
        return PlanExecutor(
            turn_runner=self._agent_turn_runner,
            run_record_repository=self.agent_run_record_repository,
            spawn_workers_for_plan=self._spawn_workers_for_plan_execution,
            review_plan_task=self._review_plan_task,
            plan_review_mode=self.plan_review_mode,
        )

    async def _review_plan_task(
        self,
        *,
        session_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        task_result: TaskResultSpec,
        planner_run_id: str,
        source_message_id: str,
        context_snapshot: RuntimeContextSnapshot,
        metadata_base: dict[str, Any],
    ) -> PlanReviewVerdict | None:
        review_metadata = {
            **dict(metadata_base or {}),
            "agent_role": REVIEWER_AGENT_ROLE,
            "task_id": task.task_id,
            "parent_run_id": task_result.run_id or planner_run_id,
            "plan_id": plan.plan_id,
            "plan": plan.to_dict(),
            "task": task.to_dict(),
            "worker_summary": task_result.summary,
            "worker_run_id": task_result.run_id,
        }
        user_text = build_reviewer_user_text(
            plan=plan,
            task=task,
            worker_summary=task_result.summary,
            worker_run_id=task_result.run_id or None,
        )
        turn_result = await self._agent_turn_runner.run_turn(
            session_id=session_id,
            source_message_id=f"{source_message_id}:{task.task_id}:review",
            user_text=user_text,
            raw_text=user_text,
            upload=None,
            context_snapshot=context_snapshot,
            run_budget=reviewer_run_budget(),
            run_metadata=review_metadata,
        )
        verdict = verdict_from_turn_reply(turn_result.reply)
        if verdict is not None:
            return verdict
        if str(turn_result.status or "").strip() == "failed":
            return PlanReviewVerdict(
                verdict="abort",
                reason=str(turn_result.reply or "Reviewer run failed."),
            )
        return PlanReviewVerdict(
            verdict="abort",
            reason="Reviewer output did not include a recognizable verdict.",
        )

    async def _spawn_workers_for_plan_execution(
        self,
        *,
        session_id: str,
        source_message_id: str,
        planner_run_id: str,
        plan: PlanSpec,
        task: TaskSpec,
        run_metadata: dict[str, Any] | None = None,
    ):
        parent_budget = plan_subagent_run_budget(plan=plan, task=task)
        subagent_tasks = [
            SpawnWorkerTaskSpec(
                instruction=subtask.instruction,
                tool_names=list(subtask.tool_names),
            )
            for subtask in task.parallel_subagents
        ]
        return await self.spawn_workers_for_tool(
            session_id=session_id,
            source_message_id=source_message_id,
            parent_run_id=planner_run_id,
            parent_spawn_depth=0,
            parent_budget=parent_budget,
            tasks=subagent_tasks,
            run_metadata=dict(run_metadata or {}),
        )

    def _subagent_spawner(self) -> SubagentSpawner:
        harness = self._agent_turn_runner.harness
        harness_id = getattr(harness, "id", "default-single-agent")
        return SubagentSpawner(
            turn_runner=self._agent_turn_runner,
            session_repository=self.session_repository,
            run_record_repository=self.agent_run_record_repository,
            harness_id=str(harness_id),
            default_max_spawn_depth=self.harness_max_spawn_depth,
            default_max_child_runs=self.harness_max_child_runs,
            default_max_parallel_spawns=self.harness_max_parallel_spawns,
        )

    async def spawn_worker_for_tool(
        self,
        *,
        session_id: str,
        source_message_id: str,
        parent_run_id: str | None,
        parent_spawn_depth: int,
        parent_budget: RunBudget,
        instruction: str,
        tool_names: list[str] | None = None,
        context_mode: str = "isolated",
        run_metadata: dict[str, Any] | None = None,
    ):
        budget = parent_budget
        names = list(tool_names or budget.allowed_tool_names)
        normalized_parent_run_id = str(parent_run_id or "").strip() or None
        return await self._subagent_spawner().spawn_worker(
            request=SpawnWorkerRequest(
                parent_session_id=session_id,
                source_message_id=source_message_id,
                instruction=instruction,
                allowed_tool_names=names,
                parent_budget=parent_budget,
                parent_run_id=normalized_parent_run_id,
                parent_spawn_depth=parent_spawn_depth,
                context_mode=context_mode,
                run_metadata=dict(run_metadata or {}),
            ),
            parent_context_snapshot=self.load_context_snapshot(session_id=session_id),
            run_budget=budget,
            registered_tool_names=self.list_tool_names(),
        )

    async def spawn_workers_for_tool(
        self,
        *,
        session_id: str,
        source_message_id: str,
        parent_run_id: str | None,
        parent_spawn_depth: int,
        parent_budget: RunBudget,
        tasks: list[SpawnWorkerTaskSpec],
        run_metadata: dict[str, Any] | None = None,
    ) -> SpawnWorkersResult:
        normalized_parent_run_id = str(parent_run_id or "").strip() or None
        return await self._subagent_spawner().spawn_workers(
            request=SpawnWorkerRequest(
                parent_session_id=session_id,
                source_message_id=source_message_id,
                instruction="",
                allowed_tool_names=list(parent_budget.allowed_tool_names),
                parent_budget=parent_budget,
                parent_run_id=normalized_parent_run_id,
                parent_spawn_depth=parent_spawn_depth,
                run_metadata=dict(run_metadata or {}),
            ),
            tasks=tasks,
            parent_context_snapshot=self.load_context_snapshot(session_id=session_id),
            run_budget=parent_budget,
            registered_tool_names=self.list_tool_names(),
            max_parallel=self.harness_max_parallel_spawns,
        )

    @staticmethod
    def _outcome_from_spawn_result(spawn_result):
        from core.clawbot.service_runtime import AssistantTurnOutcome

        return AssistantTurnOutcome(
            reply=spawn_result.reply,
            action="spawn_worker",
            disposition=spawn_result.disposition,
            status=spawn_result.status,
            tool_name="none",
            tool_arguments={},
            context={
                "parent_run_id": spawn_result.parent_run_id,
                "child_session_id": spawn_result.child_session_id,
                "child_run_id": spawn_result.child_run_id,
                "child_result": (
                    spawn_result.child_result.to_dict()
                    if spawn_result.child_result is not None
                    else None
                ),
            },
            confidence="high",
            reason="Subagent worker spawn completed." if not spawn_result.denied else spawn_result.denial_reason or "spawn_denied",
            artifacts=[],
            trace=[
                {
                    "role": "system",
                    "content": event_type,
                    "metadata": {"event_type": event_type},
                }
                for event_type in spawn_result.parent_trace_events
            ],
            tool_trace=[],
        )

    @staticmethod
    def _outcome_from_plan_execution(execution: PlanExecutionResult):
        from core.clawbot.service_runtime import AssistantTurnOutcome

        primary_tool = execution.tool_trace[-1] if execution.tool_trace else None
        return AssistantTurnOutcome(
            reply=execution.reply,
            action="plan_execute",
            disposition=execution.disposition,
            status=execution.status,
            tool_name=str((primary_tool or {}).get("tool_name") or "none"),
            tool_arguments=dict((primary_tool or {}).get("arguments") or {}),
            context=None,
            confidence="high",
            reason="Sequential plan worker execution finished.",
            artifacts=[],
            trace=[
                {
                    "role": "tool",
                    "name": entry.get("tool_name"),
                    "content": str(entry.get("metadata", {}).get("content") or entry.get("tool_name") or ""),
                }
                for entry in execution.tool_trace
                if entry.get("tool_name")
            ],
            tool_trace=list(execution.tool_trace),
        )

    async def approve_plan_execution_hitl_and_resume(
        self,
        *,
        session_id: str,
        hitl_id: str,
        text: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ):
        from core.clawbot.service_runtime import AssistantTurnOutcome

        state = self.plan_store.get_execution(session_id=session_id)
        if state is None:
            raise KeyError(f"No paused plan execution for session {session_id}")
        if state.pending_hitl_id and state.pending_hitl_id != hitl_id:
            raise ValueError(
                f"Paused plan execution expects HITL {state.pending_hitl_id}, not {hitl_id}"
            )
        request = self.hitl_store.approve(hitl_id=hitl_id)
        if request.session_id != session_id:
            raise ValueError(
                f"HITL request {hitl_id} belongs to session {request.session_id}, not {session_id}"
            )
        task_index = max(0, int(state.task_index))
        if task_index >= len(state.plan.tasks):
            raise ValueError(f"Plan execution task index out of range: {task_index}")
        task = state.plan.tasks[task_index]
        resume_metadata = dict(state.run_metadata)
        resume_metadata.update(dict(source_metadata or {}))
        if text and str(text).strip():
            resume_metadata["resume_text"] = str(text).strip()
        else:
            payload = json.dumps(request.tool_arguments, ensure_ascii=False)
            resume_metadata["resume_text"] = f"/tool {request.tool_name} {payload}"
        context_snapshot = self.load_context_snapshot(session_id=session_id)
        executor = self._plan_executor()
        completed_result, resume_trace, _turn_result = await executor.resume_task_after_hitl(
            session_id=session_id,
            plan=state.plan,
            task=task,
            planner_run_id=state.planner_run_id,
            source_message_id=state.source_message_id,
            context_snapshot=context_snapshot,
            run_metadata=resume_metadata,
            hitl_id=hitl_id,
            approved_tool_name=request.tool_name,
        )
        prior_results = [
            result
            for result in state.task_results
            if result.task_id != task.task_id
        ]
        if completed_result.status != "completed":
            self.plan_store.clear_execution(session_id=session_id)
            partial_run = PlanRunSpec(plan_id=state.plan.plan_id, status="failed")
            partial_run.task_results = [*prior_results, completed_result]
            execution = PlanExecutionResult(
                plan_run=partial_run,
                reply=format_plan_execution_reply(plan=state.plan, plan_run=partial_run),
                status="failed",
                disposition="clarify",
                tool_trace=resume_trace,
            )
            outcome = self._outcome_from_plan_execution(execution)
            self._session_shell.persist_assistant_turn(session_id=session_id, outcome=outcome)
            return outcome

        continued = await executor.execute(
            session_id=session_id,
            plan=state.plan,
            planner_run_id=state.planner_run_id,
            source_message_id=state.source_message_id,
            context_snapshot=context_snapshot,
            run_metadata=resume_metadata,
            start_task_index=task_index + 1,
            initial_task_results=[*prior_results, completed_result],
            initial_tool_trace=resume_trace,
        )
        self.plan_store.clear_execution(session_id=session_id)
        if continued.waiting_hitl:
            pending = self.hitl_store.get_latest_pending_for_session(session_id=session_id)
            if pending is not None and continued.paused_task_index is not None:
                self.plan_store.save_execution(
                    execution=StoredPlanExecution(
                        session_id=session_id,
                        plan=state.plan,
                        planner_run_id=state.planner_run_id,
                        source_message_id=state.source_message_id,
                        task_index=continued.paused_task_index,
                        task_results=list(continued.plan_run.task_results),
                        pending_hitl_id=pending.hitl_id,
                        run_metadata=resume_metadata,
                    )
                )
        outcome = self._outcome_from_plan_execution(continued)
        self._session_shell.persist_assistant_turn(session_id=session_id, outcome=outcome)
        logger.info(
            "clawbot approve_plan_execution_resume_done session_id=%s plan_id=%s status=%s",
            session_id,
            state.plan.plan_id,
            outcome.status,
        )
        return outcome

    def _persist_validated_plan(self, *, session_id: str) -> None:
        if self.agent_run_record_repository is None:
            return
        for record in self.agent_run_record_repository.list_by_session(session_id=session_id):
            if record.outcome != "plan_validated":
                continue
            plan_payload = record.metadata.get("plan")
            if not isinstance(plan_payload, dict):
                continue
            self.plan_store.save(
                stored=stored_plan_from_metadata(
                    session_id=session_id,
                    planner_run_id=record.run_id,
                    plan_payload=plan_payload,
                )
            )
            return

    async def reply_outcome(
        self,
        *,
        session_id: str,
        text: str,
        source_metadata: dict[str, Any] | None = None,
        run_budget: RunBudget | None = None,
    ) -> AssistantTurnOutcome:
        self.session_repository.get(session_id)
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=text,
            upload=None,
            source_metadata=source_metadata,
        )
        context_snapshot = self.load_context_snapshot(session_id=session_id)
        context_snapshot.current_source_event_id = inbound_turn.source_event_id
        turn_result = await self.run_agent_loop(
            session_id=session_id,
            source_message_id=inbound_turn.source_message_id,
            user_text=inbound_turn.model_text,
            raw_text=text,
            upload=None,
            context_snapshot=context_snapshot,
            run_budget=self._run_budget_for_turn(
                run_budget=run_budget,
                context_snapshot=context_snapshot,
                source_metadata=source_metadata,
            ),
            run_metadata=source_metadata,
        )
        outcome = self._session_shell.outcome_from_turn_result(turn_result)
        self._session_shell.persist_assistant_turn(
            session_id=session_id,
            outcome=outcome,
        )
        logger.info(
            "clawbot reply_outcome_done session_id=%s action=%s disposition=%s tool=%s",
            session_id,
            outcome.action,
            outcome.disposition,
            outcome.tool_name,
        )
        return outcome

    async def execute_tool_plan_outcome(
        self,
        *,
        session_id: str,
        plan: ToolPlan,
        text: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> AssistantTurnOutcome:
        self.session_repository.get(session_id)
        inbound_text = str(text or "").strip() or f"[scheduled tool execution: {plan.tool}]"
        inbound_turn = await self._session_shell.record_inbound_turn(
            session_id=session_id,
            text=inbound_text,
            upload=None,
            source_metadata=source_metadata,
        )
        context_snapshot = self.load_context_snapshot(session_id=session_id)
        context_snapshot.current_source_event_id = inbound_turn.source_event_id
        runtime = self.runtime_manager.build_runtime_state(
            session_id=session_id,
            context_snapshot=context_snapshot,
            source_message_id=inbound_turn.source_message_id,
            raw_text=inbound_text,
            upload=None,
            execution_mode=DIRECT_TOOL_PLAN_MODE,
        )
        execution_policy = self.execution_policy_resolver.for_runtime(runtime)
        execution = await self.tool_executor.execute(
            session_id=session_id,
            source_message_id=inbound_turn.source_message_id,
            plan=plan,
            text=inbound_text,
            upload=None,
            context=self.runtime_manager.runtime_to_context(runtime),
        )
        next_runtime = self.tool_executor._apply_runtime_update(
            runtime=runtime,
            execution=execution,
        )
        disposition = "clarify" if execution.needs_clarification else execution.disposition
        reply = execution.reply
        if execution_policy.normalize_disposition(disposition=disposition) != disposition:
            disposition = execution_policy.normalize_disposition(disposition=disposition)
            reply = execution_policy.suppressed_clarification_reply(
                hints=execution.hints,
                metadata=execution.metadata,
                fallback_reply=reply,
            )
        outcome = AssistantTurnOutcome(
            reply=reply,
            action=execution.action,
            disposition=disposition,
            status=execution.status,
            tool_name=plan.tool,
            tool_arguments=dict(plan.arguments or {}),
            context=self.runtime_manager.runtime_to_context(next_runtime),
            confidence="system",
            reason=plan.reason or "Scheduled task executed a concrete tool plan.",
            artifacts=list(execution.artifacts or []),
            trace=[],
            tool_trace=[
                {
                    "tool_name": plan.tool,
                    "arguments": dict(plan.arguments or {}),
                    "action": execution.action,
                    "status": execution.status,
                    "disposition": disposition,
                    "artifacts": list(execution.artifacts or []),
                    "hints": execution.hints.model_dump(exclude_none=True),
                    "metadata": dict(execution.metadata or {}),
                }
            ],
            item_id=execution.item_id,
        )
        self._session_shell.persist_assistant_turn(
            session_id=session_id,
            outcome=outcome,
        )
        logger.info(
            "clawbot execute_tool_plan_done session_id=%s tool=%s action=%s disposition=%s",
            session_id,
            plan.tool,
            outcome.action,
            outcome.disposition,
        )
        return outcome

    def list_items(self, *, session_id: str) -> list[ItemSummaryResponse]:
        self.session_repository.get(session_id)
        items = self.item_repository.list_by_session(session_id=session_id, current_only=True)
        return [
            ItemSummaryResponse(
                id=item.id,
                item_type=item.item_type,
                title=item.title,
                summary=item.summary,
                created_at=item.created_at,
            )
            for item in items
        ]

    def get_item(self, *, session_id: str, item_id: str) -> ItemDetailResponse:
        self.session_repository.get(session_id)
        item = self.item_repository.get(item_id=item_id, session_id=session_id)
        return ItemDetailResponse(
            id=item.id,
            item_type=item.item_type,
            title=item.title,
            summary=item.summary,
            normalized_text=item.normalized_text,
            locator_hint=item.locator_hint,
            created_at=item.created_at,
        )

    def delete_item(self, *, session_id: str, item_id: str) -> DeleteItemResponse:
        self.session_repository.get(session_id)
        item = self.item_repository.soft_delete(item_id=item_id, session_id=session_id)
        return DeleteItemResponse(
            reply=f"已删除资料 `{item.title}`。它不会再出现在默认列表和检索结果里。",
            item_id=item.id,
        )

    def list_agent_runs(self, *, session_id: str) -> list[AgentRunSummaryResponse]:
        self.session_repository.get(session_id)
        if self.agent_run_record_repository is None:
            return []
        records = self.agent_run_record_repository.list_by_session(session_id=session_id)
        return [self._agent_run_summary(record) for record in records]

    def get_agent_run(self, *, session_id: str, run_id: str) -> AgentRunDetailResponse:
        self.session_repository.get(session_id)
        if self.agent_run_record_repository is None:
            raise KeyError(f"Agent run record not found: {run_id}")
        record = self.agent_run_record_repository.get(run_id=run_id)
        if record.session_id != session_id:
            raise KeyError(f"Agent run record not found: {run_id}")
        return self._agent_run_detail(record)

    def list_sessions(self) -> list[SessionRecord]:
        return self.session_repository.list_recent(session_kind="conversation")

    def get_session_debug(self, *, session_id: str) -> SessionDebugResponse:
        return self._debug_assembler.build(session_id=session_id)

    def load_context_snapshot(self, *, session_id: str) -> RuntimeContextSnapshot:
        return self._runtime_snapshot_loader.load_context_snapshot(session_id=session_id)

    def list_tool_names(self) -> list[str]:
        return [spec.name for spec in self._tool_specs]

    @staticmethod
    def _agent_run_summary(record) -> AgentRunSummaryResponse:
        return AgentRunSummaryResponse(
            run_id=record.run_id,
            session_id=record.session_id,
            source_message_id=record.source_message_id,
            harness_id=record.harness_id,
            status=record.status,
            outcome=record.outcome,
            trace_id=record.trace_id,
            parent_run_id=record.parent_run_id,
            agent_role=record.agent_role,
            failure_category=record.failure_category,
            cleanup_status=record.cleanup_status,
            steps=record.steps,
            started_at=record.started_at,
            completed_at=record.completed_at,
        )

    @classmethod
    def _agent_run_detail(cls, record) -> AgentRunDetailResponse:
        return AgentRunDetailResponse(
            **cls._agent_run_summary(record).model_dump(),
            budget=dict(record.budget or {}),
            input_metadata=dict(record.input_metadata or {}),
            metadata=dict(record.metadata or {}),
            error=record.error,
            trace_events=[
                AgentRunTraceEventResponse(
                    event_type=event.event_type,
                    run_id=event.run_id,
                    session_id=event.session_id,
                    sequence=event.sequence,
                    severity=event.severity,
                    metadata=dict(event.metadata or {}),
                )
                for event in list(record.trace_events or [])
            ],
        )
