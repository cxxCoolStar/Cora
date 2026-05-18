# Archive Core Runtime Contract

This reference describes the runtime assumptions for `archive-core` script execution.

## Execution Model

- Runtime turns should load `archive-core` with `skill_view("archive-core")` before using `skill_run`.
- Runtime archive actions should use the dispatcher entrypoint:
  - `skill_run(name="archive-core", script_path="scripts/archive_dispatch.py", input={...})`
- The script process may run with its cwd set to the script directory rather than the repository root.

## Path And URL Requirements

- Treat relative filesystem paths as unstable across process boundaries.
- Prefer absolute paths for:
  - `storage_dir`
  - `upload_path`
  - any file path included in effect payloads
- Prefer an absolute database URL for `database_url`.
- For SQLite, use an absolute URL such as:
  - `sqlite:///C:/full/path/to/.cora/clawbot.db`
  - not `sqlite:///.cora/clawbot.db` when the receiving process may run under another cwd

## Required Dispatcher Inputs

The dispatcher expects one JSON object with:

- `intent`
- `session_id`
- `source_message_id`
- optional `source_event_id`
- optional `runtime_state`
- optional `database_url`
- optional `storage_dir`
- optional `upload_path`
- optional `upload_name`
- `arguments`

Do not leave `intent` blank.

## Intent Mapping

Common user asks should map like this:

- "把照片发我" / "send me the photo" -> `intent: "deliver"`
- "帮我保存这张图" -> `intent: "save"`
- "找一下上次那个文件" -> `intent: "search"`
- "打开那条记录" / "read that item" -> `intent: "read"`
- "删掉那张图片" -> `intent: "delete"`

## Failure Modes

Typical runtime failures include:

- wrong script path
- blank or unsupported `intent`
- relative SQLite URL resolving under the wrong cwd
- missing file path for a resolved delivery target
- no active channel mapping for downstream file delivery

When one of these assumptions is uncertain, inspect this reference rather than inventing a recovery path in chat.
