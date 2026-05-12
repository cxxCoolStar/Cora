from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any

from fastapi import UploadFile

from core.agent.skill_effects import HostEffectDispatcher
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, PendingSessionState, RuntimeStateDelta
from core.agent.skill_protocol import SkillExecutionResult, SkillStateDelta, UNSET
from core.agent.skill_executor import SkillScriptExecutor, SkillScriptRequest
from core.agent.skill_loader import SkillLoader
from core.clawbot.planner import ToolPlan
from core.clawbot.tool_domains import FileToolHandler, SkillToolHandler, UserMemoryToolHandler
from core.ingestion.service import IngestionService
from core.schemas.tool import ToolCall, ToolResult
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ItemRepository,
    PendingStateRepository,
)
from core.tools import ToolInvocation, register_builtin_tools, registry


@dataclass(slots=True)
class ToolExecutionResult:
    reply: str
    action: str
    status: str = "completed"
    disposition: str = "continue"
    item_id: str | None = None
    needs_clarification: bool = False
    artifacts: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    state_delta: RuntimeStateDelta | None = None


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
        gateway_service: Any | None = None,
        session_map_repository: ChannelSessionMapRepository | None = None,
        channel_name: str = "wechat",
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        skill_roots: list[Path] | None = None,
        runtime_manager: AgentRuntimeManager | None = None,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.item_repository = item_repository
        self.pending_state_repository = pending_state_repository
        self.gateway_service = gateway_service
        self.session_map_repository = session_map_repository
        self.channel_name = channel_name
        self.runtime_manager = runtime_manager or AgentRuntimeManager(
            pending_state_repository=pending_state_repository,
        )
        self.user_memory_tools = UserMemoryToolHandler.from_path(user_memory_path or Path("user-memory/USER.md"))
        self.file_tools = FileToolHandler.from_root(file_tool_root or Path("."))
        self.skill_tools = SkillToolHandler.from_roots(skill_roots)
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
            context=self.runtime_manager.runtime_to_context(runtime),
        )
        next_runtime = self._apply_runtime_update(runtime=runtime, execution=execution)
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
            metadata={
                "action": execution.action,
                "item_id": execution.item_id,
                "needs_clarification": execution.needs_clarification,
                "runtime_state": next_runtime,
            }
            | dict(execution.metadata or {}),
            error=None,
        )

    async def _dispatch_invocation(self, invocation: ToolInvocation) -> ToolExecutionResult:
        try:
            return await registry.dispatch(self, name=invocation.plan.tool, invocation=invocation)
        except KeyError:
            return ToolExecutionResult(reply="我暂时还不能处理这个请求。", action="chat")

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

    def _tool_user_memory(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.user_memory_tools.execute(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_list_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.list_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_search_files(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.search_files(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

    def _tool_read_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        result = self.file_tools.read_file(invocation)
        return ToolExecutionResult(reply=result.reply, action=result.action)

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
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")
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
        payload = {
            "session_id": invocation.session_id,
            "source_message_id": invocation.source_message_id,
            "source_event_id": invocation.context.get("current_source_event_id"),
            "text": invocation.text,
            "intent": intent,
            "arguments": skill_arguments,
            "runtime_state": invocation.context,
            "storage_dir": str(self.ingestion_service.storage_dir),
            "database_url": self.ingestion_service.item_repository.database.engine.url.render_as_string(hide_password=False),
        }
        upload = invocation.upload
        if upload is not None and (upload.filename or "").strip():
            upload_path = self._persist_temp_upload(upload)
            payload["upload_path"] = upload_path
            payload["upload_name"] = upload.filename
        return payload

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

    def _resolve_external_user_id(self, session_id: str) -> str | None:
        if self.session_map_repository is None:
            return None
        return self.session_map_repository.get_external_user_id(
            channel=self.channel_name,
            session_id=session_id,
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


__all__ = [
    "NativeToolRequest",
    "RuntimeToolExecutor",
    "ToolExecutionResult",
]
