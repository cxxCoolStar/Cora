from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from core.agent.execution_policy import DIRECT_TOOL_PLAN_MODE, ExecutionPolicyResolver
from core.agent.harness import DefaultAgentHarness
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
        self._agent_turn_runner.harness = DefaultAgentHarness(
            runner=self._agent_turn_runner,
            run_record_repository=agent_run_record_repository,
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
        return self._context_manager.build_history(session_id=session_id, current_user_text=user_text).as_messages()

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
            allowed_tool_names=list(budget.allowed_tool_names),
            denied_tool_names=list(budget.denied_tool_names),
        )

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
            steps=record.steps,
            started_at=record.started_at,
            completed_at=record.completed_at,
        )

    @classmethod
    def _agent_run_detail(cls, record) -> AgentRunDetailResponse:
        return AgentRunDetailResponse(
            **cls._agent_run_summary(record).model_dump(),
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
