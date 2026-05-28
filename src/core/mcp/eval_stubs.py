from __future__ import annotations

from pathlib import Path
from typing import Any

from core.tools.registry import ToolRegistry, ToolSpec

_EVAL_MCP_STUBS_REGISTERED = False


def register_eval_mcp_stub_tools(registry: ToolRegistry) -> None:
    """Register in-process MCP stub tools for harness evals (no subprocess server)."""
    global _EVAL_MCP_STUBS_REGISTERED
    if _EVAL_MCP_STUBS_REGISTERED:
        return
    _EVAL_MCP_STUBS_REGISTERED = True

    async def _mcp_test_write(executor: Any, invocation: Any):
        from core.clawbot.tools import ToolExecutionResult
        from core.config import CoreSettings

        arguments = dict(invocation.plan.arguments or {})
        relative_path = str(arguments.get("file_path") or "mcp-checkpoint.txt").strip()
        content = str(arguments.get("content") or "mcp-checkpoint\n")
        context = dict(invocation.context or {})
        sandbox_root = str(context.get("sandbox_workspace_root") or "").strip()
        root = Path(sandbox_root) if sandbox_root else CoreSettings().file_tool_root
        target = (Path(root) / relative_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolExecutionResult(
            reply=f"Wrote MCP checkpoint file: {relative_path}",
            action="tool_completed",
            status="completed",
            disposition="respond",
            metadata={"mcp_tool": "mcp_test_write", "file_path": relative_path},
        )

    async def _mcp_example_echo(executor: Any, invocation: Any):
        from core.clawbot.tools import ToolExecutionResult

        arguments = dict(invocation.plan.arguments or {})
        text = str(arguments.get("text") or "")
        tool = invocation.plan.tool
        return ToolExecutionResult(
            reply=f"MCP echo ({tool}): {text}",
            action="tool_completed",
            status="completed",
            disposition="respond",
            metadata={"mcp_tool": tool, "text": text},
        )

    async def _mcp_example_add(executor: Any, invocation: Any):
        from core.clawbot.tools import ToolExecutionResult

        arguments = dict(invocation.plan.arguments or {})
        total = int(arguments.get("a", 0)) + int(arguments.get("b", 0))
        tool = invocation.plan.tool
        return ToolExecutionResult(
            reply=f"MCP add ({tool}): {total}",
            action="tool_completed",
            status="completed",
            disposition="respond",
            metadata={"mcp_tool": tool, "sum": total},
        )

    registry.register(
        ToolSpec(
            name="mcp_test_write",
            toolset="mcp_test",
            description="Eval stub: write a workspace file (mutating MCP stand-in).",
            schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            handler=_mcp_test_write,
            is_agent_stateful=False,
            read_only=False,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="mcp_example_echo",
            toolset="mcp_example",
            description="Eval stub: echo text (MCP discovery stand-in).",
            schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=_mcp_example_echo,
            is_agent_stateful=False,
            read_only=True,
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="mcp_example_add",
            toolset="mcp_example",
            description="Eval stub: add two integers (MCP discovery stand-in).",
            schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            handler=_mcp_example_add,
            is_agent_stateful=False,
            read_only=True,
            risk="low",
        )
    )


__all__ = ["register_eval_mcp_stub_tools"]
