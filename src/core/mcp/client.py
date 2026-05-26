"""MCP Client implementation for stdio transport."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from core.mcp.config import MCPServerConfig
from core.mcp.schema import MCPToolResult, MCPToolSchema

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP client for communicating with a single MCP server.
    
    Implements the Model Context Protocol for tool discovery and execution.
    Currently supports stdio transport only.
    """
    
    def __init__(self, config: MCPServerConfig):
        """Initialize MCP client.
        
        Args:
            config: Server configuration
        """
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.tools: dict[str, MCPToolSchema] = {}
        self.connected = False
        self._request_id = 0
        self._pending_responses: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
    
    async def connect(self) -> None:
        """Connect to the MCP server.
        
        Raises:
            ValueError: If transport is not supported
            RuntimeError: If connection fails
        """
        if self.connected:
            logger.warning(f"MCP client {self.config.name} already connected")
            return
        
        if self.config.transport != "stdio":
            raise ValueError(f"Transport {self.config.transport} not yet supported")
        
        await self._connect_stdio()
        logger.info(f"MCP client {self.config.name} connected, discovered {len(self.tools)} tools")
    
    async def _connect_stdio(self) -> None:
        """Connect via stdio transport."""
        try:
            # Start the server process
            env = {**os.environ, **self.config.env}
            self.process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            
            # Start background reader task
            self._reader_task = asyncio.create_task(self._read_responses())
            
            # Send initialize request
            init_response = await self._send_request({
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "cora",
                        "version": "0.1.0"
                    }
                }
            })
            
            if "error" in init_response:
                raise RuntimeError(f"Initialize failed: {init_response['error']}")
            
            # Send initialized notification
            await self._send_notification({
                "method": "notifications/initialized"
            })
            
            # List available tools
            await self._list_tools()
            
            self.connected = True
        
        except Exception as exc:
            logger.error(f"Failed to connect to MCP server {self.config.name}: {exc}")
            await self.disconnect()
            raise RuntimeError(f"MCP connection failed: {exc}") from exc

    async def _list_tools(self) -> None:
        """List tools provided by the server."""
        response = await self._send_request({
            "method": "tools/list"
        })
        
        if "error" in response:
            logger.error(f"Failed to list tools from {self.config.name}: {response['error']}")
            return
        
        tools_data = response.get("result", {}).get("tools", [])
        
        for tool_data in tools_data:
            tool = MCPToolSchema(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self.tools[tool.name] = tool
        
        logger.debug(f"Loaded {len(self.tools)} tools from {self.config.name}: {list(self.tools.keys())}")
    
    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Call a tool on the MCP server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        
        Raises:
            ValueError: If tool not found
            RuntimeError: If not connected
        """
        if not self.connected:
            raise RuntimeError(f"MCP client {self.config.name} not connected")
        
        if tool_name not in self.tools:
            raise ValueError(f"Tool {tool_name} not found in server {self.config.name}")
        
        response = await self._send_request({
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        })
        
        if "error" in response:
            error = response["error"]
            return MCPToolResult(
                content=[],
                is_error=True,
                error_code=str(error.get("code", "unknown")),
                error_message=error.get("message", "Unknown error"),
            )
        
        result_data = response.get("result", {})
        return MCPToolResult(
            content=result_data.get("content", []),
            is_error=result_data.get("isError", False),
            metadata=result_data.get("_meta"),
        )
    
    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except Exception as exc:
                logger.warning(f"Error disconnecting from {self.config.name}: {exc}")
        
        self.connected = False
        self.tools.clear()
        self._pending_responses.clear()
        logger.info(f"MCP client {self.config.name} disconnected")

    async def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response.
        
        Args:
            request: Request payload (without jsonrpc and id fields)
        
        Returns:
            Response payload
        """
        if not self.process or not self.process.stdin:
            raise RuntimeError("Not connected")
        
        request_id = self._next_request_id()
        full_request = {
            "jsonrpc": "2.0",
            "id": request_id,
            **request
        }
        
        # Create future for response
        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending_responses[request_id] = future
        
        # Send request
        message = json.dumps(full_request) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()
        
        # Wait for response
        try:
            response = await asyncio.wait_for(future, timeout=self.config.timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_responses.pop(request_id, None)
            raise RuntimeError(f"Request {request_id} timed out after {self.config.timeout}s")
    
    async def _send_notification(self, notification: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected).
        
        Args:
            notification: Notification payload (without jsonrpc field)
        """
        if not self.process or not self.process.stdin:
            raise RuntimeError("Not connected")
        
        full_notification = {
            "jsonrpc": "2.0",
            **notification
        }
        
        message = json.dumps(full_notification) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()
    
    async def _read_responses(self) -> None:
        """Background task to read responses from server."""
        if not self.process or not self.process.stdout:
            return
        
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                
                try:
                    response = json.loads(line.decode())
                    
                    # Handle response
                    if "id" in response:
                        request_id = response["id"]
                        future = self._pending_responses.pop(request_id, None)
                        if future and not future.done():
                            future.set_result(response)
                    
                    # Handle notifications (no id field)
                    # Currently we don't handle server-initiated notifications
                
                except json.JSONDecodeError as exc:
                    logger.warning(f"Failed to decode JSON from {self.config.name}: {exc}")
                except Exception as exc:
                    logger.error(f"Error processing response from {self.config.name}: {exc}")
        
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Reader task failed for {self.config.name}: {exc}")
    
    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self._request_id += 1
        return self._request_id
