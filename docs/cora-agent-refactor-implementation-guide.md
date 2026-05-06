# Cora Agent Refactor Implementation Guide

## Purpose

This document captures the agreed refactor direction for turning the current Cora codebase into a more general AI agent runtime.

It is written as an implementation guide for future work. It reflects the decisions already discussed about:

- tool-layer redesign
- loop/orchestrator redesign
- skill boundaries
- filesystem-oriented archive strategy
- migration order

The goal is not to preserve the current ClawBot flow. The goal is to replace it with a more general agent architecture that still supports the current WeChat archive scenario.

## North Star

The future Cora architecture should look like this:

1. A general agent runtime with a single main loop.
2. A small set of generic tools plus a few domain-neutral helper capabilities.
3. Skills that describe workflows and may include reusable scripts.
4. A filesystem-first archive layout using folders as topics and a structured log as the retrieval index.
5. Thin product policies for the current WeChat scenario, not product-specific business logic embedded everywhere.

The intended layering is:

`skill -> tool -> helper script / capability -> filesystem or channel`

Not:

`skill -> hidden product service maze`

## Problems In The Current Architecture

The current implementation is centered around [service.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/service.py), [tools.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/tools.py), and [ingestion/service.py](C:/Users/asta1/ai-project/Cora/src/core/ingestion/service.py).

Main issues:

- `ClawBotService.ingest()` mixes message intake, routing, clarification logic, tool orchestration, persistence, and reply generation.
- There are multiple pre-loop LLM routing steps such as `_interpret_initial_input()`, `_interpret_pending_input_reply()`, `_interpret_clarification_reply()`, and `_resolve_reference_candidate_via_llm()`.
- Current tools are too operation-specific and reflect the current product shape rather than a reusable agent shape.
- Topic classification is part of the save path, which makes archive writes slow and brittle.
- Current domain logic is still product-centered rather than capability-centered.

## Architectural Decisions

### 1. Replace The Current Product Flow

The current ClawBot-specific flow should be treated as legacy behavior to be replaced, not extended.

The future agent should not depend on:

- multiple pre-loop intent routers
- hardcoded save/read/send branching in `ClawBotService`
- current topic service semantics as the main abstraction

### 2. Move To A Unified Main Loop

The new runtime should follow a Hermes-style unified loop:

1. accept inbound event
2. persist raw conversation event
3. load runtime state
4. build messages and available tools
5. run one main model loop
6. execute tool calls
7. append tool results back into the message trace
8. continue until a final user-facing answer is produced

There should be no separate LLM mini-loops outside the main loop for:

- initial intent interpretation
- pending input routing
- reference-resolution routing
- clarification reply routing

Those decisions should happen inside the main loop via tools.

### 3. Use Fewer, More General Tools

Tool boundaries should follow Hermes-style domain grouping, not one-operation-per-tool.

The first target tool set is:

- generic tools: `read_file`, `write_file`, `search_files`, `patch`, `bash`
- optional support tools: vision analysis, channel send
- one shared archive workflow skill with scripts

If a Cora-specific tool layer is still needed during migration, keep it coarse. Prefer:

- `archive`
- `archive_state`

Do not preserve the current fine-grained tool split such as:

- `save_content`
- `save_file`
- `overview_knowledge_base`
- `list_topics`
- `open_topic`
- `read_item`
- `summarize_item`
- `send_file_to_user`
- `clarify_reference`
- `clarify_capture_intent`

If temporary Cora tools remain during migration, they should be compatibility shims only.

### 4. Skills Are Not Only Prompt Text

Skills may contain:

- `SKILL.md`
- references
- templates
- scripts

Therefore, workflow implementation may live partly in skill-owned scripts.

This is important. The future design is not "prompt-only skills". It is "workflow skills with executable helpers".

### 5. Core Capabilities Should Not Be Duplicated Across Skills

Even though skills may include scripts, the core archive behavior should be shared and reusable.

Recommended pattern:

- create one shared skill such as `archive-core`
- place shared archive scripts there
- let higher-level skills reuse the same scripts and conventions

Avoid having multiple unrelated skills each implement their own save/index/find logic.

## Filesystem-First Archive Design

### Topic Model

Future topics should be represented primarily as folders.

Example:

```text
archive/
  topics/
    personal-photos/
    travel/
    receipts/
```

This makes the archive naturally navigable with generic file tools and easier for a general agent to inspect.

### Index Model

Do not rely only on folder scanning.

Maintain a structured index log, preferably JSONL.

Example record:

```json
{"id":"img_20260506_001","type":"image","topic":"personal-photos","path":"archive/topics/personal-photos/wechat_image.jpg","summary":"Portrait photo taken at Zhujiang Park","description":"Young woman standing in a garden scene","source":"wechat","created_at":"2026-05-06T09:35:30+08:00"}
```

The index is the primary lookup surface for:

- retrieval
- delivery
- duplicate detection
- later reindexing
- audit/debugging

### Save Workflow

When saving an image, the intended skill-guided workflow is:

1. inspect the image content
2. inspect available topic folders
3. choose the best topic folder
4. save the file into that folder
5. append one structured record into the index log

### Send-Back Workflow

When sending a previously archived image back to the user, the intended workflow is:

1. search the index log
2. resolve the target file path
3. confirm the file exists
4. use channel delivery capability to send it

## Skill Strategy

### Shared Core Skill

Create a shared skill such as:

`skills/archive-core/`

Suggested contents:

- `SKILL.md`
- `scripts/save_asset.py`
- `scripts/find_asset.py`
- `scripts/update_index.py`
- `templates/index_record.json`
- optional references documenting topic conventions

This skill should define the shared archive contract.

### Higher-Level Skills

Add scenario skills on top of `archive-core`, for example:

- `wechat-archive-assistant`
- `archive-capture`
- `archive-retrieval`
- `archive-delivery`

Responsibilities:

- `archive-core`
  - shared archive rules
  - shared save/find/index scripts
- `archive-capture`
  - when to save
  - how to pick topics
- `archive-retrieval`
  - how to search and read archived content
- `archive-delivery`
  - how to locate and send archived files
- `wechat-archive-assistant`
  - channel-specific wording and behavioral rules

## What Should Not Become A Skill

The following should not be modeled as prompt-only behavior:

- channel adapters
- durable message persistence
- file send implementation
- structured index updates
- low-level atomic save operations
- concurrency-sensitive session context

These can be invoked from skills, but they still need stable code or scripts behind them.

## Loop Refactor Blueprint

### Current Target

The current hot spot is [service.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/service.py).

The long-term goal is for this file to become a thin compatibility entrypoint only.

### New Runtime Components

Suggested new runtime modules:

```text
src/core/agent/
  orchestrator.py
  loop.py
  prompt_builder.py
  runtime_state.py
  skill_loader.py
```

Responsibilities:

- `orchestrator.py`
  - turn-level coordination
  - persistence of user/assistant/tool trace
- `loop.py`
  - one unified model/tool loop
- `prompt_builder.py`
  - assemble identity, platform hints, skill index, runtime summary, history
- `runtime_state.py`
  - session-local working set, focus item, pending state, recent events
- `skill_loader.py`
  - load skill metadata and skill text for prompt inclusion

### New Unified Loop

High-level pseudocode:

```python
async def handle_turn(event):
    user_msg = persist_user_message(event)
    source_event = persist_source_event(event, user_msg)
    runtime = runtime_loader.load(event.session_id, source_event)

    messages = prompt_builder.build(
        runtime=runtime,
        user_input=event.text,
        upload=event.upload,
        skills=skill_loader.load_applicable_skills(runtime),
        tools=tool_registry.get_tool_definitions(),
    )

    result = await loop.run(messages=messages, runtime=runtime)
    persist_trace(result.trace)
    return result.final_reply
```

Main loop behavior:

1. call model
2. if no tool calls, finish
3. if tool calls exist, execute them
4. append assistant tool-call message
5. append tool-result message
6. update runtime state
7. continue

### Clarification Handling

Clarification should become part of the normal tool/message trace.

Do not keep the current model where clarification is mostly handled by service-level branching outside the loop.

Instead:

1. model decides clarification is needed
2. model uses the relevant tool or script-backed action
3. pending state is written explicitly
4. tool result is appended into the trace
5. next user reply resumes via the same main loop

## Capability Boundaries

The future system still needs durable capabilities, but they should be neutral and reusable.

Preferred capability shapes:

- archive filesystem persistence
- structured index maintenance
- archive lookup
- channel delivery
- session runtime state
- background enrichment

Avoid product-centric service shapes like:

- "ClawBot product flow service"
- "WeChat photo assistant main service"
- "personal wiki topic organizer as the only archive abstraction"

## Topic Classification

Topic selection should move closer to folder choice and archive conventions.

Additional enrichment, if retained, should be off the critical save path.

That means:

- save succeeds when file and index entry are durable
- enrichment may happen after save
- enrichment failure should not turn save into a user-visible failure

## Logging And Indexing Requirements

Archive writes must be debuggable.

At minimum, keep a structured append-only log containing:

- asset id
- source platform
- stored path
- topic folder
- file name
- short summary
- richer description when available
- created timestamp
- optional note from the user

If a save action succeeds for the file but fails for the index, that is a partial-failure condition and must be surfaced clearly.

## Migration Plan

### Phase 1: Document And Freeze Direction

Done by this guide.

No new product-specific branching should be added to the old `ClawBotService` path unless strictly needed as a short-term bug fix.

### Phase 2: Introduce Shared Archive Skill

Create `skills/archive-core/` with:

- `SKILL.md`
- shared scripts
- structured index template

This is the first new stable contract.

### Phase 3: Add Filesystem Archive Layout

Introduce:

- archive root
- topic folders
- JSONL index

Use this in parallel with current storage until confidence is high.

### Phase 4: Build Unified Agent Loop

Add new `src/core/agent/` loop components.

During this phase:

- keep current WeChat ingress
- route selected flows into the new loop
- keep legacy flow available as fallback

### Phase 5: Replace Fine-Grained Tooling

Retire current ClawBot-specific fine-grained tools from [builtin.py](C:/Users/asta1/ai-project/Cora/src/core/tools/builtin.py) and [tools.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/tools.py).

The default path should become:

- generic file/process tools
- skill-guided archive scripts
- optional temporary coarse archive tool wrappers

### Phase 6: Move Save/Retrieve/Deliver To Skill-Guided Archive Workflows

Main user flows to migrate:

- save uploaded image
- save note or link
- list/find archived assets
- send back archived image

### Phase 7: Decommission Legacy Product Flow

After the new loop and shared archive skill are stable:

- shrink `ClawBotService`
- remove old LLM pre-routing helpers
- remove legacy tool shims that no longer add value

## Implementation Constraints

- Favor generic, reusable naming over product naming.
- Keep the archive contract inspectable on disk.
- Keep core scripts shared, not duplicated in many skills.
- Preserve truthful failure semantics.
- Keep channel delivery outside prompt-only logic.
- Prefer JSONL or similarly structured logs over free-form notes.

## Open Design Questions

These are still allowed to change during implementation:

1. Whether the first migration should expose a temporary coarse `archive` tool, or immediately rely on generic file/process tools plus skill scripts only.
2. Whether the index should be global or per-topic split.
3. Whether image descriptions should be stored inline in the JSONL record or sidecar markdown/json files.
4. Whether current SQLite persistence remains as a parallel source of truth during transition or becomes a temporary compatibility layer only.

## Immediate Next Steps

Recommended first implementation steps:

1. Create `skills/archive-core/` and define the shared archive contract.
2. Define the filesystem layout for archive topics and index logs.
3. Write the first archive helper scripts:
   - `save_asset.py`
   - `find_asset.py`
   - `update_index.py`
4. Add a new unified agent loop package under `src/core/agent/`.
5. Route one narrow user flow through the new path first:
   - save uploaded image
   - retrieve and send archived image

## Summary

The refactor direction is:

- replace the current ClawBot-centric flow
- adopt a unified Hermes-style loop
- reduce tool granularity
- lean on skills that include reusable scripts
- move archive organization to a filesystem-plus-index design
- keep shared archive logic centralized in a reusable core skill

This should make Cora more general, more inspectable, and more suitable as a long-term AI agent platform rather than a single product flow.
