# PR-6b: Tool Registry 集成与配置文件支持 - 完成总结

> 状态：✅ 已完成  
> 日期：2026-05-25  
> 前置：PR-6a（MCP Client 基础设施）

## 实现内容

### 1. 配置文件支持

**文件**：`config/mcp_servers.json`
- 示例配置文件，包含两个示例 MCP server
- 支持 stdio transport 配置
- 包含所有配置选项的示例

**文件**：`src/core/mcp/config_loader.py`
- `load_mcp_configs()`：从 JSON 文件加载配置
- `_parse_server_config()`：解析单个 server 配置
- 支持默认配置路径（`config/mcp_servers.json`）
- 完整的错误处理和验证

### 2. MCP Tool Adapter

**文件**：`src/core/mcp/tool_adapter.py`
- `MCPToolAdapter`：MCP 工具适配器类
  - `create_tool_specs()`：为所有 MCP 工具创建 ToolSpec
  - `_create_tool_spec()`：为单个 MCP 工具创建 ToolSpec
  - `_create_handler()`：创建工具处理函数
- **功能**：
  - 将 MCP 工具转换为 Cora 内部 ToolSpec 格式
  - 生成异步处理函数
  - 错误处理和结果转换
  - 元数据传递

### 3. Tool Manager 扩展

**文件**：`src/core/tools/manager.py`（扩展）
- 添加 `mcp_manager` 参数
- 添加 `mcp_adapter` 参数
- `_register_mcp_tools()`：注册 MCP 工具到 registry
- **集成点**：
  - 在 `__post_init__` 中自动注册 MCP 工具
  - MCP 工具与内置工具使用统一接口
  - 支持通过 registry 调度 MCP 工具

### 4. 测试覆盖

**文件**：`tests/test_mcp_config_loader.py`（6 个测试）
- ✅ `test_load_mcp_configs_from_file`
- ✅ `test_load_mcp_configs_returns_empty_list_if_file_not_found`
- ✅ `test_load_mcp_configs_raises_on_invalid_json`
- ✅ `test_load_mcp_configs_raises_on_missing_servers_key`
- ✅ `test_load_mcp_configs_raises_on_missing_required_fields`
- ✅ `test_load_mcp_configs_uses_defaults_for_optional_fields`

**文件**：`tests/test_mcp_tool_adapter.py`（4 个测试）
- ✅ `test_mcp_tool_adapter_creates_tool_specs`
- ✅ `test_mcp_tool_adapter_tool_spec_properties`
- ✅ `test_mcp_tool_adapter_handler_executes_tool`
- ✅ `test_mcp_tool_adapter_handler_handles_errors`

**文件**：`tests/test_mcp_tool_manager_integration.py`（5 个测试）
- ✅ `test_tool_manager_registers_mcp_tools`
- ✅ `test_tool_manager_can_get_mcp_tool_specs`
- ✅ `test_tool_manager_builds_model_specs_for_mcp_tools`
- ✅ `test_tool_manager_can_dispatch_mcp_tool`
- ✅ `test_tool_manager_mcp_and_builtin_tools_coexist`

## 测试结果

### 单元测试
- **Config Loader 测试**：6/6 通过 ✅
- **Tool Adapter 测试**：4/4 通过 ✅
- **Tool Manager 集成测试**：5/5 通过 ✅
- **总计**：15/15 通过 ✅

### 集成测试
- **Harness Evals**：39/39 通过 ✅
- 确认没有破坏现有功能

### 累计测试（PR-6a + PR-6b）
- **MCP 模块测试**：30/30 通过 ✅
- **Harness Evals**：39/39 通过 ✅

## 技术亮点

1. **无缝集成**：
   - MCP 工具与内置工具使用相同的 ToolSpec 接口
   - 通过 ToolRegistry 统一管理
   - 支持相同的调度机制

2. **配置驱动**：
   - JSON 配置文件定义 MCP servers
   - 支持启用/禁用 server
   - 完整的配置验证

3. **适配器模式**：
   - MCPToolAdapter 桥接 MCP 和 Cora 工具系统
   - 自动生成处理函数
   - 透明的错误处理和结果转换

4. **向后兼容**：
   - ToolManager 的 `mcp_manager` 参数是可选的
   - 不影响现有代码
   - 可以选择性启用 MCP 支持

## 使用示例

### 基本用法

```python
from core.mcp import load_mcp_configs, MCPClientManager
from core.tools.manager import ToolManager
from core.tools.registry import ToolRegistry

# 加载配置
configs = load_mcp_configs("config/mcp_servers.json")

# 创建 MCP manager 并连接
mcp_manager = MCPClientManager(configs)
await mcp_manager.connect_all()

# 创建 ToolManager with MCP 支持
registry = ToolRegistry()
tool_manager = ToolManager(
    registry=registry,
    auto_register_builtins=True,
    mcp_manager=mcp_manager,
)

# 现在可以使用 MCP 工具了
all_tools = tool_manager.registry.names()
# 包含内置工具和 MCP 工具（mcp_{server}_{tool}）
```

### 配置文件示例

```json
{
  "servers": [
    {
      "name": "database",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres"],
      "env": {
        "POSTGRES_CONNECTION_STRING": "postgresql://..."
      },
      "enabled": true,
      "timeout": 30.0
    }
  ]
}
```

## 已知限制

1. **Toolset 解析**：
   - MCP 工具的 toolset（如 `mcp_test`）不在默认 toolset 配置中
   - 需要直接通过工具名称访问
   - 将在后续 PR 中改进

2. **Tool Policy**：
   - MCP 工具使用默认 policy（medium risk）
   - 详细的 policy 配置将在 PR-6c 中实现

3. **启动时连接**：
   - 当前需要手动创建和连接 MCP manager
   - 自动启动将在后续集成中实现

## 下一步（PR-6c）

1. **Tool Policy 集成**：
   - MCP 工具的 allow/deny/ask 策略
   - 支持通配符配置（`mcp_database_*`）
   - HITL 确认流程

2. **Policy 配置文件**：
   - 扩展 `config/tool_policies.yaml`
   - 为 MCP 工具定义策略规则

3. **测试**：
   - Policy 相关的 eval 测试
   - HITL 流程测试

## 参考

- [cora-phase6-mcp-integration-design.md](./cora-phase6-mcp-integration-design.md) — Phase 6 设计文档
- [pr-6a-summary.md](./pr-6a-summary.md) — PR-6a 完成总结
- [Model Context Protocol Specification](https://spec.modelcontextprotocol.io/) — MCP 协议规范

