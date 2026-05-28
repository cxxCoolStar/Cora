from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from time import perf_counter
import tempfile
from urllib.parse import unquote
from typing import Any

from fastapi import UploadFile
import httpx

from core.agent.execution_policy import ExecutionPolicy, ExecutionPolicyResolver
from core.agent.spawn_budget import run_budget_from_dict
from core.agent.subagent_spawner import format_spawn_reply
from core.agent.skill_effects import HostEffectDispatcher
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, PendingSessionState, RuntimeStateDelta
from core.agent.skill_protocol import SkillExecutionResult, SkillStateDelta, UNSET
from core.agent.skill_executor import SkillScriptExecutor, SkillScriptRequest
from core.agent.skill_loader import SkillLoader
from core.clawbot.planner import ToolPlan
from core.clawbot.tool_domains import (
    FileToolHandler,
    ScheduledTaskToolHandler,
    SessionSearchToolHandler,
    SkillToolHandler,
    TerminalToolHandler,
    UserMemoryToolHandler,
    WebToolHandler,
)
from core.ingestion.service import IngestionService
from core.schemas.execution import ExecutionHints, SuppressedPendingRequest
from core.schemas.harness import RunBudget
from core.schemas.subagent import SpawnWorkerTaskSpec
from core.schemas.tool import ToolCall, ToolResult
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ItemRepository,
    MessageRepository,
    PendingStateRepository,
    ScheduledTaskRepository,
    SessionRepository,
    SessionSummaryRepository,
)
from core.tools import ToolInvocation, register_builtin_tools, registry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolExecutionResult:
    reply: str
    action: str
    status: str = "completed"
    disposition: str = "continue"
    item_id: str | None = None
    needs_clarification: bool = False
    artifacts: list[dict[str, Any]] | None = None
    hints: ExecutionHints = field(default_factory=ExecutionHints)
    metadata: dict[str, Any] | None = None
    state_delta: RuntimeStateDelta | None = None

    def __post_init__(self) -> None:
        self.sync_legacy_metadata()

    def sync_legacy_metadata(self) -> None:
        metadata = dict(self.metadata or {})
        metadata.update(self.hints.to_legacy_metadata())
        self.metadata = metadata


@dataclass(slots=True)
class NativeToolRequest:
    session_id: str
    tool_call: ToolCall
    runtime: ConversationRuntimeState

    @property
    def source_message_id(self) -> str:
        return str(self.runtime.metadata.get("source_message_id") or "")

    @property
    def raw_text(self) -> str | None:
        return self.runtime.metadata.get("raw_text")

    @property
    def upload(self) -> UploadFile | None:
        upload = self.runtime.metadata.get("upload")
        return upload if getattr(upload, "filename", None) is not None else None

    def to_plan(self) -> ToolPlan:
        return ToolPlan(
            tool=self.tool_call.tool_name,
            arguments=self.tool_call.arguments,
            reason="LLM selected this tool via native tool calling.",
            source="llm_tool_call",
        )


class RuntimeToolExecutor:
    def __init__(
        self,
        *,
        ingestion_service: IngestionService,
        item_repository: ItemRepository,
        pending_state_repository: PendingStateRepository,
        message_repository: MessageRepository | None = None,
        session_repository: SessionRepository | None = None,
        session_summary_repository: SessionSummaryRepository | None = None,
        scheduled_task_repository: ScheduledTaskRepository | None = None,
        source_event_repository: Any | None = None,
        gateway_service: Any | None = None,
        session_map_repository: ChannelSessionMapRepository | None = None,
        channel_name: str = "wechat",
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        skill_roots: list[Path] | None = None,
        runtime_manager: AgentRuntimeManager | None = None,
        execution_policy_resolver: ExecutionPolicyResolver | None = None,
        web_http_client: httpx.Client | None = None,
        web_tavily_api_key: str | None = None,
        web_tavily_base_url: str | None = None,
        scheduled_task_default_timezone: str | None = None,
        clawbot_service: Any | None = None,
    ) -> None:
        self._clawbot_service = clawbot_service
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.pending_state_repository = pending_state_repository
        self.message_repository = message_repository
        self.session_summary_repository = session_summary_repository
        self.gateway_service = gateway_service
        self.session_map_repository = session_map_repository
        self.channel_name = channel_name
        self.execution_policy_resolver = execution_policy_resolver or ExecutionPolicyResolver()
        self.runtime_manager = runtime_manager or AgentRuntimeManager(
            pending_state_repository=pending_state_repository,
            execution_policy_resolver=self.execution_policy_resolver,
        )
        self.user_memory_tools = UserMemoryToolHandler.from_path(user_memory_path or Path("user-memory/USER.md"))
        self.file_tools = FileToolHandler.from_root(file_tool_root or Path("."))
        self.terminal_tools = TerminalToolHandler.from_root(file_tool_root or Path("."))
        self.web_tools = WebToolHandler.from_client(
            http_client=web_http_client,
            tavily_api_key=web_tavily_api_key,
            tavily_base_url=web_tavily_base_url,
        )
        self.skill_tools = SkillToolHandler.from_roots(skill_roots)
        self.session_search_tools = (
            SessionSearchToolHandler.from_repositories(
                message_repository=message_repository,
                summary_repository=session_summary_repository,
                session_repository=session_repository,
                session_map_repository=session_map_repository,
                channel_name=channel_name,
            )
            if message_repository is not None and session_summary_repository is not None
            else None
        )
        self.scheduled_task_tools = (
            ScheduledTaskToolHandler(
                repository=scheduled_task_repository,
                default_timezone=scheduled_task_default_timezone,
                source_event_repository=source_event_repository,
            )
            if scheduled_task_repository is not None
            else None
        )
        self.skill_loader = SkillLoader(skill_roots=skill_roots)
        self.skill_script_executor = SkillScriptExecutor(skill_loader=self.skill_loader)
        self.effect_dispatcher = HostEffectDispatcher(
            ingestion_service=self.ingestion_service,
            can_send_files_to_user=self.can_send_files_to_user,
            resolve_external_user_id=self._resolve_external_user_id,
            send_file=self._run_send_file,
            persist_temp_upload=self._persist_temp_upload,
            current_source_event_id=self._current_source_event_id,
            item_artifact=self._item_artifact,
            ingest_upload=self._ingest_upload,
        )
        register_builtin_tools()
        logger.info(
            "runtime_tool_executor initialized delivery_available=%s registered_tools=%s",
            self.can_send_files_to_user(),
            registry.names(),
        )

    def bind_clawbot_service(self, clawbot_service: Any) -> None:
        self._clawbot_service = clawbot_service

    def can_send_files_to_user(self) -> bool:
        return self.gateway_service is not None and self.session_map_repository is not None

    async def execute(
        self,
        *,
        session_id: str,
        source_message_id: str,
        plan: ToolPlan,
        text: str | None,
        upload: UploadFile | None,
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        invocation = ToolInvocation(
            session_id=session_id,
            source_message_id=source_message_id,
            plan=plan,
            text=text,
            upload=upload,
            context=context,
        )
        return await self._dispatch_invocation(invocation)

    async def execute_tool_call(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        runtime: ConversationRuntimeState,
    ) -> ToolResult:
        request = NativeToolRequest(
            session_id=session_id,
            tool_call=tool_call,
            runtime=runtime,
        )
        execution = await self.execute(
            session_id=request.session_id,
            source_message_id=request.source_message_id,
            plan=request.to_plan(),
            text=request.raw_text,
            upload=request.upload,
            context=self._tool_context_for_runtime(runtime),
        )
        next_runtime = self._apply_runtime_update(runtime=runtime, execution=execution)
        result_metadata = {
            "action": execution.action,
            "item_id": execution.item_id,
            "needs_clarification": execution.needs_clarification,
            "runtime_state": next_runtime,
        } | dict(execution.metadata or {})
        result_metadata.update(execution.hints.to_legacy_metadata())
        return ToolResult(
            success=execution.status != "failed",
            content=execution.reply,
            status=execution.status,
            disposition="clarify" if execution.needs_clarification else execution.disposition,
            action=execution.action,
            state_delta={
                "last_action": next_runtime.last_action,
                "current_source_event_id": next_runtime.current_source_event_id,
                "pending_state": self._pending_state_payload(next_runtime.pending_state),
                "skill_state": dict(next_runtime.skill_state),
            },
            artifacts=list(execution.artifacts or []),
            hints=execution.hints.model_copy(deep=True),
            metadata=result_metadata,
            error=None,
        )

    async def _dispatch_invocation(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = str(invocation.plan.tool or "").strip()
        started_at = perf_counter()
        
        # Check idempotency key for mutating operations on plan resume
        from core.agent.idempotency import generate_idempotency_key, is_tool_idempotent
        
        run_id = invocation.context.get("agent_run_id", "")
        task_id = invocation.context.get("task_id", "")
        idempotency_key = generate_idempotency_key(
            run_id=run_id,
            task_id=task_id,
            tool_name=tool_name,
            tool_arguments=dict(invocation.plan.arguments or {}),
        )
        
        if idempotency_key:
            completed_ops = invocation.context.get("completed_operations", [])
            if idempotency_key in completed_ops:
                # Operation already completed, skip execution
                logger.info(
                    "tool skipped idempotency session_id=%s tool=%s idempotency_key=%s",
                    invocation.session_id,
                    tool_name,
                    idempotency_key,
                )
                return ToolExecutionResult(
                    reply=f"Operation {tool_name} already completed (skipped via idempotency key)",
                    action="tool_skipped",
                    status="completed",
                    disposition="respond",
                    metadata={
                        "idempotency_key": idempotency_key,
                        "skipped": True,
                        "is_idempotent": is_tool_idempotent(tool_name),
                    },
                )
        
        logger.info(
            "tool dispatch start session_id=%s tool=%s arguments=%s available_tools=%s",
            invocation.session_id,
            tool_name,
            dict(invocation.plan.arguments or {}),
            registry.names(),
        )
        progress = self._wechat_progress_session()
        if progress is not None:
            await progress.on_tool_start(tool_name)
        try:
            normalized_invocation = invocation
            if tool_name != invocation.plan.tool:
                normalized_invocation = ToolInvocation(
                    session_id=invocation.session_id,
                    source_message_id=invocation.source_message_id,
                    plan=ToolPlan(
                        tool=tool_name,
                        arguments=dict(invocation.plan.arguments),
                        reason=invocation.plan.reason,
                        source=invocation.plan.source,
                    ),
                    text=invocation.text,
                    upload=invocation.upload,
                    context=dict(invocation.context),
                )
            blocked_result = self._blocked_tool_result(
                invocation=normalized_invocation,
                tool_name=tool_name,
            )
            if blocked_result is not None:
                return blocked_result
            result = await registry.dispatch(self, name=tool_name, invocation=normalized_invocation)
            
            # Record idempotency key for successful mutating operations
            if idempotency_key and result.status == "completed":
                if result.metadata is None:
                    result.metadata = {}
                result.metadata["idempotency_key"] = idempotency_key
                result.metadata["is_idempotent"] = is_tool_idempotent(tool_name)
            
            duration_ms = int((perf_counter() - started_at) * 1000)
            if result.status == "failed":
                logger.warning(
                    "tool dispatch done session_id=%s tool=%s status=%s action=%s duration_ms=%s reply=%s metadata=%s",
                    invocation.session_id,
                    tool_name,
                    result.status,
                    result.action,
                    duration_ms,
                    str(result.reply or "")[:500],
                    dict(result.metadata or {}),
                )
            else:
                logger.info(
                    "tool dispatch done session_id=%s tool=%s status=%s action=%s duration_ms=%s",
                    invocation.session_id,
                    tool_name,
                    result.status,
                    result.action,
                    duration_ms,
                )
            if progress is not None:
                await progress.on_tool_done(
                    tool_name,
                    action=str(result.action or ""),
                    status=str(result.status or ""),
                )
            return result
        except KeyError:
            logger.exception(
                "tool dispatch unknown_tool session_id=%s tool=%s available_tools=%s arguments=%s",
                invocation.session_id,
                tool_name,
                registry.names(),
                dict(invocation.plan.arguments or {}),
            )
            return ToolExecutionResult(reply="我暂时还不能处理这个请求。", action="chat")

        except Exception:
            logger.exception(
                "tool dispatch failed session_id=%s tool=%s duration_ms=%s",
                invocation.session_id,
                tool_name,
                int((perf_counter() - started_at) * 1000),
            )
            raise

    def _apply_runtime_update(
        self,
        *,
        runtime: ConversationRuntimeState,
        execution: ToolExecutionResult,
    ) -> ConversationRuntimeState:
        next_snapshot = self.runtime_manager.snapshot_from_runtime(runtime)
        if execution.state_delta is not None:
            next_snapshot = self.runtime_manager.apply_state_delta(
                snapshot=next_snapshot,
                state_delta=execution.state_delta,
            )
        return self.runtime_manager.build_runtime_state(
            session_id=runtime.session_id,
            context_snapshot=next_snapshot,
            source_message_id=str(runtime.metadata.get("source_message_id") or ""),
            raw_text=runtime.metadata.get("raw_text"),
            upload=request_upload(runtime),
        )

    def _tool_context_for_runtime(self, runtime: ConversationRuntimeState) -> dict[str, Any]:
        return self.runtime_manager.runtime_to_context(runtime)

    def _file_tools_for(self, context: dict[str, Any]) -> FileToolHandler:
        sandbox_root = str(context.get("sandbox_workspace_root") or "").strip()
        if sandbox_root:
            return FileToolHandler.from_root(Path(sandbox_root))
        return self.file_tools

    def _terminal_tools_for(self, context: dict[str, Any]) -> TerminalToolHandler:
        sandbox_root = str(context.get("sandbox_workspace_root") or "").strip()
        if sandbox_root:
            return TerminalToolHandler.from_root(Path(sandbox_root))
        return self.terminal_tools

    def _tool_user_memory(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.user_memory_tools.execute(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_list_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self._file_tools_for(invocation.context).list_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_search_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self._file_tools_for(invocation.context).search_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_read_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self._file_tools_for(invocation.context).read_file(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_write_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self._file_tools_for(invocation.context).write_file(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_shell_exec(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self._terminal_tools_for(invocation.context).execute(invocation)
        return ToolExecutionResult(
            reply=result.reply,
            action=result.action,
            status=result.status,
            metadata=dict(result.metadata or {}),
        )

    def _tool_web_search(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.web_tools.search(invocation)
        return ToolExecutionResult(
            reply=result.reply,
            action=result.action,
            status=result.status,
            metadata=dict(result.metadata or {}),
        )

    def _tool_web_fetch(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.web_tools.fetch(invocation)
        return ToolExecutionResult(
            reply=result.reply,
            action=result.action,
            status=result.status,
            metadata=dict(result.metadata or {}),
        )

    def _tool_search_sessions(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self.session_search_tools is None:
            return ToolExecutionResult(
                reply="search_sessions is not configured for this runtime.",
                action="retrieve",
                status="failed",
            )
        self.session_search_tools.store.channel_name = self.channel_name
        self.session_search_tools.store.session_map_repository = self.session_map_repository
        result = self.session_search_tools.search(invocation)
        return ToolExecutionResult(
            reply=result.reply,
            action=result.action,
            status=result.status,
            metadata=dict(result.metadata or {}),
        )

    def _spawn_context_from_invocation(self, invocation: ToolInvocation) -> tuple[str | None, int, RunBudget]:
        context = dict(invocation.context or {})
        parent_run_id = str(context.get("agent_run_id") or "").strip() or None
        spawn_depth = max(0, int(context.get("spawn_depth") or 0))
        parent_budget = run_budget_from_dict(context.get("run_budget"))
        return parent_run_id, spawn_depth, parent_budget

    async def _tool_spawn_worker(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self._clawbot_service is None:
            return ToolExecutionResult(
                reply="spawn_worker is not configured for this runtime.",
                action="spawn_worker",
                status="failed",
            )
        arguments = dict(invocation.plan.arguments or {})
        instruction = str(arguments.get("instruction") or "").strip()
        if not instruction:
            return ToolExecutionResult(
                reply="spawn_worker requires a non-empty instruction.",
                action="spawn_worker",
                status="failed",
            )
        tool_names = _string_list_argument(arguments.get("tool_names"))
        from core.agent.subagent_context import normalize_context_mode

        context_mode = normalize_context_mode(str(arguments.get("context_mode") or "isolated"))
        parent_run_id, spawn_depth, parent_budget = self._spawn_context_from_invocation(invocation)
        spawn_result = await self._clawbot_service.spawn_worker_for_tool(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            parent_run_id=parent_run_id,
            parent_spawn_depth=spawn_depth,
            parent_budget=parent_budget,
            instruction=instruction,
            tool_names=tool_names or None,
            context_mode=context_mode,
        )
        metadata: dict[str, Any] = {
            "parent_run_id": spawn_result.parent_run_id,
            "child_session_id": spawn_result.child_session_id,
            "child_run_id": spawn_result.child_run_id,
        }
        if spawn_result.child_result is not None:
            metadata["child_result"] = spawn_result.child_result.to_dict()
        reply = (
            spawn_result.reply
            if spawn_result.denied
            else format_spawn_reply(child_result=spawn_result.child_result, denied=False)
        )
        return ToolExecutionResult(
            reply=reply,
            action="spawn_worker",
            status=spawn_result.status,
            disposition=spawn_result.disposition,
            metadata=metadata,
        )

    async def _tool_spawn_workers(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self._clawbot_service is None:
            return ToolExecutionResult(
                reply="spawn_workers is not configured for this runtime.",
                action="spawn_workers",
                status="failed",
            )
        arguments = dict(invocation.plan.arguments or {})
        tasks = _spawn_worker_tasks_from_argument(arguments.get("tasks"))
        if not tasks:
            return ToolExecutionResult(
                reply="spawn_workers requires at least one task with an instruction.",
                action="spawn_workers",
                status="failed",
            )
        parent_run_id, spawn_depth, parent_budget = self._spawn_context_from_invocation(invocation)
        spawn_result = await self._clawbot_service.spawn_workers_for_tool(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            parent_run_id=parent_run_id,
            parent_spawn_depth=spawn_depth,
            parent_budget=parent_budget,
            tasks=tasks,
        )
        metadata: dict[str, Any] = {
            "parent_run_id": spawn_result.parent_run_id,
            "child_results": [
                item.child_result.to_dict()
                for item in spawn_result.results
                if item.child_result is not None
            ],
        }
        return ToolExecutionResult(
            reply=spawn_result.reply,
            action="spawn_workers",
            status=spawn_result.status,
            disposition=spawn_result.disposition,
            metadata=metadata,
        )

    def _tool_scheduled_tasks(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if self.scheduled_task_tools is None:
            return ToolExecutionResult(
                reply="scheduled_tasks is not configured for this runtime.",
                action="automation",
                status="failed",
            )
        result = self.scheduled_task_tools.execute(
            invocation,
            owner_external_user_id=self._resolve_external_user_id(invocation.session_id),
        )
        return ToolExecutionResult(
            reply=result.reply,
            action=result.action,
            status=result.status,
            metadata=dict(result.metadata or {}),
        )

    def _tool_skills_list(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.skill_tools.list_skills(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_skill_view(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.skill_tools.view_skill(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    async def _tool_skill_run(self, invocation: ToolInvocation) -> ToolExecutionResult:
        arguments = dict(invocation.plan.arguments or {})
        skill_name = str(arguments.get("name") or "").strip()
        script_path = str(arguments.get("script_path") or "").strip()
        input_payload = dict(arguments.get("input") or {})
        input_payload = self._normalize_skill_input(
            skill_name=skill_name,
            script_path=script_path,
            input_payload=input_payload,
            invocation=invocation,
        )
        logger.info(
            "skill_run start session_id=%s skill=%s script=%s intent=%s",
            invocation.session_id,
            skill_name,
            script_path,
            str(input_payload.get("intent") or "").strip(),
        )
        if not skill_name:
            return ToolExecutionResult(reply="skill_run 需要提供 skill 名称。", action="skill", status="failed", disposition="respond")
        if not script_path:
            return ToolExecutionResult(reply="skill_run 需要提供脚本路径。", action="skill", status="failed", disposition="respond")
        payload = self._build_skill_payload(invocation=invocation, input_payload=input_payload)
        try:
            skill_result = self.skill_script_executor.run(
                SkillScriptRequest(
                    skill_name=skill_name,
                    script_path=script_path,
                    input_payload=payload,
                )
            )
        except ValueError as exc:
            logger.exception(
                "skill_run failed session_id=%s skill=%s script=%s",
                invocation.session_id,
                skill_name,
                script_path,
            )
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")

        execution = ToolExecutionResult(
            reply=skill_result.message,
            action=skill_result.action,
            status=skill_result.status,
            disposition=skill_result.disposition,
            artifacts=list(skill_result.artifacts),
            metadata={
                "skill_name": skill_name,
                "script_path": script_path,
                "result": skill_result.raw_payload,
                "raw_skill_action": skill_result.action,
            },
            state_delta=self._runtime_state_delta_from_skill_delta(
                invocation=invocation,
                state_delta=skill_result.state_delta,
            ),
        )
        try:
            await self.effect_dispatcher.apply(invocation=invocation, execution=execution, effects=skill_result.effects)
        except ValueError as exc:
            logger.exception(
                "skill_run effect_apply_failed session_id=%s skill=%s script=%s action=%s",
                invocation.session_id,
                skill_name,
                script_path,
                skill_result.action,
            )
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")
        logger.info(
            "skill_run done session_id=%s skill=%s script=%s action=%s status=%s disposition=%s effects=%s",
            invocation.session_id,
            skill_name,
            script_path,
            execution.action,
            execution.status,
            execution.disposition,
            [effect.kind for effect in skill_result.effects],
        )
        self._apply_pending_result(invocation=invocation, skill_name=skill_name, skill_result=skill_result, execution=execution)
        if execution.status == "failed" and execution.action in {"capture", "retrieve", "delete", "organize"}:
            execution.action = "chat"
        if execution.artifacts:
            first_ref = next(
                (
                    str(artifact.get("ref") or "").strip()
                    for artifact in execution.artifacts
                    if artifact.get("kind") == "item" and str(artifact.get("ref") or "").strip()
                ),
                "",
            )
            execution.item_id = first_ref or None
        return execution

    async def _tool_archive_run(self, invocation: ToolInvocation) -> ToolExecutionResult:
        from core.skills.action_dispatcher import dispatch_cora_archive

        arguments = dict(invocation.plan.arguments or {})
        intent = str(arguments.get("intent") or "").strip()
        logger.info(
            "archive_run start session_id=%s intent=%s",
            invocation.session_id,
            intent,
        )
        if not intent:
            return ToolExecutionResult(reply="archive_run 需要提供 intent。", action="skill", status="failed", disposition="respond")
        payload = self._build_skill_payload(invocation=invocation, input_payload=arguments)
        try:
            skill_result = SkillExecutionResult.from_payload(dispatch_cora_archive(payload))
        except (ValueError, RuntimeError) as exc:
            logger.exception("archive_run failed session_id=%s intent=%s", invocation.session_id, intent)
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")

        execution = ToolExecutionResult(
            reply=skill_result.message,
            action=skill_result.action,
            status=skill_result.status,
            disposition=skill_result.disposition,
            artifacts=list(skill_result.artifacts),
            metadata={
                "skill_name": "archive-core",
                "script_path": "adapters/cora/dispatch.py",
                "result": skill_result.raw_payload,
                "raw_skill_action": skill_result.action,
            },
            state_delta=self._runtime_state_delta_from_skill_delta(
                invocation=invocation,
                state_delta=skill_result.state_delta,
            ),
        )
        try:
            await self.effect_dispatcher.apply(invocation=invocation, execution=execution, effects=skill_result.effects)
        except ValueError as exc:
            logger.exception(
                "archive_run effect_apply_failed session_id=%s intent=%s action=%s",
                invocation.session_id,
                intent,
                skill_result.action,
            )
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")
        self._apply_pending_result(
            invocation=invocation,
            skill_name="archive-core",
            skill_result=skill_result,
            execution=execution,
        )
        if execution.status == "failed" and execution.action in {"capture", "retrieve", "delete", "organize"}:
            execution.action = "chat"
        if execution.artifacts:
            first_ref = next(
                (
                    str(artifact.get("ref") or "").strip()
                    for artifact in execution.artifacts
                    if artifact.get("kind") == "item" and str(artifact.get("ref") or "").strip()
                ),
                "",
            )
            execution.item_id = first_ref or None
        return execution

    async def _ingest_upload(
        self,
        *,
        invocation: ToolInvocation,
        upload: UploadFile,
        user_note: str | None,
    ):
        temp_path = Path(self._persist_temp_upload(upload))
        return await self.ingestion_service.ingest_saved_upload(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            source_event_id=self._current_source_event_id(invocation),
            file_path=temp_path,
            filename=upload.filename or temp_path.name,
            user_note=user_note,
        )

    @staticmethod
    def _item_artifact(*, item_id: str | None, topic_name: str | None) -> dict[str, Any]:
        return {"kind": "item", "ref": item_id, "payload": {"topic_name": topic_name}}

    @staticmethod
    def _current_source_event_id(invocation: ToolInvocation) -> str | None:
        return str(invocation.context.get("current_source_event_id") or "").strip() or None

    def _resolve_latest_pending(self, *, session_id: str, status: str) -> None:
        latest = self.pending_state_repository.get_latest_pending(session_id=session_id)
        if latest is not None:
            self.pending_state_repository.resolve(pending_state_id=latest.id, status=status)

    def _build_skill_payload(self, *, invocation: ToolInvocation, input_payload: dict[str, Any]) -> dict[str, Any]:
        skill_arguments = dict(input_payload)
        intent = str(skill_arguments.pop("intent", "") or "").strip()
        database_engine_url = self.ingestion_service.item_repository.database.engine.url
        payload = {
            "session_id": invocation.session_id,
            "source_message_id": invocation.source_message_id,
            "source_event_id": invocation.context.get("current_source_event_id"),
            "text": invocation.text,
            "intent": intent,
            "arguments": skill_arguments,
            "runtime_state": invocation.context,
            "storage_dir": str(self.ingestion_service.storage_dir),
            "database_url": self._normalize_database_url(database_engine_url),
        }
        upload = invocation.upload
        if upload is not None and (upload.filename or "").strip():
            upload_path = self._persist_temp_upload(upload)
            payload["upload_path"] = upload_path
            payload["upload_name"] = upload.filename
        return payload

    def _normalize_skill_input(
        self,
        *,
        skill_name: str,
        script_path: str,
        input_payload: dict[str, Any],
        invocation: ToolInvocation,
    ) -> dict[str, Any]:
        normalized = dict(input_payload)
        if str(normalized.get("intent") or "").strip():
            return normalized
        skill = self.skill_loader.find_skill(skill_name)
        if skill is None:
            return normalized
        runtime_metadata = skill.runtime_metadata or {}
        entrypoint = str(runtime_metadata.get("entrypoint") or "").strip()
        if entrypoint and entrypoint != script_path:
            return normalized
        required_fields = runtime_metadata.get("required_input_fields") or []
        if "intent" not in required_fields:
            return normalized
        inferred_intent = self._infer_skill_intent(
            skill_name=skill_name,
            input_payload=normalized,
            invocation=invocation,
            runtime_metadata=runtime_metadata,
        )
        if inferred_intent:
            normalized["intent"] = inferred_intent
        return normalized

    def _infer_skill_intent(
        self,
        *,
        skill_name: str,
        input_payload: dict[str, Any],
        invocation: ToolInvocation,
        runtime_metadata: dict[str, Any],
    ) -> str | None:
        del skill_name
        phrases = runtime_metadata.get("intent_phrases") or {}
        if not isinstance(phrases, dict):
            phrases = {}

        def supports(intent_name: str) -> bool:
            return intent_name in phrases

        if "question" in input_payload and supports("clarify"):
            return "clarify"
        if "resolution" in input_payload and supports("resolve_pending"):
            return "resolve_pending"
        if (invocation.upload is not None or str(input_payload.get("text") or "").strip()) and supports("save"):
            return "save"
        item_id = str(input_payload.get("item_id") or "").strip()
        query = str(input_payload.get("query") or "").strip()
        lowered_text = str(invocation.text or "").strip().lower()
        if item_id and any(token in lowered_text for token in ("删除", "删掉", "移除", "delete", "remove")):
            return "delete"
        if item_id and any(token in lowered_text for token in ("打开", "读取", "看看", "全文", "read", "open", "show")):
            return "read"
        if lowered_text:
            for intent_name, candidates in phrases.items():
                if not isinstance(candidates, list):
                    continue
                for candidate in candidates:
                    token = str(candidate or "").strip().lower()
                    if token and token in lowered_text:
                        return intent_name
        if query and supports("search"):
            return "search"
        return None

    @staticmethod
    def _normalize_database_url(database_url: Any) -> str:
        rendered = (
            database_url.render_as_string(hide_password=False)
            if hasattr(database_url, "render_as_string")
            else str(database_url)
        )
        if getattr(database_url, "drivername", "") != "sqlite":
            return rendered
        raw_database = getattr(database_url, "database", None)
        if raw_database in {None, "", ":memory:"}:
            return rendered
        db_path = Path(unquote(str(raw_database)))
        absolute_path = db_path if db_path.is_absolute() else db_path.resolve()
        return f"sqlite:///{absolute_path.as_posix()}"

    @staticmethod
    def _persist_temp_upload(upload: UploadFile) -> str:
        suffix = Path(upload.filename or "upload.bin").suffix
        upload.file.seek(0)
        data = upload.file.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        with tempfile.NamedTemporaryFile(prefix="cora-skill-", suffix=suffix, delete=False) as temp_file:
            temp_file.write(data)
            return temp_file.name

    @staticmethod
    def _runtime_state_delta_from_skill_delta(*, invocation: ToolInvocation, state_delta: SkillStateDelta) -> RuntimeStateDelta:
        current_source_event_id = str(state_delta.current_source_event_id or invocation.context.get("current_source_event_id") or "").strip()
        return RuntimeStateDelta(
            last_action=state_delta.last_action,
            current_source_event_id=current_source_event_id or None,
            skill_state=dict(state_delta.skill_state),
        )

    def _apply_pending_result(
        self,
        *,
        invocation: ToolInvocation,
        skill_name: str,
        skill_result: SkillExecutionResult,
        execution: ToolExecutionResult,
    ) -> None:
        pending_request = skill_result.pending_state_delta.request
        if pending_request is not UNSET and pending_request is not None:
            pending = pending_request
            if self._is_background_execution_context(dict(invocation.context or {})):
                policy = self._execution_policy_for_context(invocation.context)
                execution.reply = policy.clarify_suppressed_reply or execution.reply
                execution.status = "failed"
                execution.needs_clarification = True
                execution.disposition = "clarify"
                execution.hints.policy_tag = policy.clarify_policy_tag
                execution.hints.override_reply = policy.clarify_suppressed_reply
                execution.hints.suppressed_pending = SuppressedPendingRequest(
                    question=pending.question,
                    choices=list(pending.choices),
                    payload=dict(pending.payload),
                )
                execution.sync_legacy_metadata()
                return
            record = self._create_pending_record(
                invocation=invocation,
                question=pending.question or execution.reply,
                choices=list(pending.choices),
                payload={"skill_name": skill_name} | dict(pending.payload),
            )
            execution.needs_clarification = True
            execution.disposition = "clarify"
            execution.state_delta = RuntimeStateDelta(
                last_action=execution.state_delta.last_action if execution.state_delta else None,
                current_source_event_id=execution.state_delta.current_source_event_id if execution.state_delta else invocation.context.get("current_source_event_id"),
                pending_state=PendingSessionState(
                    pending_id=record.id,
                    skill_name=skill_name,
                    kind=pending.kind,  # type: ignore[arg-type]
                    question=pending.question or execution.reply,
                    choices=list(pending.choices),
                    payload={"skill_name": skill_name} | dict(pending.payload),
                ),
                skill_state=dict(execution.state_delta.skill_state if execution.state_delta else {}),
            )
        pending_status = str(skill_result.pending_state_delta.status or "").strip()
        if pending_status:
            latest = self.pending_state_repository.get_latest_pending(session_id=invocation.session_id)
            if latest is not None:
                self.pending_state_repository.resolve(pending_state_id=latest.id, status=pending_status)
            state_delta = execution.state_delta or RuntimeStateDelta()
            execution.state_delta = RuntimeStateDelta(
                last_action=state_delta.last_action,
                current_source_event_id=state_delta.current_source_event_id or invocation.context.get("current_source_event_id"),
                pending_state=None,
                skill_state=state_delta.skill_state,
            )

    def _create_pending_record(
        self,
        *,
        invocation: ToolInvocation,
        question: str,
        choices: list[str],
        payload: dict[str, Any],
    ):
        latest = self.pending_state_repository.get_latest_pending(session_id=invocation.session_id)
        if latest is not None:
            self.pending_state_repository.resolve(pending_state_id=latest.id, status="superseded")
        return self.pending_state_repository.create(
            session_id=invocation.session_id,
            source_message_id=invocation.source_message_id,
            question=question,
            candidate_intents=choices,
            pending_payload=payload,
        )

    @staticmethod
    def _pending_state_payload(pending_state: PendingSessionState | None) -> dict[str, Any]:
        if pending_state is None:
            return {}
        return {
            "pending_id": pending_state.pending_id,
            "skill_name": pending_state.skill_name,
            "kind": pending_state.kind,
            "question": pending_state.question,
            "choices": list(pending_state.choices),
            **dict(pending_state.payload),
        }

    def _wechat_progress_session(self):
        if str(self.channel_name or "").strip() != "wechat":
            return None
        from core.channels.wechat.progress import get_active_wechat_progress

        return get_active_wechat_progress()

    def _resolve_external_user_id(self, session_id: str) -> str | None:
        if self.session_map_repository is None:
            return None
        return self.session_map_repository.get_external_user_id(
            channel=self.channel_name,
            session_id=session_id,
        )

    def _execution_policy_for_context(
        self,
        context: dict[str, Any] | None,
    ) -> ExecutionPolicy:
        return self.execution_policy_resolver.for_context(context)

    def _is_background_execution_context(self, context: dict[str, Any]) -> bool:
        return self._execution_policy_for_context(context).background_execution

    def _blocked_tool_result(
        self,
        *,
        invocation: ToolInvocation,
        tool_name: str,
    ) -> ToolExecutionResult | None:
        policy = self._execution_policy_for_context(dict(invocation.context or {}))
        normalized_tool_name = str(tool_name or "").strip()
        blocked_tool_reply = policy.blocked_tool_reply(tool_name=normalized_tool_name)
        if blocked_tool_reply is None:
            return None
        return ToolExecutionResult(
            reply=blocked_tool_reply,
            action="chat",
            status="failed",
            disposition="respond",
            hints=ExecutionHints(
                policy_tag=policy.blocked_tool_policy_tag,
                blocked_tool_name=normalized_tool_name,
            ),
        )

    async def _run_send_file(self, *, user_id: str, file_path: str, file_name: str) -> dict[str, Any]:
        sender = getattr(self.gateway_service, "send_file_to_user", None)
        if sender is None:
            raise ValueError("当前网关不支持文件发送。")
        result = sender(user_id=user_id, file_path=file_path, file_name=file_name)
        if hasattr(result, "__await__"):
            return await result
        return result

    async def persist_pending_upload_entry(self, *, upload: UploadFile, source_event_id: str | None) -> dict[str, str | None]:
        upload_path = self._persist_temp_upload(upload)
        return {
            "upload_path": upload_path,
            "upload_filename": upload.filename or "upload.bin",
            "source_event_id": source_event_id,
        }


def request_upload(runtime: ConversationRuntimeState) -> UploadFile | None:
    upload = runtime.metadata.get("upload")
    return upload if getattr(upload, "filename", None) is not None else None


def _string_list_argument(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _spawn_worker_tasks_from_argument(value: object) -> list[SpawnWorkerTaskSpec]:
    if not isinstance(value, list):
        return []
    tasks: list[SpawnWorkerTaskSpec] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        instruction = str(item.get("instruction") or "").strip()
        if not instruction:
            continue
        context_mode = str(item.get("context_mode") or "").strip() or None
        tasks.append(
            SpawnWorkerTaskSpec(
                instruction=instruction,
                tool_names=_string_list_argument(item.get("tool_names")),
                context_mode=context_mode,
            )
        )
    return tasks


__all__ = [
    "NativeToolRequest",
    "RuntimeToolExecutor",
    "ToolExecutionResult",
]
