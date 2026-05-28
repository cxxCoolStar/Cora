# MCP server examples

## `echo_server.py`

A minimal **stdio** MCP server compatible with Cora's `MCPClient` (`src/core/mcp/client.py`).

Tools exposed:

| Tool | Description |
|------|-------------|
| `echo` | Returns the `text` argument |
| `add` | Returns `a + b` as text |

### Enable in Cora

Add a server block to `config/mcp_servers.json` (or copy from `cora_local_stdio.example.json`):

```json
{
  "name": "local",
  "transport": "stdio",
  "command": "python",
  "args": ["examples/mcp_servers/echo_server.py"],
  "env": {},
  "enabled": true,
  "timeout": 30.0,
  "retry_on_failure": true,
  "max_retries": 3
}
```

Set environment variables and start gateway:

```env
CORA_MCP_ENABLED=true
CORA_MCP_CONFIG_PATH=config/mcp_servers.json
```

After startup, tools appear as `mcp_local_echo` and `mcp_local_add`.

### Manual smoke test

```powershell
python examples/mcp_servers/echo_server.py
```

Then paste JSON-RPC lines (one object per line); the server responds on stdout. Cora handles this protocol automatically when the gateway connects.
