from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import logging
from pathlib import Path
import tempfile
from typing import Any

from fastapi import UploadFile

from core.agent.skill_executor import SkillScriptExecutor, SkillScriptRequest
from core.agent.skill_loader import SkillLoader
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState
from core.archivefs.service import ArchiveSkillScriptRunner
from core.clawbot.planner import ToolPlan
from core.clawbot.tool_domains import FileToolHandler, SkillToolHandler, UserMemoryToolHandler
from core.clawbot.tool_runtime import (
    AmbiguousItemReferenceError,
    ArchiveToolRuntimeHost,
    ToolExecutionResult,
)
from core.ingestion.service import IngestionService
from core.schemas.tool import ToolCall, ToolResult
from core.storage.models import ClarificationStateRecord, ItemRecord
from core.storage.repositories import (
    ChannelSessionMapRepository,
    ClarificationRepository,
    ItemRepository,
)
from core.tools import ToolInvocation, register_builtin_tools, registry
from core.topics.service import TopicOrganizerService

logger = logging.getLogger(__name__)


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
        clarification_repository: ClarificationRepository,
        topic_organizer: TopicOrganizerService | None = None,
        archive_runner: ArchiveSkillScriptRunner | None = None,
        gateway_service: Any | None = None,
        session_map_repository: ChannelSessionMapRepository | None = None,
        channel_name: str = "wechat",
        user_memory_path: Path | None = None,
        file_tool_root: Path | None = None,
        skill_roots: list[Path] | None = None,
        runtime_manager: AgentRuntimeManager | None = None,
    ) -> None:
        self.runtime_manager = runtime_manager or AgentRuntimeManager(
            clarification_repository=clarification_repository,
        )
        self.archive_host = ArchiveToolRuntimeHost(
            ingestion_service=ingestion_service,
            item_repository=item_repository,
            clarification_repository=clarification_repository,
            topic_organizer=topic_organizer,
            archive_runner=archive_runner,
            gateway_service=gateway_service,
            session_map_repository=session_map_repository,
            channel_name=channel_name,
        )
        self.user_memory_tools = UserMemoryToolHandler.from_path(user_memory_path or Path("user-memory/USER.md"))
        self.file_tools = FileToolHandler.from_root(file_tool_root or Path("."))
        self.skill_tools = SkillToolHandler.from_roots(skill_roots)
        self.skill_loader = SkillLoader(skill_roots=skill_roots)
        self.skill_script_executor = SkillScriptExecutor(skill_loader=self.skill_loader)
        register_builtin_tools()

    @property
    def ingestion_service(self) -> IngestionService:
        return self.archive_host.ingestion_service

    @property
    def item_repository(self) -> ItemRepository:
        return self.archive_host.item_repository

    @property
    def clarification_repository(self) -> ClarificationRepository:
        return self.archive_host.clarification_repository

    @property
    def topic_organizer(self) -> TopicOrganizerService | None:
        return self.archive_host.topic_organizer

    @property
    def gateway_service(self) -> Any | None:
        return self.archive_host.gateway_service

    @gateway_service.setter
    def gateway_service(self, value: Any | None) -> None:
        self.archive_host.gateway_service = value

    @property
    def session_map_repository(self) -> ChannelSessionMapRepository | None:
        return self.archive_host.session_map_repository

    @session_map_repository.setter
    def session_map_repository(self, value: ChannelSessionMapRepository | None) -> None:
        self.archive_host.session_map_repository = value

    @property
    def channel_name(self) -> str:
        return self.archive_host.channel_name

    @channel_name.setter
    def channel_name(self, value: str) -> None:
        self.archive_host.channel_name = value

    def can_send_files_to_user(self) -> bool:
        return self.archive_host.can_send_files_to_user()

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
            success=True,
            content=execution.reply,
            status=execution.status,
            disposition="clarify" if execution.needs_clarification else execution.disposition,
            action=execution.action,
            state_update={
                "last_action": next_runtime.last_action,
                "current_source_event_id": next_runtime.current_source_event_id,
                "pending_skill": next_runtime.pending_skill,
                "skill_state": dict(next_runtime.skill_state),
            },
            artifacts=list(execution.artifacts or []),
            metadata={
                "action": execution.action,
                "item_id": execution.item_id,
                "needs_clarification": execution.needs_clarification,
                "runtime_state": next_runtime,
            },
            error=None,
        )

    async def _dispatch_invocation(self, invocation: ToolInvocation) -> ToolExecutionResult:
        logger.info(
            "tool execute_start session_id=%s tool=%s text=%s has_upload=%s",
            invocation.session_id,
            invocation.plan.tool,
            (invocation.text or "")[:160],
            bool(invocation.upload and (invocation.upload.filename or "").strip()),
        )
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
        if execution.state_update is not None:
            next_snapshot = self.runtime_manager.apply_state_update(
                snapshot=next_snapshot,
                state_update=execution.state_update,
            )
        return self.runtime_manager.build_runtime_state(
            session_id=runtime.session_id,
            context_snapshot=next_snapshot,
            source_message_id=str(runtime.metadata.get("source_message_id") or ""),
            raw_text=runtime.metadata.get("raw_text"),
            upload=request_upload(runtime),
        )

    async def _tool_archive(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.execute_archive(invocation)

    async def _tool_archive_state(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.execute_archive_state(invocation)

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

    def _tool_skill_run(self, invocation: ToolInvocation) -> ToolExecutionResult:
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
            result = self.skill_script_executor.run(
                SkillScriptRequest(
                    skill_name=skill_name,
                    script_path=script_path,
                    input_payload=payload,
                )
            )
        except ValueError as exc:
            return ToolExecutionResult(reply=str(exc), action="skill", status="failed", disposition="respond")
        state_update_payload = dict(result.get("state_update") or {})
        execution = ToolExecutionResult(
            reply=str(result.get("message") or ""),
            action=str(result.get("action") or f"skill.{skill_name}"),
            status=str(result.get("status") or "completed"),
            disposition=str(result.get("disposition") or "respond"),
            artifacts=list(result.get("artifacts") or []),
            metadata={"skill_name": skill_name, "script_path": script_path, "result": result},
        )
        if execution.disposition == "clarify":
            execution.needs_clarification = True
        if execution.artifacts:
            first_item = next((artifact for artifact in execution.artifacts if artifact.get("kind") == "item"), None)
            if first_item is not None:
                execution.item_id = str(first_item.get("ref") or "") or None
        execution.state_update = self._tool_state_delta_from_payload(
            invocation=invocation,
            payload=state_update_payload,
        )
        return execution

    async def _tool_save_file(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.save_file(invocation)

    async def _tool_save_content(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.save_content(invocation)

    def _build_skill_payload(self, *, invocation: ToolInvocation, input_payload: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "session_id": invocation.session_id,
            "source_message_id": invocation.source_message_id,
            "source_event_id": invocation.context.get("current_source_event_id"),
            "text": invocation.text,
            "arguments": input_payload,
            "runtime_state": invocation.context,
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
    def _tool_state_delta_from_payload(*, invocation: ToolInvocation, payload: dict[str, Any]) -> Any:
        from core.agent.runtime_state import ToolStateDelta

        current_source_event_id = str(payload.get("current_source_event_id") or invocation.context.get("current_source_event_id") or "").strip()
        return ToolStateDelta(
            last_action=str(payload.get("last_action") or "").strip() or None,
            current_source_event_id=current_source_event_id or None,
            pending_skill=str(payload.get("pending_skill") or "").strip() or None,
            skill_state=dict(payload.get("skill_state") or {}),
        )

    def _tool_overview_knowledge_base(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.overview_knowledge_base(invocation)

    def _tool_list_topics(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.list_topics(invocation)

    def _tool_open_topic(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.open_topic(invocation)

    def _tool_read_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.read_item(invocation)

    def _tool_summarize_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.summarize_item(invocation)

    def _tool_delete_item(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.delete_item(invocation)

    def _tool_clarify_reference(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.clarify_reference(invocation)

    def _create_reference_clarification(
        self,
        *,
        invocation: ToolInvocation,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> ToolExecutionResult:
        return self.archive_host.create_reference_clarification(
            invocation=invocation,
            query=query,
            candidates=candidates,
        )

    def _tool_clarify_capture_intent(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return self.archive_host.clarify_capture_intent(invocation)

    async def _tool_resolve_pending(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.resolve_pending(invocation)

    async def _persist_pending_upload(self, *, upload: UploadFile) -> dict[str, str]:
        return await self.archive_host.persist_pending_upload(upload=upload)

    async def persist_pending_upload_entry(self, *, upload: UploadFile, source_event_id: str | None) -> dict[str, str | None]:
        return await self.archive_host.persist_pending_upload_entry(upload=upload, source_event_id=source_event_id)

    def _build_upload_clarification_question(self, *, upload: UploadFile) -> str:
        return self.archive_host.build_upload_clarification_question(upload=upload)

    async def _resolve_pending_input_interpretation(
        self,
        *,
        invocation: ToolInvocation,
        pending: ClarificationStateRecord,
        pending_payload: dict[str, Any],
        note: str,
    ) -> ToolExecutionResult:
        return await self.archive_host.resolve_pending_input_interpretation(
            invocation=invocation,
            pending=pending,
            pending_payload=pending_payload,
            note=note,
        )

    async def _tool_send_file_to_user(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return await self.archive_host.send_file_to_user(invocation)

    def _resolve_delivery_targets(
        self,
        *,
        session_id: str,
        item: ItemRecord,
        user_text: str | None,
        limit: int = 9,
    ) -> list[dict[str, Any]]:
        return self.archive_host.resolve_delivery_targets(
            session_id=session_id,
            item=item,
            user_text=user_text,
            limit=limit,
        )

    def _resolve_direct_delivery_target(self, *, item: ItemRecord) -> dict[str, Any] | None:
        return self.archive_host.resolve_direct_delivery_target(item=item)

    def _resolve_pending_selected_item(self, *, invocation: ToolInvocation, pending_payload: dict[str, Any]) -> ItemRecord | None:
        return self.archive_host.resolve_pending_selected_item(
            invocation=invocation,
            pending_payload=pending_payload,
        )

    def _resolve_target_item(
        self,
        *,
        session_id: str,
        plan: ToolPlan,
        context: dict[str, Any],
        user_text: str | None,
        purpose: str,
    ) -> ItemRecord:
        return self.archive_host.resolve_target_item(
            session_id=session_id,
            plan=plan,
            context=context,
            user_text=user_text,
            purpose=purpose,
        )

    def _resolve_candidate_item(
        self,
        *,
        session_id: str,
        user_text: str | None,
        title_hint: str,
        purpose: str,
    ) -> ItemRecord:
        return self.archive_host.resolve_candidate_item(
            session_id=session_id,
            user_text=user_text,
            title_hint=title_hint,
            purpose=purpose,
        )

    @staticmethod
    def _build_reference_query(*, user_text: str | None, title_hint: str) -> str:
        return ArchiveToolRuntimeHost.build_reference_query(user_text=user_text, title_hint=title_hint)

    def _find_candidate_items(
        self,
        *,
        session_id: str,
        query: str,
        preferred_types: set[str],
        limit: int,
    ) -> list[tuple[ItemRecord, int]]:
        return self.archive_host.find_candidate_items(
            session_id=session_id,
            query=query,
            preferred_types=preferred_types,
            limit=limit,
        )

    @staticmethod
    def _preferred_item_types(*, user_text: str | None, purpose: str) -> set[str]:
        return ArchiveToolRuntimeHost.preferred_item_types(user_text=user_text, purpose=purpose)

    @staticmethod
    def _candidate_match_score(*, item: ItemRecord, query: str, preferred_types: set[str]) -> int:
        return ArchiveToolRuntimeHost.candidate_match_score(item=item, query=query, preferred_types=preferred_types)

    def _format_item_reply(self, *, item: ItemRecord, mode: str) -> str:
        return self.archive_host.format_item_reply(item=item, mode=mode)

    def _format_topic_reply(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> tuple[str, list[dict[str, Any]]]:
        return self.archive_host.format_topic_reply(topic_matches)

    def _single_short_item_from_topic_matches(self, topic_matches: list[tuple[object, list[ItemRecord]]]) -> ItemRecord | None:
        return self.archive_host.single_short_item_from_topic_matches(topic_matches)

    def _format_summary_reply(self, *, item: ItemRecord) -> str:
        return self.archive_host.format_summary_reply(item=item)

    def _search_archive_assets(self, *, query: str):
        return self.archive_host.search_archive_assets(query=query)

    def _format_archive_lookup_reply(self, lookup):
        return self.archive_host.format_archive_lookup_reply(lookup)

    def _resolve_archive_result_to_item(self, result: dict[str, Any], session_id: str | None = None) -> ItemRecord | None:
        return self.archive_host.resolve_archive_result_to_item(result, session_id=session_id)

    @staticmethod
    def _item_snapshot(item: ItemRecord, *, rank: int) -> dict[str, Any]:
        return ArchiveToolRuntimeHost.item_snapshot(item, rank=rank)

    @staticmethod
    def _extract_rank_from_text(text: str) -> int | None:
        return ArchiveToolRuntimeHost.extract_rank_from_text(text)

    @staticmethod
    def looks_like_link(text: str) -> bool:
        return ArchiveToolRuntimeHost.looks_like_link(text)

    @staticmethod
    def _detect_media_kind(*, upload: UploadFile | None) -> str | None:
        return ArchiveToolRuntimeHost.detect_media_kind(upload=upload)

    @staticmethod
    def _merge_recent_items(*snapshots: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        return ArchiveToolRuntimeHost.merge_recent_items(*snapshots, limit=limit)

    def _build_state_update(
        self,
        *,
        invocation: ToolInvocation,
        last_action: str,
    ):
        return self.archive_host.build_state_update(invocation=invocation, last_action=last_action)


def request_upload(runtime: ConversationRuntimeState) -> UploadFile | None:
    upload = runtime.metadata.get("upload")
    return upload if getattr(upload, "filename", None) is not None else None


__all__ = [
    "AmbiguousItemReferenceError",
    "NativeToolRequest",
    "RuntimeToolExecutor",
    "ToolExecutionResult",
]
