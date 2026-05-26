#!/usr/bin/env python
"""Stub MCP server for testing.

This is a minimal MCP server implementation that provides a few simple tools
for testing the MCP client implementation.
"""

import json
import sys
from typing import Any


def send_response(response: dict[str, Any]) -> None:
    """Send a JSON-RPC response to stdout."""
    message = json.dumps(response)
    print(message, flush=True)


def handle_initialize(request_id: int, params: dict[str, Any]) -> None:
    """Handle initialize request."""
    send_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "stub-mcp-server",
                "version": "0.1.0"
            }
        }
    })


def handle_tools_list(request_id: int) -> None:
    """Handle tools/list request."""
    send_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo back the input text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "Text to echo"
                            }
                        },
                        "required": ["text"]
                    }
                },
                {
                    "name": "add",
                    "description": "Add two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {
                                "type": "number",
                                "description": "First number"
                            },
                            "b": {
                                "type": "number",
                                "description": "Second number"
                            }
                        },
                        "required": ["a", "b"]
                    }
                },
                {
                    "name": "error",
                    "description": "Always returns an error (for testing error handling)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }
    })


def handle_tools_call(request_id: int, params: dict[str, Any]) -> None:
    """Handle tools/call request."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    
    if tool_name == "echo":
        text = arguments.get("text", "")
        send_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        })
    
    elif tool_name == "add":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        result = a + b
        send_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": str(result)
                    }
                ]
            }
        })
    
    elif tool_name == "error":
        send_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": "Intentional error for testing"
            }
        })
    
    else:
        send_response({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Tool not found: {tool_name}"
            }
        })


def main() -> None:
    """Main server loop."""
    # Read requests from stdin line by line
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params", {})
            
            if method == "initialize":
                handle_initialize(request_id, params)
            
            elif method == "notifications/initialized":
                # No response needed for notifications
                pass
            
            elif method == "tools/list":
                handle_tools_list(request_id)
            
            elif method == "tools/call":
                handle_tools_call(request_id, params)
            
            else:
                if request_id is not None:
                    send_response({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    })
        
        except json.JSONDecodeError:
            # Invalid JSON, skip
            pass
        except Exception as exc:
            # Send error response if we have a request ID
            if "request_id" in locals() and request_id is not None:
                send_response({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {exc}"
                    }
                })


if __name__ == "__main__":
    main()
