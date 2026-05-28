# MCP 集成用户指南

Cora 通过 [Model Context Protocol](https://modelcontextprotocol.io/) 把外部工具接到与内置 `skill_run` 相同的 harness 上：tool policy、HITL、plan 幂等与重试。

## 快速开始

### 1. 配置 Server

编辑 `config/mcp_servers.json`（可参考 `examples/mcp_servers/cora_local_stdio.example.json`）：

```json
{
  "servers": [
    {
      "name": "local",
      "transport": "stdio",
      "command": "python",
      "args": ["examples/mcp_servers/echo_server.py"],
      "enabled": true,
      "timeout": 30.0,
      "retry_on_failure": true,
      "max_retries": 3
    }
  ]
}
```

### 2. 启用环境变量

```env
CORA_MCP_ENABLED=true
CORA_MCP_CONFIG_PATH=config/mcp_servers.json
```

### 3. 启动 Gateway

```powershell
python -m core.cli.main gateway
```

启动日志中应出现 MCP 连接成功及注册的工具数量。工具名格式为：

```text
mcp_{server_name}_{tool_name}
```

例如 `mcp_local_echo`、`mcp_local_add`。

### 4. 调用方式

- **微信 / 对话**：模型在 toolset 允许时直接选择 MCP 工具（`wechat_safe` 默认对 MCP **ask**，需用户确认）。
- **CLI 调试**：`/tool mcp_local_echo {"text":"hello"}`（与内置工具相同语法）。

## Policy 与 HITL

MCP 工具与内置工具共用 `ToolPolicyEngine`，按 **policy profile** 治理：

| Profile | MCP 行为（摘要） |
|---------|------------------|
| `wechat_safe` | 默认 `ask`；拒绝 `mcp_*_shell` / `exec` / `delete` 模式 |
| `background_readonly` | 仅允许 read/query/list/search 类 MCP 名 |
| `coding_full` | 默认允许所有 MCP 工具 |

配置见 `src/core/agent/policy_profiles.py`。微信端回复「确认」/「拒绝」完成 HITL（见 `docs/wechat-hitl.md`）。

## Mutating 工具与 Plan Resume

对会改状态的 MCP 工具，在 `config/mcp_tool_metadata.json` 中声明：

```json
{
  "tools": {
    "mcp_*_write": {
      "pattern": true,
      "is_mutating": true,
      "is_idempotent": true,
      "idempotency_key_extractor": "args.file_path"
    }
  }
}
```

Plan 执行 resume 时会根据 idempotency key 跳过已完成的 MCP 写操作。详见 `docs/cora-phase6-mcp-integration-design.md` PR-6d。

## 故障排查

| 现象 | 检查 |
|------|------|
| 启动无 MCP 工具 | `CORA_MCP_ENABLED=true`；`enabled: true`；command/args 路径正确 |
| 工具调用超时 | 增大 `timeout`；确认 server 进程未退出 |
| 微信一直要确认 | `wechat_safe` 的 `mcp_default_policy=ask`；或收窄 tool 名 |
| Policy 拒绝 | 工具名是否匹配 `mcp_denied_patterns` / readonly profile |

连接失败时 Cora **不会** 阻止 gateway 启动，仅该 server 的工具不可用。

## 相关文档

- [mcp-server-development.md](./mcp-server-development.md) — 编写自定义 MCP server
- [cora-phase6-mcp-integration-design.md](./cora-phase6-mcp-integration-design.md) — 架构与设计
- [examples/mcp_servers/README.md](../examples/mcp_servers/README.md) — 本地示例 server
