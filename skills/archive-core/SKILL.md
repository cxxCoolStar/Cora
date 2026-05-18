---
name: archive-core
description: Shared filesystem archive contract for saving, indexing, and locating archived assets across higher-level archive workflows.
version: 0.1.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [archive, filesystem, indexing, retrieval]
  cora:
    runtime:
      entrypoint: scripts/archive_dispatch.py
      required_input_fields: [intent]
      intent_phrases:
        deliver: [发我, 发给我, 发回来, 发送, 传给我, send me, send back, deliver]
        save: [保存, 存一下, 记住, archive, save]
        search: [找, 查, 查一下, 搜索, find, search, look up]
        read: [打开, 读取, 看看, 展开, 全文, read, open, show]
        delete: [删除, 删掉, 移除, remove, delete]
        overview: [概览, 总览, overview]
        list_topics: [主题, topic, categories]
        clarify: [澄清, clarify]
        resolve_pending: [确认, 继续, cancel, summarize]
---

# Archive Core

This skill defines the shared archive contract for Cora's filesystem-first archive workflows.

Use this skill when a workflow needs to:

- save a file into a topic folder
- append a structured archive record
- look up a previously archived asset
- rebuild or inspect archive metadata
- save, search, or read content records from Cora's database through skill scripts
- resolve a saved file for downstream delivery

## Runtime Prerequisites

Before running archive-core scripts in a runtime turn:

- the database must be reachable from the script process
- `database_url` should be an absolute SQLite URL or another directly openable database URL
- `storage_dir` should be an absolute path when files may be resolved across process boundaries
- if you need path semantics, cwd assumptions, or failure modes, load `references/runtime-contract.md`

## Archive Layout

The archive root uses this structure:

```text
archive-root/
  topics/
    <topic-slug>/
      asset.ext
  logs/
    archive_index.jsonl
```

`topics/` stores the durable asset files.

`logs/archive_index.jsonl` is the primary lookup surface for archive retrieval.

## Shared Rules

1. Treat folder selection and index writing as part of one archive workflow.
2. Use topic folders as the main organizational boundary.
3. Write one JSON object per line into `archive_index.jsonl`.
4. Never assume folder scanning alone is enough for retrieval; always prefer the index.
5. If file persistence succeeds but index writing fails, surface that as a partial failure.
6. Keep records append-only. If later updates are needed, write a new record rather than mutating history in place.

## Save Workflow

When saving an asset:

1. Determine the target topic folder.
2. Save the file under `topics/<topic-slug>/`.
3. Create a structured archive record.
4. Append the record to `logs/archive_index.jsonl`.

Use:

- `scripts/save_asset.py` for the full save-and-index workflow
- `scripts/update_index.py` when only an index append is needed

## Lookup Workflow

When locating an archived asset:

1. Search `logs/archive_index.jsonl`.
2. Match by id, topic, filename, path fragment, summary, or description.
3. Resolve the top candidate to a real file path.
4. Confirm the path exists before downstream actions such as delivery.

Use:

- `scripts/find_asset.py` for structured lookup

## Script Entry Points

For Hermes-lite runtime turns, the main entrypoint is:

- `scripts/archive_dispatch.py`

Load this skill with `skill_view("archive-core")` first, then run the dispatcher through:

- `skill_run(name="archive-core", script_path="scripts/archive_dispatch.py", input={...})`

This dispatcher path is mandatory for runtime turns. Do not invent alternate files such as `scripts/deliver.js`, `scripts/search.js`, or other ad-hoc entrypoints unless this skill document explicitly adds them later.

The dispatcher expects one JSON object with:

- `intent`: `save`, `search`, `read`, `delete`, `deliver`, `overview`, `list_topics`, `clarify`, or `resolve_pending`
- `session_id`
- `source_message_id`
- optional `source_event_id`
- `arguments`: skill-local arguments such as `text`, `query`, `mode`, or `user_note`
- optional `upload_path` and `upload_name` when saving a file upload

When calling the dispatcher, always set `intent` explicitly. Do not leave it blank.

Common runtime mappings:

- send back a saved photo, image, file, or attachment -> `intent: "deliver"`
- save an uploaded image or file -> `intent: "save"`
- search or find a previously saved note, photo, or file -> `intent: "search"`
- open, read, show, or summarize one saved item -> `intent: "read"`
- delete or remove a saved item -> `intent: "delete"`
- ask for a high-level archive summary -> `intent: "overview"`
- ask for topics/categories in the archive -> `intent: "list_topics"`

When a user sends a short follow-up text that is clearly describing a recently uploaded image or file, prefer treating that text as the asset's user note for `intent: "save"` instead of creating a standalone text note. In those cases the uploaded asset remains the primary archive item.

For delivery requests, use:

- `skill_run(name="archive-core", script_path="scripts/archive_dispatch.py", input={"intent":"deliver", ...})`

If the runtime turn depends on database or filesystem access, load `skill_view("archive-core", "references/runtime-contract.md")` before improvising assumptions about cwd, relative paths, or error recovery.

The dispatcher returns structured JSON with:

- `message`
- `status`
- `disposition`
- `action`
- `effects`
- `artifacts`
- `state_delta`
- optional `pending_state_delta` when the workflow needs clarification or resolves an existing pending state

- `scripts/save_asset.py`
  Saves a file into a topic folder and appends an index record.

- `scripts/find_asset.py`
  Searches the archive index and returns matching records.

- `scripts/update_index.py`
  Appends one structured archive record to the archive index.

- `scripts/save_content.py`
  Saves text content into Cora's database-backed content store.

- `scripts/search_content.py`
  Searches saved content records in Cora's database.

- `scripts/read_content.py`
  Reads one saved content record by item id or query.

## Output Contract

All scripts return JSON to stdout so that generic tools or future agent loops can consume them reliably.

If a script fails, it should return a non-zero exit code and a concise error message on stderr.
