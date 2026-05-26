# Phase 6：MCP 工具集成（PR-6）

> 状态：🚧 进行中（6a/6b/6c 已完成，6d/6e 待做）  
> 前置：Phase 5（checkpoint、idempotency、retry、replay）

## 1. 目标

集成 **Model Context Protocol (MCP)**，让 Cora 能够动态加载和使用外部工具服务器提供的工具，扩展 agent 的能力边界。

### 问题场景

```
当前 Cora 只支持内置工具（read_file, write_file, search_files 等）

用户想要：
- 查询数据库（需要 SQL 工具）
- 调用云服务 API（需要 AWS/Azure 工具）
- 访问企业内部系统（需要自定义工具）

但是：
- 每个新工具都需要修改 Cora 核心代码
- 无法动态加载第三方工具
- 工具生态封闭，扩展性差
```

### 解决方案

通过 **MCP 协议** 连接外部工具服务器：
- **动态发现**：启动时自动发现 MCP 服务器提供的工具
- **统一接口**：MCP 工具与内置工具使用相同的 policy/HITL 机制
- **可扩展**：用户可以添加自己的 MCP 服务器，无需修改 Cora 代码
- **安全治理**：MCP 工具受 tool policy 约束，支持 HITL 确认

---

## 2. 核心概念

### 2.1 什么是 MCP？

**Model Context Protocol (MCP)** 是一个开放协议，用于 LLM 应用与外部工具/数据源的标准化通信。

**核心组件：**
- **MCP Server**：提供工具的服务进程（如 database-mcp-server、aws-mcp-server）
- **MCP Client**：连接并调用 MCP Server 的客户端（Cora 扮演此角色）
- **Tool Schema**：工具的 JSON Schema 定义（参数、返回值）
- **Transport**：通信方式（stdio、HTTP、WebSocket）

**MCP 工具示例：**
```json
{
  "name": "query_database",
  "description": "Execute SQL query on the database",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "SQL query to execute"},
      "database": {"type": "string", "description": "Database name"}
    },
    "required": ["query"]
  }
}
```

### 2.2 MCP 在 Cora 中的位置

```
┌─────────────────────────────────────────────────────────────┐
│                         Cora Agent                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Tool Policy Engine                      │   │
│  │  (allow/deny/ask, HITL, sandbox, idempotency)       │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                  │
│  ┌────────────────────────┴──────────────────────────────┐  │
│  │           Tool Registry & Dispatcher                  │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │  │
│  │  │ Built-in     │  │ MCP Tools    │  │ Future     │  │  │
│  │  │ Tools        │  │ (Dynamic)    │  │ Extensions │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                           │                                  │
│  ┌────────────────────────┴──────────────────────────────┐  │
│  │              MCP Client Manager                       │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │  │
│  │  │ Server 1     │  │ Server 2     │  │ Server N   │  │  │
│  │  │ (database)   │  │ (aws)        │  │ (custom)   │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   External MCP Servers │
              │  (stdio/HTTP/WebSocket)│
              └────────────────────────┘
```


### 2.3 MCP 工具 vs 内置工具

| 特性 | 内置工具 | MCP 工具 |
|------|----------|----------|
| **定义位置** | Cora 代码库 | 外部 MCP Server |
| **加载方式** | 静态（启动时） | 动态（连接 Server 时） |
| **扩展性** | 需修改代码 | 配置文件添加 Server |
| **Policy 治理** | ✅ 支持 | ✅ 支持（统一接口） |
| **HITL 确认** | ✅ 支持 | ✅ 支持（统一接口） |
| **Idempotency** | ✅ 支持 | ✅ 支持（需 Server 配合） |
| **Retry** | ✅ 支持 | ✅ 支持（统一错误处理） |
| **Sandbox** | ✅ 支持 | ⚠️ 取决于 Server 实现 |

### 2.4 MCP Server 配置示例

```json
{
  "mcp_servers": {
    "database": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres"],
      "env": {
        "POSTGRES_CONNECTION_STRING": "postgresql://user:pass@localhost/db"
      },
      "transport": "stdio",
      "enabled": true
    },
    "aws": {
      "command": "python",
      "args": ["-m", "mcp_server_aws"],
      "env": {
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "default"
      },
      "transport": "stdio",
      "enabled": true
    },
    "custom": {
      "url": "http://localhost:8080/mcp",
      "transport": "http",
      "enabled": false
    }
  }
}
```

---

## 3. 数据模型

### 3.1 MCP Server 配置

```python
# src/core/mcp/config.py

from dataclasses import dataclass, field
from typing import Literal

@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    name: str
    """Server 名称（唯一标识）"""
    
    transport: Literal["stdio", "http", "websocket"]
    """传输方式"""
    
    enabled: bool = True
    """是否启用"""
    
    # stdio transport
    command: str | None = None
    """启动命令（stdio）"""
    
    args: list[str] = field(default_factory=list)
    """命令参数（stdio）"""
    
    env: dict[str, str] = field(default_factory=dict)
    """环境变量（stdio）"""
    
    # HTTP/WebSocket transport
    url: str | None = None
    """Server URL（http/websocket）"""
    
    headers: dict[str, str] = field(default_factory=dict)
    """HTTP headers（http/websocket）"""
    
    # Connection settings
    timeout: float = 30.0
    """连接超时（秒）"""
    
    retry_on_failure: bool = True
    """连接失败时是否重试"""
    
    max_retries: int = 3
    """最大重试次数"""
```


### 3.2 MCP Tool Schema

```python
# src/core/mcp/schema.py

from dataclasses import dataclass
from typing import Any

@dataclass
class MCPToolSchema:
    """MCP 工具定义"""
    name: str
    """工具名称"""
    
    description: str
    """工具描述"""
    
    input_schema: dict[str, Any]
    """输入参数 JSON Schema"""
    
    server_name: str
    """所属 MCP Server 名称"""
    
    is_mutating: bool = False
    """是否为 mutating 操作（用于 idempotency）"""
    
    idempotency_key_extractor: str | None = None
    """幂等键提取表达式（如 "args.file_path"）"""
    
    def to_tool_spec(self) -> dict[str, Any]:
        """转换为 LLM 可用的 tool spec"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            }
        }
```

### 3.3 MCP Tool Execution Result

```python
# src/core/mcp/execution.py

from dataclasses import dataclass
from typing import Any

@dataclass
class MCPToolResult:
    """MCP 工具执行结果"""
    content: list[dict[str, Any]]
    """结果内容（MCP 协议格式）"""
    
    is_error: bool = False
    """是否为错误"""
    
    error_code: str | None = None
    """错误代码"""
    
    error_message: str | None = None
    """错误消息"""
    
    metadata: dict[str, Any] | None = None
    """额外元数据"""
    
    def to_text(self) -> str:
        """转换为文本格式（给 LLM）"""
        if self.is_error:
            return f"Error: {self.error_message}"
        
        # 合并所有 content 的 text 字段
        texts = []
        for item in self.content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        
        return "\n".join(texts)
```

---

## 4. 实现路径

### 4.1 MCP Client 基础设施

```python
# src/core/mcp/client.py

import asyncio
import json
from typing import Any

class MCPClient:
    """MCP 客户端（单个 Server 连接）"""
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.tools: dict[str, MCPToolSchema] = {}
        self.connected = False
    
    async def connect(self) -> None:
        """连接到 MCP Server"""
        if self.config.transport == "stdio":
            await self._connect_stdio()
        elif self.config.transport == "http":
            await self._connect_http()
        else:
            raise ValueError(f"Unsupported transport: {self.config.transport}")
    
    async def _connect_stdio(self) -> None:
        """通过 stdio 连接"""
        self.process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self.config.env},
        )
        
        # 发送 initialize 请求
        await self._send_request({
            "jsonrpc": "2.0",
            "id": 1,
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
        
        # 接收 initialize 响应
        response = await self._receive_response()
        
        # 发送 initialized 通知
        await self._send_notification({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        
        # 列出可用工具
        await self._list_tools()
        
        self.connected = True
```


    async def _list_tools(self) -> None:
        """列出 Server 提供的工具"""
        await self._send_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        })
        
        response = await self._receive_response()
        tools_data = response.get("result", {}).get("tools", [])
        
        for tool_data in tools_data:
            tool = MCPToolSchema(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self.tools[tool.name] = tool
    
    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """调用工具"""
        if tool_name not in self.tools:
            raise ValueError(f"Tool {tool_name} not found in server {self.config.name}")
        
        await self._send_request({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        })
        
        response = await self._receive_response()
        
        if "error" in response:
            return MCPToolResult(
                content=[],
                is_error=True,
                error_code=response["error"].get("code"),
                error_message=response["error"].get("message"),
            )
        
        result_data = response.get("result", {})
        return MCPToolResult(
            content=result_data.get("content", []),
            is_error=result_data.get("isError", False),
            metadata=result_data.get("_meta"),
        )
    
    async def disconnect(self) -> None:
        """断开连接"""
        if self.process:
            self.process.terminate()
            await self.process.wait()
        self.connected = False
    
    async def _send_request(self, request: dict[str, Any]) -> None:
        """发送 JSON-RPC 请求"""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Not connected")
        
        message = json.dumps(request) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()
    
    async def _send_notification(self, notification: dict[str, Any]) -> None:
        """发送 JSON-RPC 通知（无需响应）"""
        await self._send_request(notification)
    
    async def _receive_response(self) -> dict[str, Any]:
        """接收 JSON-RPC 响应"""
        if not self.process or not self.process.stdout:
            raise RuntimeError("Not connected")
        
        line = await self.process.stdout.readline()
        return json.loads(line.decode())
    
    def _next_id(self) -> int:
        """生成下一个请求 ID"""
        if not hasattr(self, "_request_id"):
            self._request_id = 2
        self._request_id += 1
        return self._request_id
```


### 4.2 MCP Client Manager

```python
# src/core/mcp/manager.py

from typing import Any

class MCPClientManager:
    """管理多个 MCP Client 连接"""
    
    def __init__(self, configs: list[MCPServerConfig]):
        self.configs = configs
        self.clients: dict[str, MCPClient] = {}
        self.all_tools: dict[str, MCPToolSchema] = {}
    
    async def connect_all(self) -> None:
        """连接所有启用的 MCP Server"""
        for config in self.configs:
            if not config.enabled:
                continue
            
            try:
                client = MCPClient(config)
                await client.connect()
                self.clients[config.name] = client
                
                # 合并工具列表
                for tool_name, tool_schema in client.tools.items():
                    # 工具名称加前缀避免冲突：mcp_{server_name}_{tool_name}
                    prefixed_name = f"mcp_{config.name}_{tool_name}"
                    self.all_tools[prefixed_name] = tool_schema
                
                logger.info(f"Connected to MCP server {config.name}, loaded {len(client.tools)} tools")
            
            except Exception as exc:
                logger.error(f"Failed to connect to MCP server {config.name}: {exc}")
                if not config.retry_on_failure:
                    raise
    
    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """调用 MCP 工具"""
        # 解析工具名称：mcp_{server_name}_{tool_name}
        if not tool_name.startswith("mcp_"):
            raise ValueError(f"Not an MCP tool: {tool_name}")
        
        parts = tool_name.split("_", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid MCP tool name: {tool_name}")
        
        server_name = parts[1]
        original_tool_name = parts[2]
        
        client = self.clients.get(server_name)
        if not client:
            raise ValueError(f"MCP server {server_name} not connected")
        
        return await client.call_tool(
            tool_name=original_tool_name,
            arguments=arguments,
        )
    
    def get_all_tool_specs(self) -> list[dict[str, Any]]:
        """获取所有 MCP 工具的 spec（给 LLM）"""
        return [tool.to_tool_spec() for tool in self.all_tools.values()]
    
    async def disconnect_all(self) -> None:
        """断开所有连接"""
        for client in self.clients.values():
            await client.disconnect()
        self.clients.clear()
        self.all_tools.clear()
```

### 4.3 Tool Registry 集成

```python
# src/core/tools/registry.py

class ToolRegistry:
    """工具注册表（内置 + MCP）"""
    
    def __init__(self, mcp_manager: MCPClientManager | None = None):
        self.builtin_tools = self._load_builtin_tools()
        self.mcp_manager = mcp_manager
    
    def get_all_tools(self) -> list[dict[str, Any]]:
        """获取所有可用工具（内置 + MCP）"""
        tools = list(self.builtin_tools)
        
        if self.mcp_manager:
            tools.extend(self.mcp_manager.get_all_tool_specs())
        
        return tools
    
    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断是否为 MCP 工具"""
        return tool_name.startswith("mcp_")
    
    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        """执行工具（内置或 MCP）"""
        if self.is_mcp_tool(tool_name):
            return await self._execute_mcp_tool(
                tool_name=tool_name,
                arguments=arguments,
                context=context,
            )
        else:
            return await self._execute_builtin_tool(
                tool_name=tool_name,
                arguments=arguments,
                context=context,
            )
    
    async def _execute_mcp_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> ToolExecutionResult:
        """执行 MCP 工具"""
        if not self.mcp_manager:
            raise RuntimeError("MCP manager not initialized")
        
        try:
            result = await self.mcp_manager.call_tool(
                tool_name=tool_name,
                arguments=arguments,
            )
            
            if result.is_error:
                return ToolExecutionResult(
                    reply=result.error_message or "MCP tool execution failed",
                    action="tool_failed",
                    status="failed",
                    disposition="respond",
                    metadata={"error_code": result.error_code},
                )
            
            return ToolExecutionResult(
                reply=result.to_text(),
                action="tool_completed",
                status="completed",
                disposition="respond",
                metadata=result.metadata or {},
            )
        
        except Exception as exc:
            return ToolExecutionResult(
                reply=f"MCP tool execution error: {exc}",
                action="tool_failed",
                status="failed",
                disposition="respond",
            )
```


### 4.4 Tool Policy 集成

MCP 工具与内置工具使用**相同的 policy 机制**：

```python
# src/core/tools/policy.py

class ToolPolicyEngine:
    """工具策略引擎（支持 MCP 工具）"""
    
    def check_tool_allowed(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        policy_profile: str,
    ) -> ToolPolicyDecision:
        """检查工具是否允许执行"""
        # MCP 工具也受 policy 约束
        if tool_name.startswith("mcp_"):
            # 可以配置 MCP 工具的 policy
            # 例如：mcp_database_* 需要 HITL 确认
            return self._check_mcp_tool_policy(
                tool_name=tool_name,
                arguments=arguments,
                policy_profile=policy_profile,
            )
        
        # 内置工具的 policy 检查
        return self._check_builtin_tool_policy(
            tool_name=tool_name,
            arguments=arguments,
            policy_profile=policy_profile,
        )
    
    def _check_mcp_tool_policy(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        policy_profile: str,
    ) -> ToolPolicyDecision:
        """检查 MCP 工具的 policy"""
        # 示例：所有 MCP 工具默认需要 HITL 确认
        if policy_profile == "wechat_safe":
            return ToolPolicyDecision(
                action="ask",
                reason=f"MCP tool {tool_name} requires confirmation",
            )
        
        # 其他 profile 可以配置不同的策略
        return ToolPolicyDecision(action="allow")
```

**Policy 配置示例：**

```yaml
# config/tool_policies.yaml

profiles:
  wechat_safe:
    # 内置工具
    read_file: allow
    write_file: ask
    
    # MCP 工具（通配符）
    mcp_database_*: ask
    mcp_aws_*: deny
    mcp_custom_*: allow
  
  default:
    # 所有工具默认允许
    "*": allow
```

---

## 5. Eval 设计

### 5.1 Eval Case: `mcp_tool_discovery_and_execution`

```json
{
  "id": "mcp_tool_discovery_and_execution",
  "type": "harness",
  "description": "Agent should discover and execute MCP tools from connected servers.",
  "tags": ["harness", "mcp", "tool", "discovery"],
  "setup": {
    "mcp_servers": {
      "test": {
        "command": "python",
        "args": ["-m", "tests.mcp_stub_server"],
        "transport": "stdio",
        "enabled": true
      }
    }
  },
  "steps": [
    {
      "label": "agent discovers MCP tools on startup",
      "input": {
        "agent_role": "chat",
        "text": "What tools are available?"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["mcp_test_echo", "mcp_test_add"]
      }
    },
    {
      "label": "agent executes MCP tool successfully",
      "input": {
        "agent_role": "chat",
        "text": "Use mcp_test_echo to echo 'hello world'"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["hello world"],
        "state": {
          "latest_agent_run_trace_contains_all": [
            "tool.started",
            "mcp_test_echo",
            "tool.completed"
          ]
        }
      }
    }
  ]
}
```

### 5.2 Eval Case: `mcp_tool_respects_policy`

```json
{
  "id": "mcp_tool_respects_policy",
  "type": "harness",
  "description": "MCP tools should respect tool policy (allow/deny/ask).",
  "tags": ["harness", "mcp", "policy"],
  "setup": {
    "mcp_servers": {
      "test": {
        "command": "python",
        "args": ["-m", "tests.mcp_stub_server"],
        "transport": "stdio",
        "enabled": true
      }
    },
    "policy_profile": "wechat_safe",
    "tool_policy": {
      "mcp_test_*": "ask"
    }
  },
  "steps": [
    {
      "label": "agent requests HITL for MCP tool",
      "input": {
        "agent_role": "chat",
        "text": "Use mcp_test_echo to echo 'test'"
      },
      "expect": {
        "status": "waiting_hitl",
        "disposition": "clarify",
        "reply_contains_all": ["confirmation required", "mcp_test_echo"]
      }
    }
  ]
}
```


### 5.3 Eval Case: `mcp_tool_idempotency_on_resume`

```json
{
  "id": "mcp_tool_idempotency_on_resume",
  "type": "harness",
  "description": "MCP mutating tools should respect idempotency keys on plan resume.",
  "tags": ["harness", "mcp", "idempotency", "checkpoint"],
  "setup": {
    "mcp_servers": {
      "test": {
        "command": "python",
        "args": ["-m", "tests.mcp_stub_server"],
        "transport": "stdio",
        "enabled": true
      }
    },
    "planner_stub_mode": "two_step",
    "mcp_tool_metadata": {
      "mcp_test_write": {
        "is_mutating": true,
        "idempotency_key_extractor": "args.file_path"
      }
    }
  },
  "steps": [
    {
      "label": "plan execution fails after MCP write",
      "input": {
        "agent_role": "execute",
        "text": "/execute"
      },
      "expect": {
        "status": "failed",
        "disposition": "respond",
        "reply_contains_all": ["execution failed", "/execute resume"]
      }
    },
    {
      "label": "resume skips MCP write via idempotency",
      "input": {
        "agent_role": "execute",
        "text": "/execute resume"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "state": {
          "latest_agent_run_trace_contains_all": [
            "tool_skipped",
            "idempotency_key",
            "mcp_test_write"
          ]
        }
      }
    }
  ]
}
```

---

## 6. 实现切片（小步 PR）

### PR-6a: MCP Client 基础设施 ✅

**目标**：实现 MCP 协议的基础通信层

- [x] `src/core/mcp/config.py`：`MCPServerConfig` 数据模型
- [x] `src/core/mcp/schema.py`：`MCPToolSchema`, `MCPToolResult` 数据模型
- [x] `src/core/mcp/client.py`：`MCPClient` 类（stdio transport）
  - [x] `connect()` 方法：启动 Server 进程
  - [x] `_send_request()` / `_receive_response()`：JSON-RPC 通信
  - [x] `_list_tools()`：列出可用工具
  - [x] `call_tool()`：调用工具
  - [x] `disconnect()`：断开连接
- [x] `src/core/mcp/manager.py`：`MCPClientManager` 类
- [x] 单元测试：`test_mcp_client.py`（8 个测试）
- [x] 单元测试：`test_mcp_manager.py`（7 个测试）
- [x] 集成测试：使用 stub MCP server 测试通信
- [x] Stub server：`tests/mcp/stub_server.py`

**验收标准**：
- ✅ 能成功连接到 stdio MCP server
- ✅ 能列出 server 提供的工具
- ✅ 能调用工具并获取结果
- ✅ 单元测试通过（15/15）
- ✅ 现有 harness eval 全绿（39/39）

### PR-6b: MCP Client Manager 与 Tool Registry 集成 ✅

**目标**：管理多个 MCP server 连接，集成到工具注册表

- [x] `src/core/mcp/manager.py`：`MCPClientManager` 类
  - [x] `connect_all()`：连接所有启用的 server
  - [x] `call_tool()`：路由工具调用到对应 server
  - [x] `get_all_tool_specs()`：获取所有工具 spec
  - [x] `disconnect_all()`：断开所有连接
- [x] `src/core/mcp/config_loader.py`：配置文件加载器
- [x] `src/core/mcp/tool_adapter.py`：MCP 工具适配器
- [x] `src/core/tools/manager.py`：集成 MCP 工具
  - [x] `get_all_tools()`：返回内置 + MCP 工具
  - [x] `is_mcp_tool()`：判断工具类型
  - [x] `execute_tool()`：统一执行接口
- [x] 配置文件：`config/mcp_servers.json`
- [x] 单元测试：`test_mcp_config_loader.py`（6 个测试）
- [x] 单元测试：`test_mcp_tool_adapter.py`（4 个测试）
- [x] 集成测试：`test_mcp_tool_manager_integration.py`（5 个测试）

**验收标准**：
- ✅ 能同时连接多个 MCP server
- ✅ 工具名称不冲突（使用前缀）
- ✅ Tool registry 能返回所有工具
- ✅ MCP 工具与内置工具使用统一接口
- ✅ 单元测试通过（15/15）
- ✅ 现有 harness eval 全绿（39/39）

### PR-6c: Tool Policy 与 HITL 集成 ✅

**目标**：MCP 工具受 policy 约束，支持 HITL 确认

- [x] `src/core/agent/tool_policy_engine.py`：MCP 通配符匹配 + `mcp_default_policy`
- [x] `src/core/agent/policy_profiles.py`：`HarnessPolicyProfile` MCP 字段与各 profile 配置
- [x] MCP 工具绕过 builtin `allowed_tool_names` 限制（走独立 MCP 策略）
- [x] `src/core/mcp/runtime.py` + `ClawBotContainer.connect_mcp_if_enabled()`：gateway 启动时连接 MCP
- [x] 单元测试：`tests/test_mcp_tool_policy.py`
- [x] 集成测试：`tests/test_mcp_tool_policy_integration.py`
- [x] Eval：`evals/cases/harness/mcp_tool_respects_policy.json`

**验收标准**：
- ✅ MCP 工具受 policy 约束
- ✅ 能配置 allow/deny/ask 策略（通配符 + default policy）
- ✅ HITL 确认流程正常工作（`mcp_default_policy_ask`）
- ✅ Harness eval 通过（40/40）

### PR-6d: Idempotency 与 Retry 集成 🚧

**目标**：MCP mutating 工具支持幂等性和重试

- [ ] `src/core/mcp/schema.py`：扩展 `MCPToolSchema`
  - [ ] `is_mutating` 字段
  - [ ] `idempotency_key_extractor` 字段
- [ ] `src/core/agent/idempotency.py`：支持 MCP 工具
  - [ ] `generate_idempotency_key()` 支持 MCP 工具
- [ ] `src/core/agent/retry_policy.py`：支持 MCP 工具
  - [ ] `classify_error()` 识别 MCP 错误
- [ ] 配置文件：`config/mcp_tool_metadata.json`（工具元数据）
- [ ] 单元测试：`test_mcp_tool_idempotency`
- [ ] Eval：`mcp_tool_idempotency_on_resume.json`

**验收标准**：
- MCP mutating 工具生成 idempotency key
- Resume 时跳过已完成的 MCP 操作
- MCP 工具错误能正确分类和重试
- Eval 通过

### PR-6e: Eval 与文档 🚧

**目标**：完善 eval 测试和用户文档

- [ ] Eval：`mcp_tool_discovery_and_execution.json`
- [ ] Eval：`mcp_tool_respects_policy.json`
- [ ] Eval：`mcp_tool_idempotency_on_resume.json`
- [ ] 文档：`docs/mcp-integration-guide.md`（用户指南）
- [ ] 文档：`docs/mcp-server-development.md`（开发自定义 server）
- [ ] 示例：`examples/mcp_servers/`（示例 server 实现）
- [ ] 运行 `.\scripts\run_harness_evals.cmd` 确保全绿

**验收标准**：
- 所有 MCP eval 通过
- 现有 39 个 harness eval 仍然全绿
- 用户文档完整清晰
- 有可运行的示例

---

## 7. 边界情况

### 7.1 MCP Server 连接失败

**场景**：Server 进程启动失败或连接超时

**处理**：
- 记录错误日志
- 如果 `retry_on_failure=true`，重试连接
- 如果重试失败，跳过该 server，继续启动 Cora
- Agent 运行时，MCP 工具不可用，返回友好错误

### 7.2 MCP Server 运行时崩溃

**场景**：Server 进程在运行中崩溃

**处理**：
- 检测到 stdout/stderr 关闭
- 标记 server 为 disconnected
- 后续工具调用返回错误
- 可选：自动重启 server（配置项）

### 7.3 工具名称冲突

**场景**：多个 server 提供同名工具

**处理**：
- 使用前缀：`mcp_{server_name}_{tool_name}`
- 避免冲突
- 在 LLM prompt 中说明工具来源

### 7.4 MCP 工具的 Sandbox

**问题**：MCP server 是外部进程，难以沙箱化

**方案**：
- **选项 A（保守）**：MCP 工具默认不支持 sandbox，policy 设为 `ask`
- **选项 B（激进）**：信任 MCP server 实现自己的沙箱
- **推荐**：选项 A，后续可通过配置切换

### 7.5 MCP 工具的幂等性

**问题**：MCP server 可能不支持幂等性

**方案**：
- 在配置中标记哪些 MCP 工具是 mutating
- 提供 `idempotency_key_extractor` 表达式
- 如果 server 不支持，Cora 侧记录 key 并跳过重复调用

---

## 8. 验收标准

- [ ] 能连接到 stdio MCP server
- [ ] 能列出和调用 MCP 工具
- [ ] MCP 工具与内置工具使用统一接口
- [ ] MCP 工具受 tool policy 约束
- [ ] MCP 工具支持 HITL 确认
- [ ] MCP mutating 工具支持 idempotency
- [ ] MCP 工具错误能正确分类和重试
- [ ] Eval 通过：
  - `mcp_tool_discovery_and_execution` ✅
  - `mcp_tool_respects_policy` ✅
  - `mcp_tool_idempotency_on_resume` ✅
- [ ] 现有 39 个 harness eval 仍然全绿
- [ ] 单元测试覆盖 MCP client、manager、policy 集成
- [ ] 用户文档完整（配置、使用、开发）

---

## 9. 未来扩展

### 9.1 HTTP/WebSocket Transport

当前 PR 只实现 stdio transport，后续可扩展：
- HTTP transport（RESTful API）
- WebSocket transport（双向通信）

### 9.2 MCP Server 健康检查

- 定期 ping server
- 检测 server 崩溃并自动重启
- 监控 server 性能（延迟、错误率）

### 9.3 MCP Tool Caching

- 缓存工具列表（避免每次启动都 list）
- 缓存工具调用结果（对于幂等读操作）

### 9.4 MCP Server Marketplace

- 提供 MCP server 推荐列表
- 一键安装常用 server（database、aws、github 等）

---

## 10. 参考

- [Model Context Protocol Specification](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [cora-phase5b-idempotency-design.md](./cora-phase5b-idempotency-design.md) — Phase 5b 幂等性
- [cora-phase5c-retry-backoff-design.md](./cora-phase5c-retry-backoff-design.md) — Phase 5c 重试机制
- [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) — 架构路线图

