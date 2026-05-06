---
name: archive-core
description: Shared filesystem archive contract for saving, indexing, and locating archived assets across higher-level archive workflows.
version: 0.1.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [archive, filesystem, indexing, retrieval]
---

# Archive Core

This skill defines the shared archive contract for Cora's filesystem-first archive workflows.

Use this skill when a workflow needs to:

- save a file into a topic folder
- append a structured archive record
- look up a previously archived asset
- rebuild or inspect archive metadata

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

- `scripts/save_asset.py`
  Saves a file into a topic folder and appends an index record.

- `scripts/find_asset.py`
  Searches the archive index and returns matching records.

- `scripts/update_index.py`
  Appends one structured archive record to the archive index.

## Output Contract

All scripts return JSON to stdout so that generic tools or future agent loops can consume them reliably.

If a script fails, it should return a non-zero exit code and a concise error message on stderr.
