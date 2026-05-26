# PR-6a: MCP Client 基础设施 - 完成总结

> 状态：✅ 已完成  
> 日期：2026-05-25

## 实现内容

### 1. 数据模型

**文件**：`src/core/mcp/config.py`
- `MCPServerConfig`：MCP Server 配置数据模型
  - 支持 stdio、http、websocket transport
  - 包含连接设置（timeout、retry）
  - 提供配置验证方法

**文件**：`src/core/mcp/schema.py`
- `MCPToolSchema`：MCP 工具定义
  - 工具名称、描述、输入 schema
  - 支持转换为 LLM 可用的 tool spec
  - 包含幂等性相关字段（为后续 PR 准备）
- `MCPToolResult`：MCP 工具执行结果
  - 支持 MCP 协议的 content 格式
  - 错误处理（error_code、error_message）
  - 提供 `to_text()` 方法转换为文本

### 2. MCP Client 实现

**文件**：`src/core/mcp/client.py`
- `MCPClient`：单个 MCP Server 连接客户端
  - **连接管理**：
    - `connect()`：连接到 MCP Server
    - `disconnect()`：断开连接并清理资源
    - `_connect_stdio()`：stdio transport 实现
  - **协议实现**：
    - `_send_request()`：发送 JSON-RPC 请求并等待响应
    - `_send_notification()`：发送 JSON-RPC 通知（无响应）
    - `_read_responses()`：后台任务读取服务器响应
  - **工具管理**：
    - `_list_tools()`：列出服务器提供的工具
    - `call_tool()`：调用工具并返回结果
  - **并发支持**：
    - 使用 `_pending_responses` 字典管理并发请求
    - 支持多个工具调用同时进行

### 3. MCP Client Manager 实现

**文件**：`src/core/mcp/manager.py`
- `MCPClientManager`：管理多个 MCP Server 连接
  - **连接管理**：
    - `connect_all()`：连接所有启用的服务器
    - `disconnect_all()`：断开所有连接
  - **工具聚合**：
    - 使用前缀避免工具名称冲突（`mcp_{server}_{tool}`）
    - `get_all_tool_specs()`：获取所有工具 spec
    - `get_tool_schema()`：根据名称获取工具 schema
  - **工具调用路由**：
    - `call_tool()`：解析工具名称并路由到对应服务器

### 4. 测试基础设施

**文件**：`tests/mcp/stub_server.py`
- Stub MCP Server 实现
  - 提供 3 个测试工具：`echo`、`add`、`error`
  - 完整的 MCP 协议实现（initialize、tools/list、tools/call）
  - 用于单元测试和集成测试

**文件**：`tests/test_mcp_client.py`
- MCP Client 单元测试（8 个测试）
  - ✅ `test_mcp_client_connect_and_disconnect`
  - ✅ `test_mcp_client_discovers_tools`
  - ✅ `test_mcp_client_call_echo_tool`
  - ✅ `test_mcp_client_call_add_tool`
  - ✅ `test_mcp_client_handles_tool_error`
  - ✅ `test_mcp_client_raises_on_unknown_tool`
  - ✅ `test_mcp_client_raises_when_not_connected`
  - ✅ `test_mcp_client_concurrent_tool_calls`

**文件**：`tests/test_mcp_manager.py`
- MCP Manager 单元测试（7 个测试）
  - ✅ `test_mcp_manager_connects_to_multiple_servers`
  - ✅ `test_mcp_manager_skips_disabled_servers`
  - ✅ `test_mcp_manager_call_tool`
  - ✅ `test_mcp_manager_raises_on_invalid_tool_name`
  - ✅ `test_mcp_manager_get_all_tool_specs`
  - ✅ `test_mcp_manager_get_tool_schema`
  - ✅ `test_mcp_manager_disconnect_all`

## 测试结果

### 单元测试
- **MCP Client 测试**：8/8 通过 ✅
- **MCP Manager 测试**：7/7 通过 ✅
- **总计**：15/15 通过 ✅

### 集成测试
- **Harness Evals**：39/39 通过 ✅
- 确认没有破坏现有功能

## 技术亮点

1. **异步并发**：
   - 使用 asyncio 实现非阻塞 I/O
   - 支持并发工具调用
   - 后台任务处理服务器响应

2. **错误处理**：
   - 连接超时处理
   - 工具调用错误处理
   - 优雅的断开连接和资源清理

3. **可扩展性**：
   - 设计支持多种 transport（stdio、http、websocket）
   - 工具名称前缀机制避免冲突
   - 为后续 PR（policy、idempotency）预留接口

4. **测试覆盖**：
   - 完整的单元测试覆盖
   - Stub server 用于隔离测试
   - 并发场景测试

## 已知限制

1. **Transport 支持**：
   - 当前只实现了 stdio transport
   - HTTP 和 WebSocket transport 将在后续 PR 中实现

2. **错误重试**：
   - 当前没有自动重试机制
   - 将在 PR-6b 中集成到 retry policy

3. **工具元数据**：
   - `is_mutating` 和 `idempotency_key_extractor` 字段已定义
   - 实际使用将在 PR-6d 中实现

## 下一步（PR-6b）

1. **Tool Registry 集成**：
   - 将 MCP 工具集成到现有的 Tool Registry
   - 统一内置工具和 MCP 工具的执行接口

2. **配置文件支持**：
   - 创建 `config/mcp_servers.json` 配置文件
   - 支持从配置文件加载 MCP Server

3. **启动时连接**：
   - 在 Cora 启动时自动连接 MCP Server
   - 处理连接失败和重试

4. **LLM 集成**：
   - 将 MCP 工具添加到 LLM 的 tool list
   - 测试 LLM 能否正确调用 MCP 工具

## 参考

- [cora-phase6-mcp-integration-design.md](./cora-phase6-mcp-integration-design.md) — Phase 6 设计文档
- [Model Context Protocol Specification](https://spec.modelcontextprotocol.io/) — MCP 协议规范

