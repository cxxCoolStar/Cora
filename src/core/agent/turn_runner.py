from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from fastapi import UploadFile

from core.agent.loop import AgentLoop, LoopResult, ToolExecutionTrace
from core.agent.orchestrator import AgentOrchestrator, OrchestratorInput
from core.agent.runtime_manager import AgentRuntimeManager
from core.agent.runtime_state import ConversationRuntimeState, RuntimeContextSnapshot
from core.agent.skill_loader import SkillLoader
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.tool import ToolCall, ToolResult


@dataclass(slots=True)
class PreparedTurn:
    context_snapshot: RuntimeContextSnapshot
    runtime: ConversationRuntimeState
    history: list[Message]


@dataclass(slots=True)
class ToolExecutionSummary:
    tool_name: str
    arguments: dict[str, Any]
    action: str
    status: str
    disposition: str
    artifacts: list[dict[str, Any]]
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
class AgentTurnRunner:
    orchestrator: AgentOrchestrator
    loop: AgentLoop
    runtime_manager: AgentRuntimeManager
    skill_loader: SkillLoader
    history_loader: Callable[..., list[Message]]
    delivery_available: Callable[[], bool]
    media_kind_resolver: Callable[[UploadFile | None], str | None]

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
    ) -> AgentTurnResult:
        prepared_turn = self.prepare_turn(
            session_id=session_id,
            user_text=user_text,
            source_message_id=source_message_id,
            raw_text=raw_text,
            upload=upload,
            context_snapshot=context_snapshot,
        )
        loop_result = await self.orchestrator.handle_turn(
            OrchestratorInput(
                session_id=session_id,
                user_text=user_text,
                runtime=prepared_turn.runtime,
                upload_name=upload.filename if upload is not None else None,
                delivery_available=self.delivery_available(),
                history=prepared_turn.history,
            )
        )
        retry_category = self.tool_retry_category(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )
        if retry_category is not None:
            forced_result = await self._forced_tool_loop_result(
                category=retry_category,
                session_id=session_id,
                user_text=user_text,
                prepared_turn=prepared_turn,
            )
            if forced_result is not None:
                loop_result = forced_result
            else:
                retry_instruction = self.tool_retry_instruction(retry_category)
                retry_messages = self.orchestrator.prompt_builder.build_messages(
                    session_id=session_id,
                    user_text=user_text,
                    runtime=prepared_turn.runtime,
                    skills=self.skill_loader.list_skills(),
                    history=prepared_turn.history,
                    upload_name=upload.filename if upload is not None else None,
                    delivery_available=self.delivery_available(),
                )
                retry_messages.insert(
                    1,
                    Message.system(session_id=session_id, content=retry_instruction),
                )
                loop_result = await self.loop.run(
                    session_id=session_id,
                    initial_messages=retry_messages,
                    runtime=prepared_turn.runtime,
                )
        return self.loop_result_to_turn_result(loop_result)

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
        return PreparedTurn(
            context_snapshot=context_snapshot,
            runtime=self.runtime_manager.build_runtime_state(
                session_id=session_id,
                context_snapshot=context_snapshot,
                source_message_id=source_message_id,
                raw_text=raw_text,
                upload=upload,
            ),
            history=self.history_loader(session_id=session_id, user_text=user_text),
        )

    async def _forced_tool_loop_result(
        self,
        *,
        category: str,
        session_id: str,
        user_text: str,
        prepared_turn: PreparedTurn,
    ) -> LoopResult | None:
        if category != "deliver":
            return None
        tool_call = ToolCall(
            tool_name="skill_run",
            arguments={
                "name": "archive-core",
                "script_path": "scripts/archive_dispatch.py",
                "input": {
                    "query": user_text,
                },
            },
        )
        result = await self.loop.tool_executor.execute_tool_call(
            session_id=session_id,
            tool_call=tool_call,
            runtime=prepared_turn.runtime,
        )
        return self._loop_result_from_forced_tool(
            session_id=session_id,
            runtime=prepared_turn.runtime,
            tool_call=tool_call,
            result=result,
        )

    @staticmethod
    def _loop_result_from_forced_tool(
        *,
        session_id: str,
        runtime: ConversationRuntimeState,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> LoopResult:
        next_runtime = result.metadata.get("runtime_state")
        final_runtime = next_runtime if isinstance(next_runtime, ConversationRuntimeState) else runtime
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
            metadata={"turn_type": "forced_tool_call", "category": "deliver"},
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

    def tool_retry_category(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> str | None:
        if loop_result.tool_trace or loop_result.exit_reason != "assistant_text":
            return None
        text = (raw_text or user_text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if self._looks_like_delivery_request(text=text, lowered=lowered):
            return "deliver"
        if self._looks_like_delete_request(text=text, lowered=lowered):
            return "delete"
        if self._looks_like_user_memory_request(text=text, lowered=lowered):
            return "user_memory"
        if upload is not None and self.media_kind_resolver(upload) == "image":
            return "save_file"
        return None

    @staticmethod
    def tool_retry_instruction(category: str) -> str:
        if category == "deliver":
            return (
                "Tool-use correction: the user is asking for a previously saved photo or file to be sent back over the current channel. "
                "Do not claim file delivery is unsupported. Inspect the relevant skill with skill_view if needed, then use the appropriate tool workflow. "
                "If the target is ambiguous, let the tool-backed workflow return a clarification instead of answering from chat."
            )
        if category == "delete":
            return (
                "Tool-use correction: the user is asking to delete saved content. "
                "Do not answer with plain chat. Inspect the relevant skill if needed and use the proper tool-backed delete workflow."
            )
        if category == "user_memory":
            return (
                "Tool-use correction: the user is asking to remember, inspect, update, or forget durable personal information. "
                "Use the user_memory tool instead of replying from chat."
            )
        if category == "save_file":
            return (
                "Tool-use correction: the user uploaded a file or image that should be handled through a tool-backed workflow. "
                "Inspect the relevant skill if needed and use the proper save workflow."
            )
        return (
            "Tool-use correction: this request should be handled with tools instead of plain chat. "
            "Use the relevant skill workflow and tool support before answering."
        )

    def loop_result_to_turn_result(self, result: LoopResult) -> AgentTurnResult:
        runtime_context = self.runtime_manager.runtime_to_context(result.runtime)
        tool_trace = [self._tool_summary_from_loop_trace(entry) for entry in result.tool_trace]
        if not result.tool_trace:
            reply = result.final_response or "我暂时还不能理解这个请求，你可以换一种说法试试。"
            reason = "The model responded directly." if result.exit_reason == "assistant_text" else "Tool-calling loop produced no final answer."
            confidence = "medium" if result.exit_reason == "assistant_text" else "low"
        else:
            primary_tool = result.tool_trace[-1]
            reply = self.select_final_agent_reply(
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
        return AgentTurnResult(
            reply=reply,
            status=result.status,
            disposition=result.disposition,
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
    def _tool_summary_from_loop_trace(entry: ToolExecutionTrace) -> ToolExecutionSummary:
        return ToolExecutionSummary(
            tool_name=entry.tool_name,
            arguments=dict(entry.arguments),
            action=entry.action,
            status=entry.status,
            disposition=entry.disposition,
            artifacts=list(entry.artifacts),
            metadata=dict(entry.metadata),
        )

    @staticmethod
    def select_final_agent_reply(*, last_execution: ToolExecutionTrace, assistant_text: str | None) -> str:
        final_reply = (assistant_text or "").strip()
        if not final_reply:
            return last_execution.content
        if AgentTurnRunner._should_prefer_tool_reply(last_execution=last_execution):
            return last_execution.content
        tool_name = last_execution.tool_name
        tool_arguments = last_execution.arguments
        if tool_name == "skill_run":
            skill_input = tool_arguments.get("input") or {}
            if str(skill_input.get("intent") or "").strip() == "deliver" and last_execution.action != "retrieve":
                return last_execution.content
        return final_reply

    @staticmethod
    def _should_prefer_tool_reply(*, last_execution: ToolExecutionTrace) -> bool:
        if last_execution.tool_name != "skill_run":
            return False
        metadata = last_execution.metadata
        if str(metadata.get("skill_name") or "").strip() != "archive-core":
            return False
        content = (last_execution.content or "").strip()
        if not content:
            return False
        # When archive-core already returned a concrete "not found" retrieval result,
        # keep that tool-authored reply instead of letting the model embellish it.
        if "没有找到" in content and not last_execution.artifacts:
            return True
        return False

    @staticmethod
    def _looks_like_delivery_request(*, text: str, lowered: str) -> bool:
        delivery_verbs = ("发我", "发给我", "发送", "传给我", "回传", "转发", "send me", "send back", "deliver")
        file_nouns = ("照片", "图片", "原图", "文件", "附件", "pdf", "jpg", "jpeg", "png", "文档", "file", "photo", "image")
        return any(token in text for token in delivery_verbs) and any(token in lowered for token in file_nouns)

    @staticmethod
    def _looks_like_delete_request(*, text: str, lowered: str) -> bool:
        delete_verbs = ("删除", "删掉", "移除", "清掉", "remove", "delete")
        target_nouns = ("资料", "文件", "图片", "照片", "附件", "第", "序号", "item", "file", "photo", "image")
        return any(token in text for token in delete_verbs) and any(token in text or token in lowered for token in target_nouns)

    @staticmethod
    def _looks_like_user_memory_request(*, text: str, lowered: str) -> bool:
        memory_markers = (
            "记住", "记一下", "记下来", "忘记", "删掉这条记忆", "删除记忆", "查看记忆", "个人记忆",
            "remember this", "forget this", "show my memory", "user memory",
        )
        return any(token in text or token in lowered for token in memory_markers)

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
