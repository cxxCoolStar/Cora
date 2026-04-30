# Cora Wiki Tooling Refactor Spec

## 1. Overview

This document defines the next-stage tool architecture for `Cora`.

It replaces the earlier archive-agent tool model with a more explicit
`wiki/topic`-centric model inspired by `Hermes`, but intentionally much
smaller and narrower in scope.

The new goal is not to support a generic multi-domain tool ecosystem.

The goal is to make `Cora` behave like a reliable **personal knowledge wiki
agent** that can:

- capture text, links, and files as source materials
- organize those materials into stable topics
- browse the knowledge base structure directly
- read and summarize specific items
- maintain agent state such as working set, focus item, and clarification flow

## 2. Why Refactor the Tool Model

The current tool model is still shaped by the earlier archive/RAG design.

Current tools:

1. `save_text_or_link`
2. `save_file`
3. `search_items`
4. `get_item`
5. `summarize_item`
6. `clarify_reference`

That set was acceptable for:

- "save this"
- "find that"
- "show me the full text"

It is no longer a good fit for the new `llm_wiki` direction.

Examples:

- "现在知识库中有什么"
  This is not `search_items`.
  This is a knowledge-base overview request.

- "有哪些 topic"
  This is not item search.
  This is topic browsing.

- "看看网络配置这个主题下都有什么"
  This is not retrieval over chunks or item metadata.
  This is opening a topic.

- "把历史资料重新整理一下"
  This is not search or summarize.
  This is maintenance.

The problem is not just that some tools are missing.

The deeper problem is that the current tool taxonomy mixes together:

- content ingestion
- content reading
- knowledge navigation
- agent state management

That makes planner behavior less stable and makes the code harder to evolve.

## 3. Hermes Reference: What Matters

The relevant Hermes references are:

- `C:\Users\asta1\ai-project\hermes-agent\tools\registry.py`
- `C:\Users\asta1\ai-project\hermes-agent\toolsets.py`
- `C:\Users\asta1\ai-project\hermes-agent\model_tools.py`
- `C:\Users\asta1\ai-project\hermes-agent\run_agent.py`

The key takeaways are:

### 3.1 Tool registration is centralized

Hermes lets each tool self-register into a registry.

This means:

- tool identity is explicit
- schema and handler stay together
- tool metadata is queryable
- planner and runtime do not need to hardcode tool details everywhere

### 3.2 Toolsets are separate from tool definitions

Hermes defines:

- what a tool is
- which toolset it belongs to
- which tools are exposed in a specific platform/session

as separate layers.

This separation is valuable for `Cora`, even though `Cora` is much smaller.

### 3.3 Some tools are really agent-state tools

Hermes explicitly treats some tools as special agent-loop concepts
instead of generic dispatch-only tools.

This matters for `Cora` because things like:

- clarification
- working-set handling
- focus-item transitions
- knowledge overview
- reindex / maintenance

are not ordinary content operations.

They affect the agent's internal state machine.

## 4. What We Should Not Copy from Hermes

`Cora` should not become a general-purpose Hermes clone.

We should not copy:

- large cross-domain tool catalogs
- many platform-specific toolsets
- broad terminal/browser/web tool systems
- heavy plugin + MCP assumptions in the first iteration

`Cora` is a focused personal archive/wiki assistant.

So we borrow the **structure**, not the **surface area**.

## 5. New Tool Architecture

The new architecture should introduce three explicit layers:

1. `tool registry`
2. `toolsets`
3. `agent-state execution conventions`

### 5.1 Tool Registry

Recommended new module:

- `src/core/tools/registry.py`

Responsibilities:

- register tool definitions
- expose schemas for planner/model use
- map tool names to handlers
- expose metadata for debug/UI

Minimal `ToolSpec` shape:

```python
@dataclass(slots=True)
class ToolSpec:
    name: str
    toolset: str
    description: str
    schema: dict
    handler: Callable
    is_agent_stateful: bool = False
    read_only: bool = False
    requires_confirmation: bool = False
```

### 5.2 Toolsets

Recommended new module:

- `src/core/tools/toolsets.py`

`Cora` does not need dozens of toolsets.

It needs a few stable capability groups.

Proposed toolsets:

1. `capture`
2. `wiki_browse`
3. `wiki_read`
4. `wiki_maintenance`
5. `agent_state`

This is enough to express the whole product clearly.

### 5.3 Agent-State Tools

Some tools should be marked `is_agent_stateful=True`.

These are tools whose primary effect is not returning domain data, but
shaping how the conversation should continue.

Examples:

- `clarify_reference`
- `set_focus_item`
- `set_working_set`
- `overview_knowledge_base`
- `reindex_topics`

These tools should still be represented in the registry, but their runtime
behavior may be handled with additional state transitions rather than simple
"call handler, return string" logic.

## 6. Proposed V2 Tool Taxonomy

The V2 tool taxonomy should be:

### 6.1 Capture Tools

These write new source materials into the knowledge base.

1. `save_text`
2. `save_link`
3. `save_file`

Reason:

- text, link, and file captures are distinct enough to deserve first-class tools
- this avoids overloading `save_text_or_link`
- it makes planner behavior easier to debug

### 6.2 Wiki Browse Tools

These answer "what exists in the knowledge base?"

1. `overview_knowledge_base`
2. `list_topics`
3. `open_topic`
4. `list_recent_items`

Reason:

- users often want orientation, not search
- "what's in my knowledge base?" is not a search query
- topic browsing should be explicit

### 6.3 Wiki Read Tools

These answer "show me or summarize a known thing."

1. `read_item`
2. `summarize_item`

Optional later:

3. `compare_items`

Reason:

- reading a specific item is a separate action from finding it
- this keeps the mental model clean: browse first, then read

### 6.4 Wiki Maintenance Tools

These maintain the structure of the knowledge base.

1. `reindex_topics`
2. `reclassify_item`
3. `exclude_item_from_kb`

Reason:

- this separates maintenance from normal user-facing read flows
- maintenance may be slow, expensive, or interactive

### 6.5 Agent-State Tools

These manage conversation continuity.

1. `clarify_reference`
2. `clarify_capture_intent`
3. `resolve_working_set_reference`

These may remain partially internal rather than fully exposed to the model.

## 7. Tool Definitions: Recommended First Batch

The recommended first V2 batch is:

1. `save_text`
2. `save_link`
3. `save_file`
4. `overview_knowledge_base`
5. `list_topics`
6. `open_topic`
7. `read_item`
8. `summarize_item`
9. `clarify_reference`

This is a better first batch than immediately adding every maintenance tool.

Why:

- it covers the most common real user flows
- it aligns with the `llm_wiki` direction
- it reduces planner ambiguity immediately

## 8. Tool Behavior Expectations

### 8.1 `overview_knowledge_base`

Purpose:

- answer broad inventory questions

Examples:

- "现在知识库中有什么"
- "你这边存了哪些东西"
- "先给我看一下目前的知识库概览"

Expected output:

- top topics
- recent documents
- total counts
- optionally sensitive topic redaction rules later

This should not perform semantic search.

### 8.2 `list_topics`

Purpose:

- enumerate available topics

Examples:

- "有哪些主题"
- "列出我现在的 topic"

Expected output:

- topic names
- short summaries
- item counts

### 8.3 `open_topic`

Purpose:

- inspect a specific topic and produce a working set of topic items

Examples:

- "看看网络配置这个主题"
- "打开简历相关资料"

Expected output:

- topic summary
- top items under the topic
- updated working set

### 8.4 `read_item`

Purpose:

- read the full text or selected view of a specific item

Examples:

- "第一个给我全文"
- "看看那个简历"

Expected output:

- full text if short enough
- or structured read view if long

### 8.5 `summarize_item`

Purpose:

- summarize a known item already in focus

Examples:

- "这里面写了什么"
- "帮我总结这个文件"

Expected output:

- brief summary
- optionally structured key points later

## 9. Planner Changes

The planner should stop treating tool selection as:

- save
- search
- get
- summarize

and instead think in terms of:

1. capture
2. browse
3. read
4. maintain
5. clarify

That means the planner prompt should be rewritten around those categories.

Recommended planner rule examples:

- If the user asks what exists in the knowledge base, use `overview_knowledge_base`.
- If the user asks for topics, use `list_topics`.
- If the user asks for a named topic, use `open_topic`.
- If the user references a specific result or current focus, use `read_item` or `summarize_item`.
- If the user submits new material, use one of the capture tools.

## 10. Migration Plan

The migration should happen in three phases.

### Phase 1: Introduce registry + toolsets

Create:

- `src/core/tools/registry.py`
- `src/core/tools/toolsets.py`
- `src/core/tools/wiki_tools.py`
- `src/core/tools/agent_state_tools.py`

Do not change all behavior yet.

First goal:

- make tool definitions explicit and centralized

### Phase 2: Rebind current handlers to the new taxonomy

Map existing behavior:

- current ingestion logic -> capture tools
- current topic browse logic -> browse tools
- current read/summarize logic -> read tools
- clarification logic -> agent-state tools

At this stage:

- `search_items` should disappear from the planner surface
- `save_text_or_link` should split
- `get_item` should rename to `read_item`

### Phase 3: Add maintenance and stricter KB boundaries

Introduce:

- `reindex_topics`
- `exclude_item_from_kb`
- `reclassify_item`

And enforce clearer rules around:

- what can become a knowledge item
- what should remain only a message
- what requires explicit confirmation

## 11. Recommended Directory Shape

Suggested structure:

```text
src/core/tools/
├── __init__.py
├── registry.py
├── toolsets.py
├── wiki_tools.py
├── capture_tools.py
├── agent_state_tools.py
└── maintenance_tools.py
```

The existing `src/core/clawbot/tools.py` should gradually shrink and become a
thin orchestration layer or be removed entirely after the migration.

## 12. Final Recommendation

The biggest lesson from Hermes is:

**do not let tool taxonomy emerge accidentally from service methods.**

Instead:

1. define tool identity explicitly
2. group tools by capability
3. separate content tools from agent-state tools
4. let planner select from a clean and stable vocabulary

For `Cora`, this means the next milestone should not be "add one more tool."

It should be:

**replace the current ad-hoc archive tool model with a small registry-based
wiki tool architecture.**
