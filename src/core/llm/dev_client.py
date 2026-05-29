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

    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        response_format: str | None = None,
    ) -> ModelResponse:
        latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None

        if latest_tool is not None:
            tool_name = latest_tool.name or "tool"
            return ModelResponse(assistant_text=f"Tool `{tool_name}` returned: {self._tool_reply_text(latest_tool.content)}")

        if latest_user is None:
            return ModelResponse(assistant_text="How can I help?")

        text = latest_user.content.strip()
        topic_stub = self._topic_classifier_stub_response(content=text)
        if topic_stub is not None:
            return topic_stub
        if text.startswith("/plan") or "[Planner mode]" in text:
            goal_line = text.split("\n", 1)[0].strip()
            return ModelResponse(assistant_text=self._planner_plan_json(f"/plan {goal_line}"))

        if "[Reviewer mode]" in text:
            return ModelResponse(assistant_text=self._reviewer_verdict_json())

        subagent_text_reply = self._subagent_text_reply_from_messages(messages=messages, latest_user=text)
        if subagent_text_reply is not None:
            return ModelResponse(assistant_text=subagent_text_reply)

        worker_tool_call = self._worker_tool_call_from_text(text)
        if worker_tool_call is not None:
            return ModelResponse(tool_calls=[worker_tool_call])

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

    @staticmethod
    def _topic_classifier_stub_response(*, content: str) -> ModelResponse | None:
        if '"existing_topics"' not in content or '"content_preview"' not in content:
            return None
        payload = {
            "topic_name": "Eval Notes",
            "slug": "eval-notes",
            "summary": "Harness eval topic bucket.",
            "tags": ["eval"],
            "reason": "DevelopmentModelClient topic stub.",
        }
        return ModelResponse(assistant_text=json.dumps(payload, ensure_ascii=False))

    @classmethod
    def _planner_plan_json(cls, user_text: str) -> str:
        import os

        goal = user_text[5:].strip() if user_text.startswith("/plan") else user_text
        goal = goal or "Execute the requested work."
        stub_mode = str(os.environ.get("CORA_EVAL_PLANNER_STUB") or "valid").strip().lower()
        if stub_mode in {"hitl", "scheduled_tasks", "scheduled"}:
            payload = {
                "plan_id": "plan-dev-hitl",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "List scheduled tasks",
                        "tool_names": ["scheduled_tasks"],
                        "instruction": "List all scheduled tasks for the user.",
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
        if stub_mode in {"parallel_three_way", "parallel-three-way", "parallel_3"}:
            payload = {
                "plan_id": "plan-dev-parallel-three-way",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-search",
                        "title": "Parallel workspace discovery",
                        "tool_names": [],
                        "instruction": "Discover hello_agent references across the repo before any edits.",
                        "parallel_subagents": [
                            {
                                "instruction": "Find hello_agent under src/.",
                                "tool_names": ["search_files"],
                            },
                            {
                                "instruction": "Search for example modules under src/.",
                                "tool_names": ["search_files"],
                            },
                            {
                                "instruction": "Search for hello_agent mentions under tests/.",
                                "tool_names": ["search_files"],
                            },
                        ],
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
        if stub_mode in {"parallel", "parallel_search", "parallel-search"}:
            payload = {
                "plan_id": "plan-dev-parallel-search",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-search",
                        "title": "Parallel workspace search",
                        "tool_names": [],
                        "instruction": "Search the workspace for hello_agent references.",
                        "parallel_subagents": [
                            {
                                "instruction": "Find hello_agent under src.",
                                "tool_names": ["search_files"],
                            },
                            {
                                "instruction": "Search for example modules under src.",
                                "tool_names": ["search_files"],
                            },
                        ],
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
        if stub_mode in {"two_step", "two-step", "checkpoint"}:
            payload = {
                "plan_id": "plan-dev-two-step",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "Search workspace",
                        "tool_names": ["search_files"],
                        "instruction": "Find hello_agent under src.",
                    },
                    {
                        "task_id": "task-2",
                        "title": "Simulated failure step",
                        "tool_names": ["search_files"],
                        "instruction": "This step is configured to fail in eval stubs.",
                    },
                    {
                        "task_id": "task-3",
                        "title": "Search again after resume",
                        "tool_names": ["search_files"],
                        "instruction": "Find hello_agent under tests.",
                    },
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
        if stub_mode in {"mcp_checkpoint", "mcp-checkpoint"}:
            payload = {
                "plan_id": "plan-dev-mcp-checkpoint",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "title": "MCP checkpoint write",
                        "tool_names": ["mcp_test_write"],
                        "instruction": "Write checkpoint file via MCP stand-in.",
                    },
                    {
                        "task_id": "task-2",
                        "title": "Simulated failure step",
                        "tool_names": ["search_files"],
                        "instruction": "This step is configured to fail in eval stubs.",
                    },
                    {
                        "task_id": "task-3",
                        "title": "Search after resume",
                        "tool_names": ["search_files"],
                        "instruction": "Find hello_agent under tests.",
                    },
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
        if stub_mode in {"high_risk", "write_file", "review"}:
            payload = {
                "plan_id": "plan-dev-high-risk",
                "session_id": "session-dev",
                "goal": goal,
                "policy_profile": "coding_full",
                "tasks": [
                    {
                        "task_id": "task-write",
                        "title": "Write marker file",
                        "tool_names": ["write_file"],
                        "instruction": "Write a marker file under src/.",
                    }
                ],
            }
            return json.dumps(payload, ensure_ascii=False)
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
                        "title": "Search workspace",
                        "tool_names": ["search_files"],
                        "instruction": "Find `hello_agent` in `src`.",
                    }
                ],
            }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _reviewer_verdict_json(cls) -> str:
        import os

        stub_mode = str(os.environ.get("CORA_EVAL_REVIEWER_STUB") or "accept").strip().lower()
        verdict = "accept"
        reason = "Worker output matches the task and is safe to continue."
        if stub_mode in {"abort", "reject", "deny", "fail"}:
            verdict = "abort"
            reason = "Worker output is inconsistent with the plan goal."
        elif stub_mode in {"retry", "again"}:
            verdict = "retry"
            reason = "Worker output is incomplete; one retry may help."
        elif stub_mode in {"ask", "ask_user", "clarify"}:
            verdict = "ask_user"
            reason = "Need user confirmation before continuing."
        payload = {
            "verdict": verdict,
            "reason": reason,
            "confidence": "high",
        }
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _subagent_text_reply_from_messages(
        cls,
        *,
        messages: list[Message],
        latest_user: str,
    ) -> str | None:
        if "[Subagent task]" not in latest_user:
            return None
        lowered = latest_user.lower()
        if "codeword" not in lowered:
            return None
        transcript = "\n".join(
            str(message.content or "")
            for message in messages
            if str(message.role or "") in {"user", "assistant", "system"}
        )
        if "ALPHA" in transcript.upper():
            return "ALPHA"
        return "UNKNOWN"

    @classmethod
    def _worker_tool_call_from_text(cls, text: str) -> ToolCall | None:
        if "[Worker task" not in text and "[Subagent task]" not in text:
            return None
        import os

        fail_task_id = str(os.environ.get("CORA_EVAL_WORKER_FAIL_TASK_ID") or "").strip()
        if fail_task_id and f"[Worker task {fail_task_id}]" in text and "[Plan resume]" not in text:
            return ToolCall(
                id=str(uuid.uuid4()),
                tool_name="write_file",
                arguments={"path": "src/marker.txt", "content": "should not run\n"},
            )
        scope = ""
        for line in text.splitlines():
            if line.strip().lower().startswith("tool scope:"):
                scope = line.split(":", 1)[1].strip()
                break
        if not scope:
            return None
        tool_name = scope.split(",")[0].strip()
        if (
            "[Worker task task-2]" in text
            and "[Plan resume]" not in text
            and str(os.environ.get("CORA_EVAL_PLANNER_STUB") or "").strip().lower()
            in {"two_step", "two-step", "checkpoint", "mcp_checkpoint", "mcp-checkpoint"}
        ):
            return ToolCall(
                id=str(uuid.uuid4()),
                tool_name="write_file",
                arguments={"path": "src/marker.txt", "content": "checkpoint eval failure\n"},
            )
        if not tool_name:
            return None
        arguments: dict = {}
        if tool_name == "search_files":
            lowered = text.lower()
            if "tests/" in lowered or "under tests" in lowered:
                arguments = {"query": "hello_agent", "path": "tests"}
            elif "example" in lowered:
                arguments = {"query": "example", "path": "src"}
            else:
                arguments = {"query": "hello_agent", "path": "src"}
        elif tool_name == "write_file":
            arguments = {"path": "src/marker.txt", "content": "reviewed\n"}
        elif tool_name == "mcp_test_write":
            arguments = {"file_path": "mcp-checkpoint.txt", "content": "mcp idempotency checkpoint\n"}
        elif tool_name == "list_files":
            arguments = {"path": "src", "recursive": False}
        elif tool_name == "read_file":
            arguments = {"path": "src/missing.py"}
        elif tool_name == "scheduled_tasks":
            arguments = {"action": "list"}
        return ToolCall(
            id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=arguments,
        )
