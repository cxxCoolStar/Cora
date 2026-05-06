from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.agent.context_budget import ContextBudgetManager
from core.agent.runtime_state import ConversationRuntimeState
from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.tool import ToolCall, ToolResult, ToolSpec


class AgentToolExecutor(Protocol):
    async def execute(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        runtime: ConversationRuntimeState,
    ) -> ToolResult:
        ...


@dataclass(slots=True)
class LoopResult:
    final_response: str
    trace: list[Message]
    runtime: ConversationRuntimeState
    exit_reason: str
    steps: int
    action: str = "chat"
    item_id: str | None = None
    needs_clarification: bool = False
    tool_name: str = "none"
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    last_tool_reply: str | None = None
    assistant_text: str | None = None


@dataclass(slots=True)
class AgentLoop:
    model_client: ModelClient
    tool_executor: AgentToolExecutor
    tool_specs: list[ToolSpec]
    context_budget_manager: ContextBudgetManager | None = None
    max_steps: int = 6

    async def run(
        self,
        *,
        session_id: str,
        initial_messages: list[Message],
        runtime: ConversationRuntimeState,
    ) -> LoopResult:
        messages = list(initial_messages)
        trace: list[Message] = []
        last_tool_name = "none"
        last_tool_arguments: dict[str, Any] = {}
        last_action = "chat"
        last_item_id: str | None = None
        last_needs_clarification = False
        last_tool_reply: str | None = None

        for step in range(self.max_steps):
            estimated_prompt_tokens = None
            if self.context_budget_manager is not None:
                estimated_prompt_tokens = self.context_budget_manager.estimate_prompt_tokens(
                    messages=messages,
                    tools=self.tool_specs,
                    calibrated=False,
                )
            response = self.model_client.generate(messages=messages, tools=self.tool_specs)
            self._record_prompt_usage(
                response_usage=response.usage,
                estimated_prompt_tokens=estimated_prompt_tokens,
            )
            if not response.tool_calls:
                final_response = (response.assistant_text or "").strip()
                assistant_message = Message.assistant(
                    session_id=session_id,
                    content=final_response or "I do not have a final answer yet.",
                )
                messages.append(assistant_message)
                trace.append(assistant_message)
                return LoopResult(
                    final_response=assistant_message.content,
                    trace=trace,
                    runtime=runtime,
                    exit_reason="assistant_text",
                    steps=step + 1,
                    action=last_action,
                    item_id=last_item_id,
                    needs_clarification=last_needs_clarification,
                    tool_name=last_tool_name,
                    tool_arguments=dict(last_tool_arguments),
                    last_tool_reply=last_tool_reply,
                    assistant_text=response.assistant_text,
                )

            assistant_tool_message = Message.assistant_tool_calls(
                session_id=session_id,
                content=(response.assistant_text or "").strip(),
                tool_calls=[self._tool_call_payload(tool_call) for tool_call in response.tool_calls],
                metadata={"turn_type": "tool_calls", "step": step},
            )
            messages.append(assistant_tool_message)
            trace.append(assistant_tool_message)

            for tool_call in response.tool_calls:
                result = await self.tool_executor.execute(
                    session_id=session_id,
                    tool_call=tool_call,
                    runtime=runtime,
                )
                last_tool_name = tool_call.tool_name
                last_tool_arguments = dict(tool_call.arguments)
                last_action = str(result.metadata.get("action") or "chat")
                last_item_id = result.metadata.get("item_id")
                last_needs_clarification = bool(result.metadata.get("needs_clarification"))
                last_tool_reply = result.content
                next_runtime = result.metadata.get("runtime_state")
                if isinstance(next_runtime, ConversationRuntimeState):
                    runtime = next_runtime
                tool_message = Message.tool(
                    session_id=session_id,
                    name=tool_call.tool_name,
                    tool_call_id=tool_call.id,
                    content=self._tool_result_content(result=result),
                    metadata=result.metadata,
                )
                messages.append(tool_message)
                trace.append(tool_message)
                if last_needs_clarification:
                    return LoopResult(
                        final_response=result.content,
                        trace=trace,
                        runtime=runtime,
                        exit_reason="needs_clarification",
                        steps=step + 1,
                        action=last_action,
                        item_id=last_item_id,
                        needs_clarification=True,
                        tool_name=last_tool_name,
                        tool_arguments=dict(last_tool_arguments),
                        last_tool_reply=last_tool_reply,
                    )

        fallback = Message.assistant(
            session_id=session_id,
            content="I reached the maximum number of tool steps before finishing.",
        )
        messages.append(fallback)
        trace.append(fallback)
        return LoopResult(
            final_response=fallback.content,
            trace=trace,
            runtime=runtime,
            exit_reason="max_steps",
            steps=self.max_steps,
            action=last_action,
            item_id=last_item_id,
            needs_clarification=last_needs_clarification,
            tool_name=last_tool_name,
            tool_arguments=dict(last_tool_arguments),
            last_tool_reply=last_tool_reply,
        )

    @staticmethod
    def _tool_call_payload(tool_call: ToolCall) -> dict[str, Any]:
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.tool_name,
                "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
            },
        }

    def _record_prompt_usage(
        self,
        *,
        response_usage: dict[str, Any],
        estimated_prompt_tokens: int | None,
    ) -> None:
        if self.context_budget_manager is None or estimated_prompt_tokens is None:
            return
        try:
            actual_prompt_tokens = int(response_usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError, AttributeError):
            return
        if actual_prompt_tokens <= 0:
            return
        self.context_budget_manager.observe_prompt_usage(
            estimated_prompt_tokens=estimated_prompt_tokens,
            actual_prompt_tokens=actual_prompt_tokens,
        )

    @staticmethod
    def _tool_result_content(*, result: ToolResult) -> str:
        payload = {
            "success": result.success,
            "content": result.content,
            "metadata": {
                key: value
                for key, value in result.metadata.items()
                if key != "runtime_state"
            },
        }
        if result.error:
            payload["error"] = result.error
        return json.dumps(payload, ensure_ascii=False)
