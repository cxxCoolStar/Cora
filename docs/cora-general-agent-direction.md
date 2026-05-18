# Cora General Agent Direction

## Status

This document is the current architecture and product direction for Cora.

It supersedes the earlier "Hermes-lite Phase 1" and archive-first planning
documents that treated Cora primarily as a WeChat archive assistant.

Those older planning documents were removed to keep the repository from
pulling future implementation work back toward a narrower target.

## Goal

Cora should become a general-purpose agent runtime that can:

- write, read, and modify code
- inspect and search the local workspace
- execute terminal commands
- search and extract information from the web
- automate browser tasks when needed
- use reusable skills for domain workflows
- preserve durable user memory and recall prior session context

The archive and WeChat scenarios remain important, but they are no longer the
main architecture story. They should become domain skills and product shells on
top of a general runtime.

## Non-Goal

Cora is not trying to become a copy of Hermes line by line.

The target is Hermes-like in shape:

- a general loop
- a broad but structured tool surface
- stable prompt layering
- reusable skills
- multiple product shells

The target is not:

- an archive assistant with a few extra tools
- a WeChat-only runtime
- a prompt-only skill system

## North Star

The desired architecture is:

`shell -> runtime loop -> generic toolsets -> skills -> storage / external systems`

Examples of shells:

- WeChat
- local CLI
- HTTP API

Examples of generic toolsets:

- `file`
- `terminal`
- `web`
- `browser`
- `skills`
- `user_memory`
- `session_search`
- `code_execution`

Examples of domain skills built on top:

- `archive-core`
- future coding workflows
- future research workflows
- future delivery / automation workflows

## What Stays From The Current Refactor

Several current changes are still correct and should remain part of the
general-agent plan:

1. the unified main loop under `src/core/agent/`
2. explicit prompt layering in `prompt_builder.py`
3. separation between runtime state, session summary, and long-term user memory
4. tool registry and toolset-based exposure
5. skills that can include scripts and supporting files
6. eval-driven development rather than behavior-by-impression

These are good foundations for a general agent and should be extended rather
than reverted.

## What Must Change

The current repository still carries several archive-first assumptions. Those
assumptions should be removed over time.

### 1. Archive Should Stop Being The Main Runtime Narrative

The system prompt, tool guidance, and runtime policy should not frame Cora as
"mainly an archive workflow that also acts like an agent."

Instead, the runtime should describe Cora as a general assistant that happens
to have archive capabilities among other tools.

### 2. Generic Tools Must Become First-Class

To reach the intended target, Cora needs first-class generic tools rather than
only archive-flavored workflows.

The next core toolsets should be:

- `file`
  - list files
  - search files
  - read files
  - write files
  - patch files
- `terminal`
  - command execution
  - background process management
- `web`
  - search
  - extract page content
- `browser`
  - navigation
  - DOM / text snapshot
  - click / type / scroll / back
- `skills`
  - list skills
  - view skill instructions and supporting files
- `user_memory`
- `session_search`

Later additions may include:

- `code_execution`
- `delegation`
- `mcp`

### 3. Archive Must Become A Domain Capability

`archive-core` should remain valuable, but it should be treated as one domain
workflow among many.

That means:

- archive save / search / read / delete / deliver may still be implemented by
  `archive-core`
- the runtime should not hard-code archive as the agent's primary identity
- archive-specific heuristics should shrink as generic tools mature

### 4. WeChat Should Become One Shell, Not The Whole Product

WeChat remains a supported shell, but it should not define the architecture for
the entire project.

The runtime should be understandable without mentioning WeChat at all.

## Prompt Direction

The prompt should keep the current layered structure, but the content needs to
change.

### Keep

- stable agent identity
- execution discipline
- memory boundary guidance
- skills summary
- runtime state summary
- user memory snapshot
- session-summary-as-reference wording

### Change

- remove archive-first identity text
- remove archive-only mental-model wording
- stop teaching the model that `archive-core` is the primary runtime path for
  the whole system
- teach the model to choose among generic tools based on the task

The prompt should describe Cora as a coding-capable and research-capable agent,
not as a specialized archive assistant.

## Tool Surface Direction

The model-facing surface should gradually move toward Hermes-like generic
toolsets instead of a narrow product surface.

Recommended platform presets:

- `cora-wechat`
  - compact set suitable for messaging
  - likely excludes unrestricted terminal and broad browser automation
- `cora-cli`
  - coding-focused set
  - includes `file`, `terminal`, `web`, `browser`, `skills`, `user_memory`,
    `session_search`
- `cora-api`
  - general programmable surface
  - configurable per deployment

This is a better fit than making every platform share an archive-centered tool
story.

## Memory Direction

The current memory boundary work should stay, but the scope should broaden.

Recommended memory layers:

1. runtime state
   - operational state for the current turn
2. session summary
   - compressed handoff context
3. user memory
   - durable personal facts and preferences
4. session search / historical recall
   - explicit retrieval over prior conversations and records

The important rule is that durable memory and temporary session context must
remain distinct.

## Migration Plan

### Phase 0: Freeze The New Direction

- keep this document as the source of truth
- avoid adding new docs that restore the old "Hermes-lite Phase 1" framing

### Phase 1: Generalize The Prompt And Tool Story

- rewrite archive-first prompt language
- add missing generic toolsets
- keep `archive-core` working, but demote it from primary narrative

### Phase 2: Add Coding And Web Research Capability

- terminal execution
- file write / patch support
- web search and extraction
- browser automation where needed
- tests and evals for coding and web tasks

### Phase 3: Expand Product Shells

- keep WeChat
- add a local CLI shell
- strengthen the HTTP API shell

### Phase 4: Advanced Agent Features

- session search
- code execution helpers
- optional delegation
- optional MCP integration

These later features should only land after the generic runtime and tool
surface are stable.

## Immediate Implementation Guidance

If a change is ambiguous, prefer the option that makes Cora more like a general
agent runtime.

Examples:

- prefer generic `terminal` and `web` tools over archive-specific wrappers
- prefer a platform preset over hard-coding one shell's assumptions globally
- prefer skill-backed domain workflows over business logic spread across the
  service layer
- prefer shrinking special-case heuristics in `turn_runner.py`
- prefer eval coverage for coding and web tasks, not only archive retrieval

## Acceptance Criteria

Cora is moving in the right direction when the following become true:

1. a new contributor can describe Cora as a general agent before mentioning
   archive or WeChat
2. coding, file editing, and web research are first-class supported tasks
3. the prompt no longer centers archive-specific instructions
4. archive remains supported as a domain capability
5. platform shells differ mainly by policy and available toolsets, not by
   separate agent architectures
6. evals cover coding, web research, memory, and archive workflows together

## Repository Notes

The remaining document `docs/cora-evaluation-module-design.md` is still useful
as an evaluation design reference, but it should be interpreted through the
general-agent direction defined here.
