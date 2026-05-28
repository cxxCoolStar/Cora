#!/usr/bin/env python3
"""Minimal stdio MCP server for Cora local testing (echo + add tools).

Run from repo root:
  python examples/mcp_servers/echo_server.py

Wire in config/mcp_servers.json:
  {"name": "local", "command": "python", "args": ["examples/mcp_servers/echo_server.py"], ...}
"""

from __future__ import annotations

import json
import sys
from typing import Any


TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back to the caller.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    },
]


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _handle_initialize(request_id: int) -> None:
    _write(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cora-echo-server", "version": "0.1.0"},
            },
        }
    )


def _handle_tools_list(request_id: int) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})


def _handle_tools_call(request_id: int, params: dict[str, Any]) -> None:
    name = str(params.get("name") or "")
    arguments = dict(params.get("arguments") or {})
    if name == "echo":
        text = str(arguments.get("text") or "")
        content = [{"type": "text", "text": text}]
    elif name == "add":
        total = int(arguments.get("a", 0)) + int(arguments.get("b", 0))
        content = [{"type": "text", "text": str(total)}]
    else:
        _write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }
        )
        return
    _write({"jsonrpc": "2.0", "id": request_id, "result": {"content": content, "isError": False}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = str(payload.get("method") or "")
        request_id = payload.get("id")
        params = dict(payload.get("params") or {})
        if method == "initialize" and request_id is not None:
            _handle_initialize(int(request_id))
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list" and request_id is not None:
            _handle_tools_list(int(request_id))
        elif method == "tools/call" and request_id is not None:
            _handle_tools_call(int(request_id), params)


if __name__ == "__main__":
    main()
