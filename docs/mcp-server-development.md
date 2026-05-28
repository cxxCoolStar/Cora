# 开发自定义 MCP Server（Cora）

Cora 当前仅实现 **stdio** transport。你的 server 需与 `src/core/mcp/client.py` 使用相同的 **JSON-RPC 2.0、按行分隔** 协议。

## 协议要点

### 连接握手

1. Client → `initialize`（带 `id`）
2. Client → `notifications/initialized`（无 `id`）
3. Client → `tools/list`（带 `id`）

Server 对每条带 `id` 的请求回复一行 JSON：

```json
{"jsonrpc":"2.0","id":1,"result":{...}}
```

### `tools/list` 响应

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "echo",
        "description": "Echo text back.",
        "inputSchema": {
          "type": "object",
          "properties": {"text": {"type": "string"}},
          "required": ["text"]
        }
      }
    ]
  }
}
```

### `tools/call` 响应

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{"type": "text", "text": "hello"}],
    "isError": false
  }
}
```

错误时使用 `error` 字段而非 `result`。

## 参考实现

仓库内最小示例：

```text
examples/mcp_servers/echo_server.py
```

约 120 行 Python，无第三方 MCP SDK 依赖，适合对照协议调试。

启用配置见 `examples/mcp_servers/cora_local_stdio.example.json`。

## 在 Cora 中注册

1. 在 `config/mcp_servers.json` 增加 server 条目（`command` + `args`）。
2. 设置 `CORA_MCP_ENABLED=true` 并重启 gateway。
3. 工具自动注册为 `mcp_{server}_{tool}`，并应用 `config/mcp_tool_metadata.json` 中的 mutating 规则。

## 工具命名建议

- 读操作用 `read` / `query` / `list` / `search` 子串，便于 `background_readonly` profile 放行。
- 写操作避免放在 `wechat_safe` 默认 allow 路径外；mutating 工具务必配置 metadata。
- 多 server 时工具名由 Cora 加前缀，server 内工具名无需全局唯一。

## 测试

| 层级 | 方式 |
|------|------|
| Server 单测 | 向 stdin 写 JSON-RPC 行，读 stdout |
| Cora 集成 | 启用 local server，`/tool mcp_local_echo {...}` |
| Harness | `evals/cases/harness/mcp_tool_*.json`；eval 使用 in-process stub（`src/core/mcp/eval_stubs.py`） |

运行全量 harness：

```powershell
.\scripts\run_harness_evals.cmd
```

## 进一步阅读

- [MCP 规范](https://spec.modelcontextprotocol.io/)
- [mcp-integration-guide.md](./mcp-integration-guide.md)
