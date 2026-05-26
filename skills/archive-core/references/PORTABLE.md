# Archive Core — Portable Contract (v1.0)

This skill is **host-agnostic**. Any agent runtime can integrate it through:

1. **CLI** — `archive-cli` reads one JSON object from stdin, writes one JSON result to stdout.
2. **Python** — `ArchiveRuntime` + `FileArchiveStore` in the `archive_core` package.
3. **MCP** (optional) — wrap the same runtime behind MCP tools (not bundled in v0.2).

## Environment

| Variable | Meaning |
|----------|---------|
| `ARCHIVE_ROOT` | Filesystem archive root (default `.cora/archive`) |
| `CORA_ARCHIVE_ROOT_DIR` | Cora-compatible alias for `ARCHIVE_ROOT` |

## Request (stdin / API)

See `schemas/request.schema.json`. Minimal example:

```json
{
  "schema_version": "1.0",
  "intent": "search",
  "arguments": { "query": "resume" }
}
```

## Result (stdout / API)

See `schemas/result.schema.json`. Hosts map `actions[]` to local capabilities:

| action.type | Typical host behavior |
|-------------|------------------------|
| `store_file` | Persist upload / register item |
| `deliver_file` | Send file on channel (WeChat, email, etc.) |

If the host cannot deliver files, still return `deliver_file` in `actions` and show `message` with the resolved path.

## Storage layout

```text
{ARCHIVE_ROOT}/
  topics/<topic-slug>/<files>
  logs/archive_index.jsonl
```

## Cora-specific notes

See `references/cora.md` for `archive_result_to_skill_payload`, DB merge, and WeChat effects.
