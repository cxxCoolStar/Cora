from __future__ import annotations

import json
import uuid

from core.llm.base import ModelClient
from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolCall, ToolSpec


class DevelopmentModelClient(ModelClient):
    """Simple local model stub for early development and manual testing."""

    @staticmethod
    def _tool_reply_text(content: str) -> str:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(payload, dict):
            reply = payload.get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply
        return content

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None

        if latest_tool is not None:
            tool_name = latest_tool.name or "tool"
            return ModelResponse(assistant_text=f"Tool `{tool_name}` returned: {self._tool_reply_text(latest_tool.content)}")

        if latest_user is None:
            return ModelResponse(assistant_text="How can I help?")

        text = latest_user.content.strip()
        if text.startswith("/plan") or "[Planner mode]" in text:
            goal_line = text.split("\n", 1)[0].strip()
            return ModelResponse(assistant_text=self._planner_plan_json(f"/plan {goal_line}"))

        if text.startswith("/tool "):
            _, _, remainder = text.partition("/tool ")
            name, _, payload = remainder.partition(" ")
            arguments = {}
            if payload.strip():
                arguments = json.loads(payload)
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=str(uuid.uuid4()),
                        tool_name=name,
                        arguments=arguments,
                    )
                ]
            )

        return ModelResponse(assistant_text=f"You said: {text}")

    @classmethod
    def _planner_plan_json(cls, user_text: str) -> str:
        import os

        goal = user_text[5:].strip() if user_text.startswith("/plan") else user_text
        goal = goal or "Execute the requested work."
        stub_mode = str(os.environ.get("CORA_EVAL_PLANNER_STUB") or "valid").strip().lower()
        if stub_mode == "invalid":
            payload = {
                "plan_id": "plan-dev-invalid",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "Broken step",
                        "tool_names": ["not_a_registered_tool"],
                        "instruction": "This tool does not exist.",
                    }
                ],
            }
        else:
            payload = {
                "plan_id": "plan-dev-valid",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "List scheduled tasks",
                        "tool_names": ["scheduled_tasks"],
                        "instruction": "List all scheduled tasks.",
                    }
                ],
            }
        return json.dumps(payload, ensure_ascii=False)
