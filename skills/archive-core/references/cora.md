# Cora Host Integration

Cora loads `archive-core` from `skills/archive-core/` and keeps host-specific logic under `adapters/cora/`.

## Architecture

```text
archive_core (portable runtime + CLI)
    ↑
adapters/cora/ (dispatch, mirror, bridge, file_fallback)
    ↑
core/skills/ (hooks, bootstrap, runner, archive_run tool)
```

## Portable runtime (in-process or CLI)

```python
from core.archive.portable_bridge import run_portable_archive, archive_result_to_skill_payload

raw = run_portable_archive(
    {"intent": "search", "arguments": {"query": "resume"}},
    archive_root=settings.archive_root_dir,
    transport="in_process",
)
skill_payload = archive_result_to_skill_payload(raw)
```

## Cora dispatch (DB + file fallback)

- **`archive_run` tool** — preferred; calls `adapters/cora/dispatch.py` via `core.skills.runner`
- **`skill_run(scripts/archive_dispatch.py)`** — thin script shell; same in-process path when executor detects `archive_dispatch.py`
- **Hooks** — `bootstrap_host_skills()` registers `on_item_saved` → filesystem mirror

## Action mapping

| Portable `actions[].type` | Cora `effects[].kind` |
|---------------------------|------------------------|
| `store_file` | `ingest_saved_uploads` |
| `deliver_file` | `deliver_file` |

## WeChat flow

1. **Ingest** → SQLite + `.cora/files`
2. **Mirror hook** (default on) → `archive_index.jsonl` under `CORA_ARCHIVE_ROOT_DIR`
3. **archive_run / skill_run** → `adapters/cora/dispatch.py` (DB first, file index fallback)
4. **deliver_file** effect → WeChat gateway

Env:

```env
CORA_ARCHIVE_MIRROR_ENABLED=true
CORA_ARCHIVE_ROOT_DIR=.cora/archive
```
