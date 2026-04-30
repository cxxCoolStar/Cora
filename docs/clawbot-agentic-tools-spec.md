# ClawBot Agentic Tooling Spec

## 1. Overview

This document defines the first agentic tool model for `ClawBot`.

It is intentionally scoped to the current project reality:

- `FastAPI` app with `ClawBotService`
- persisted `sessions`, `messages`, `items`, and `item_chunks`
- ingestion for text, links, and files
- retrieval over item metadata and chunk text
- lightweight LLM-based intent routing

The goal is not to introduce a generic "tool-use platform".

The goal is to make `ClawBot` behave like a reliable **archive agent** that can:

- decide whether the user wants to save, search, expand, summarize, or clarify
- choose the right execution strategy
- keep track of the current conversation focus
- avoid using content search when direct document lookup is more appropriate

## 2. Why Agentic Tools

The current system already has the beginnings of tool behavior:

- ingestion behaves like a `save_*` action
- retrieval behaves like a `search_*` action
- summary/organization behaves like a `summarize_*` action

However, the decision flow is still too tightly coupled to top-level intents like:

- `capture`
- `retrieve`
- `organize`
- `chat`

That model is too coarse for real archive conversations.

Examples:

- "帮我找一下售前报价 Agent 系统"
  This is not just `retrieve`. It is `search_items(query=...)`.
- "这里面写了什么"
  This is not generic `organize`. It is `get_item(target=current_focus, mode=summary)`.
- "把刚才那个文件全文给我"
  This is not search at all. It is `get_item(target=working_set reference, mode=full_text)`.

So the next step is:

1. keep a lightweight intent layer
2. let an LLM-backed planner choose a constrained tool
3. execute that tool through normal backend code

## 3. Core Design Principle

LLM chooses the tool.

Backend owns execution.

That means:

- the LLM can choose from a small allowed tool set
- the LLM must emit structured arguments
- the backend validates arguments
- the backend resolves IDs and executes repositories/services
- the LLM must never directly control SQL, filesystem paths, or arbitrary code execution

## 4. Conversation State Model

Tool choice depends heavily on short-term session state.

`ClawBot` should maintain a compact conversation working memory in assistant message metadata or a future dedicated state table.

Recommended state shape:

```json
{
  "working_set": [
    {
      "item_id": "item_123",
      "title": "售前报价Agent-面试宝典",
      "summary": "售前报价Agent系统项目面试问答...",
      "saved_at": "2026-04-29T17:20:00",
      "rank": 1
    }
  ],
  "focus_item_id": "item_123",
  "last_action": "search_items"
}
```

### 4.1 `working_set`

`working_set` is the recent set of retrieval candidates currently "in play" in the conversation.

Use cases:

- "第二个文件给我看看"
- "上面那个"
- "刚才搜到的那个报价文档"

Recommended size:

- keep the latest `3-5` candidate items

### 4.2 `focus_item_id`

`focus_item_id` is the document currently being discussed.

Use cases:

- "这里面写了什么"
- "展开讲讲"
- "给我全文"

### 4.3 `last_action`

Useful for follow-up interpretation and debugging.

Examples:

- after `search_items`, a short follow-up likely refers to a search result
- after `save_file`, a short follow-up may ask for summary

## 5. Tool Categories

The V1 archive agent should expose a small but complete tool set.

Tools should be grouped into four categories:

1. ingestion tools
2. retrieval tools
3. organization tools
4. state / clarification tools

## 6. V1 Tool Set

The recommended V1 agentic tool set is:

1. `save_text_or_link`
2. `save_file`
3. `search_items`
4. `get_item`
5. `summarize_item`
6. `clarify_reference`

This is enough to support:

- saving text
- saving links
- saving files
- keyword / semantic search
- direct document expansion
- follow-up clarification

It is intentionally small.

Do not add `delete`, `bulk_compare`, `cross-session analytics`, or arbitrary metadata editing in the first agentic milestone.

## 7. Tool Definitions

### 7.1 `save_text_or_link`

Purpose:

- persist direct text notes
- persist URL submissions

When to use:

- the user sends content that should be stored
- the user explicitly says save/store/remember
- the user sends a standalone link and wants it archived

Input schema:

```json
{
  "tool": "save_text_or_link",
  "arguments": {
    "text": "string",
    "force_type": "auto | text | link"
  }
}
```

Execution behavior:

- `force_type=auto` lets backend detect whether input is a URL
- routes to current `IngestionService.ingest(...)`
- returns saved `item_id`, `item_type`, `title`, `summary`

Output schema:

```json
{
  "item_id": "string",
  "item_type": "text_note | link",
  "title": "string",
  "summary": "string"
}
```

### 7.2 `save_file`

Purpose:

- persist uploaded files and parse them when possible

When to use:

- the request contains a file upload

Input schema:

```json
{
  "tool": "save_file",
  "arguments": {
    "upload_token": "string"
  }
}
```

Execution behavior:

- upload token is backend-provided, not LLM-generated
- routes to current file ingestion path
- supports parser-based extraction and file metadata preservation

Output schema:

```json
{
  "item_id": "string",
  "item_type": "document | file_upload",
  "title": "string",
  "summary": "string",
  "parse_status": "parsed | failed | unsupported"
}
```

### 7.3 `search_items`

Purpose:

- find relevant saved materials from natural language

When to use:

- user asks to find, search, locate, recall, or retrieve prior content

Input schema:

```json
{
  "tool": "search_items",
  "arguments": {
    "query": "string",
    "top_k": 3,
    "filters": {
      "file_type": "optional string",
      "title_keyword": "optional string",
      "saved_date": "optional YYYY-MM-DD",
      "tags": ["optional", "string"]
    }
  }
}
```

Execution behavior:

- routes to retrieval pipeline
- may use query rewrite
- scores items/chunks
- returns top candidates
- updates `working_set`
- sets `focus_item_id` to top candidate when confidence is sufficiently high

Output schema:

```json
{
  "candidates": [
    {
      "item_id": "string",
      "title": "string",
      "summary": "string",
      "score": 12,
      "matched_snippet": "string"
    }
  ],
  "selected_item_id": "optional string"
}
```

### 7.4 `get_item`

Purpose:

- fetch one concrete document instead of doing another search

This is the most important tool for follow-up questions.

When to use:

- "这里面写了什么"
- "上面那个文件"
- "给我全文"
- "第二个结果给我看看"

Input schema:

```json
{
  "tool": "get_item",
  "arguments": {
    "target": {
      "type": "item_id | focus_item | working_set_rank",
      "value": "string or integer"
    },
    "mode": "summary | full_text | key_points"
  }
}
```

Execution behavior:

- backend resolves target safely
- `focus_item` reads from state
- `working_set_rank=2` resolves to the second working-set item
- does not run retrieval unless target resolution fails

Output schema:

```json
{
  "item_id": "string",
  "title": "string",
  "mode": "summary | full_text | key_points",
  "content": "string",
  "locator_hint": "optional string"
}
```

### 7.5 `summarize_item`

Purpose:

- transform one item into a more useful summary

When to use:

- "总结一下"
- "这里讲了什么"
- "帮我提炼重点"

Input schema:

```json
{
  "tool": "summarize_item",
  "arguments": {
    "target": {
      "type": "item_id | focus_item | working_set_rank",
      "value": "string or integer"
    },
    "style": "brief | structured | interview_notes"
  }
}
```

Execution behavior:

- resolves an item first
- uses stored text as source of truth
- can reuse existing summary for `brief`
- can call LLM summarization for `structured`

Output schema:

```json
{
  "item_id": "string",
  "title": "string",
  "summary": "string"
}
```

### 7.6 `clarify_reference`

Purpose:

- ask the user to disambiguate when the system cannot safely choose one target

When to use:

- multiple candidates fit the query
- user uses ambiguous reference terms like:
  - "这个"
  - "那个"
  - "上面的"
  - "第二个"
  - "刚才那个"

Input schema:

```json
{
  "tool": "clarify_reference",
  "arguments": {
    "question": "string",
    "candidates": [
      {
        "item_id": "string",
        "label": "string"
      }
    ]
  }
}
```

Execution behavior:

- creates pending clarification state
- does not execute a content action yet

Output schema:

```json
{
  "status": "pending_clarification",
  "question": "string"
}
```

## 8. Planner Contract

The planner should be the only component that lets the LLM choose a tool.

Suggested planner output:

```json
{
  "tool": "search_items",
  "arguments": {
    "query": "售前报价Agent系统",
    "top_k": 3
  },
  "reason": "The user is asking to locate previously saved material."
}
```

### 8.1 Planner constraints

The planner must:

- choose exactly one tool
- produce valid JSON
- only use tools from the allowed list
- avoid inventing item IDs
- prefer `get_item` over `search_items` for follow-up reference queries
- prefer `clarify_reference` when multiple candidates are plausible

### 8.2 Planner inputs

The planner should receive:

- current user message
- whether there is an uploaded file
- recent messages
- current `working_set`
- `focus_item_id`

### 8.3 Planner prompt rules

The prompt should teach these behaviors explicitly:

- if the user sends new content, save it
- if the user asks to find older content, search
- if the user asks about "this/that/the above file", use current state first
- do not search again when the target is already identified
- ask clarification instead of guessing between multiple likely targets

## 9. State Update Rules

Tools should update state in a predictable way.

### 9.1 After `search_items`

- set `working_set` to returned candidates
- set `focus_item_id` to top candidate when high confidence
- set `last_action=search_items`

### 9.2 After `get_item`

- preserve `working_set`
- set `focus_item_id` to the resolved item
- set `last_action=get_item`

### 9.3 After `summarize_item`

- preserve `working_set`
- preserve or update `focus_item_id`
- set `last_action=summarize_item`

### 9.4 After `save_text_or_link` or `save_file`

- clear `working_set` only if it no longer reflects the current conversation
- optionally set `focus_item_id` to the new saved item if the user immediately discusses it
- set `last_action=save_*`

## 10. Tool Selection Strategy

### 10.1 Search strategy

Use `search_items` when:

- the user refers to something not yet identified in the current state
- the query contains real content keywords
- the user is asking for prior saved material in general

Examples:

- "帮我找一下售前报价Agent系统"
- "我之前保存的面试题呢"

### 10.2 Direct lookup strategy

Use `get_item` when:

- the target is already present in focus or working set
- the user asks for expansion, full text, or explanation

Examples:

- "这里面写了什么"
- "给我全文"
- "第二个文档看看"

### 10.3 Clarification strategy

Use `clarify_reference` when:

- the user uses a vague reference
- there are multiple plausible targets
- guessing would be unsafe

Example:

- "上面那个展开一下"
  when two recent results are both plausible

## 11. Mapping To Current Code

This design can be implemented incrementally on top of the current project.

### 11.1 New module

Recommended new module:

```text
src/core/clawbot/planner.py
```

Responsibilities:

- define planner schema
- invoke LLM for tool choice
- validate the selected tool

### 11.2 New execution layer

Recommended new module:

```text
src/core/clawbot/tools.py
```

Responsibilities:

- implement backend tool handlers
- call `IngestionService`, `RetrievalService`, and repositories
- update state metadata

### 11.3 Service integration

`ClawBotService.ingest()` should evolve from:

- `intent -> hardcoded branch`

to:

- `intent -> planner -> tool execution -> response`

The existing intent router remains useful as a coarse first pass:

- file upload -> likely save
- greeting -> chat
- unresolved ambiguity -> clarify

But the final action should come from the planner/tool layer.

## 12. Recommended Execution Flow

Recommended turn flow:

1. receive user input
2. load session state (`working_set`, `focus_item_id`, pending clarification)
3. run coarse intent router
4. if intent is `chat`, respond directly
5. otherwise run planner
6. validate chosen tool + arguments
7. execute tool
8. update state
9. store assistant message with:
   - `decision`
   - `tool`
   - `tool_arguments`
   - `context_state`
10. return user-facing reply

## 13. Debugging Requirements

Because tool choice will become more important, the debug page should eventually show:

- planner-selected tool
- tool arguments
- resulting working set
- current focus item
- whether a clarification state is pending

This will make behavior much easier to tune.

## 14. Non-Goals For This Milestone

The first agentic tooling milestone should not yet include:

- arbitrary web search
- calendar / external app integration
- code execution tools
- auto-delete or destructive archive mutations
- multi-step autonomous planning across many turns

This milestone is about turning `ClawBot` into a robust archive agent, not a broad autonomous assistant.

## 15. Final Recommendation

The right next implementation step is:

1. add `working_set` + `focus_item_id` state
2. add a planner that chooses one constrained tool
3. implement the six V1 tools in backend code
4. shift follow-up questions from "search again" to direct `get_item` / `summarize_item`
5. add clarification when references are ambiguous

If this is done well, `ClawBot` will stop feeling like a chat wrapper around search and start feeling like a true archive agent.

