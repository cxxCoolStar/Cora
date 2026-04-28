# Core Agent V1 Architecture Spec

## 1. Overview

`core` is the first agent developed in this repository. Its V1 goal is to provide a small but correct foundation for a Python-based conversational agent system, with room to grow toward more advanced agent frameworks such as Hermes-style or OpenClaw-style runtimes.

V1 does **not** aim to solve the full agent problem. It focuses on four production-critical capabilities:

1. Multi-turn conversation within a persistent session
2. Basic tool calling through a structured tool interface
3. Persistent storage of conversation and execution events
4. Clear extension points for memory management and channels

The design principle for V1 is:

- Keep the runtime simple
- Keep the abstractions stable
- Delay advanced orchestration until the core loop is reliable

## 2. Goals And Non-Goals

### 2.1 Goals

- Support a single-agent runtime
- Support session-based multi-turn dialogue
- Support model-initiated tool calls with JSON-schema-like parameter validation
- Persist session state, messages, and tool execution traces
- Provide a clean boundary between runtime, model client, tools, memory, and storage
- Make it easy to add API, CLI, and future UI entry points

### 2.2 Non-Goals For V1

- Multi-agent coordination
- Complex planning or task graphs
- Asynchronous distributed execution
- Human approval workflows
- Rich long-term memory retrieval pipelines
- Vector database integration by default
- Production auth / tenancy / RBAC
- Full streaming architecture

## 3. Product Scope

V1 should support the following user experience:

1. A user starts or resumes a conversation session
2. The user sends a message
3. The agent sees the system prompt, session history, and optional memory summary
4. The model either:
   - replies directly, or
   - requests a tool call
5. The runtime executes the tool call
6. The tool result is appended to the conversation
7. The model uses the updated context to produce a final answer
8. All messages and events are persisted

This scope is sufficient for an initial interactive CLI application and can later back an HTTP or websocket API.

## 4. Design Principles

- **Small core**: V1 should be understandable in one sitting
- **Typed boundaries**: interfaces between modules should be explicit and testable
- **Provider isolation**: model-provider SDK logic must not leak into business logic
- **Event visibility**: every important runtime action should be recordable
- **Graceful extensibility**: future memory and channel systems should plug in without rewriting the core loop
- **Deterministic testing**: runtime should support fake model clients and fake tools

## 5. High-Level Architecture

The system is composed of six major layers:

1. **Entry Points**
   - CLI now
   - HTTP API later
   - worker or streaming adapters later
2. **Agent Runtime**
   - orchestrates one user turn end-to-end
3. **Model Layer**
   - wraps LLM providers behind a common interface
4. **Tool Layer**
   - registry, schema, invocation, normalization
5. **Memory Layer**
   - short-term history and optional summary memory
6. **Storage Layer**
   - persistence for sessions, messages, memories, and events

Logical cross-cutting concerns:

- channels
- tracing / events
- configuration
- prompt assembly

## 6. Core Concepts

### 6.1 Agent

`Agent` is the public-facing unit of behavior. It exposes methods such as:

- create or resume a session
- accept a user input
- execute one conversational turn
- return the final assistant response and turn metadata

Responsibilities:

- own configuration for this agent
- delegate execution to the runtime
- expose a simple API to callers

Non-responsibilities:

- direct database logic
- provider-specific SDK logic
- tool implementation details

### 6.2 Session

A `Session` represents one multi-turn conversation context.

Minimum fields:

- `id`
- `agent_name`
- `status`
- `created_at`
- `updated_at`
- `metadata`

Responsibilities:

- identify a conversation thread
- serve as the unit for retrieving history
- link memories and events to a conversation

### 6.3 Message

A `Message` is the canonical conversational unit. All interaction should normalize into this structure.

Minimum roles:

- `system`
- `user`
- `assistant`
- `tool`

Recommended fields:

- `id`
- `session_id`
- `role`
- `channel`
- `content`
- `name`
- `tool_call_id`
- `created_at`
- `metadata`

### 6.4 Tool

A `Tool` is a structured callable capability exposed to the model.

Each tool should define:

- `name`
- `description`
- `input_schema`
- `invoke(args) -> ToolResult`

V1 tools should be:

- synchronous
- local
- explicitly registered
- safe by default

### 6.5 Memory

V1 memory is intentionally minimal and has two scopes:

- **short-term memory**
  - session message history
- **summary memory**
  - compact summary generated from older turns when context grows

Long-term semantic memory is a future extension, not a V1 requirement.

### 6.6 Channel

In V1, a `channel` is a logical tag attached to messages and events rather than a full transport abstraction.

Initial channels:

- `chat`
- `tool`
- `system`
- `memory`
- `event`

This gives us observability and future UI routing without introducing a message bus too early.

## 7. Runtime Flow

### 7.1 Single Turn Execution

The runtime for one user turn should look like this:

1. Load session metadata and recent message history
2. Append the new user message
3. Load relevant memory context
4. Build the model input
5. Ask the model for the next action
6. If the model returns plain assistant text:
   - persist assistant message
   - emit completion event
   - return response
7. If the model returns one or more tool calls:
   - validate tool name and arguments
   - invoke tool
   - persist tool request and tool result messages
   - emit tool execution events
   - call the model again with tool results included
8. Persist the final assistant answer
9. Optionally update summary memory
10. Return the final response and execution metadata

### 7.2 Loop Policy

V1 should support a bounded internal loop.

Recommended defaults:

- max tool rounds per user turn: `3`
- max total tool calls per user turn: `5`
- timeout per tool: configurable

Reasons:

- prevents infinite loops
- simplifies debugging
- provides predictable UX

## 8. Module Boundaries

### 8.1 `agent`

Purpose:

- public entrypoint for application code

Suggested modules:

- `agent.py`
- `runtime.py`
- `loop.py`
- `config.py`

### 8.2 `llm`

Purpose:

- isolate provider-specific model APIs

Suggested modules:

- `base.py`
- `openai_client.py`
- `types.py`

Primary abstraction:

- `ModelClient`

Responsibilities:

- convert internal messages into provider format
- submit tool definitions
- normalize provider output into internal response types

### 8.3 `tools`

Purpose:

- manage tool definitions and invocation

Suggested modules:

- `base.py`
- `registry.py`
- `executor.py`
- `builtin/`

Responsibilities:

- register tools
- lookup tools by name
- validate input arguments
- normalize execution results

### 8.4 `memory`

Purpose:

- manage context compression and recall

Suggested modules:

- `base.py`
- `history.py`
- `summary.py`

Responsibilities:

- fetch recent history
- decide when summarization is needed
- store and load summary memory

### 8.5 `storage`

Purpose:

- persistence and repository access

Suggested modules:

- `base.py`
- `sqlite/`
  - `sessions.py`
  - `messages.py`
  - `memories.py`
  - `events.py`

Responsibilities:

- create/read/update sessions
- append and query messages
- store memory records
- store execution events

### 8.6 `channels`

Purpose:

- centralize logical channel naming and event emission

Suggested modules:

- `types.py`
- `dispatcher.py`

Responsibilities:

- define allowed channels
- normalize event payloads
- provide optional hooks for streaming later

## 9. Canonical Data Models

The exact implementation can use Pydantic models.

### 9.1 Session

```python
class Session(BaseModel):
    id: str
    agent_name: str
    status: Literal["active", "archived"]
    metadata: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
```

### 9.2 Message

```python
class Message(BaseModel):
    id: str
    session_id: str
    role: Literal["system", "user", "assistant", "tool"]
    channel: Literal["chat", "tool", "system", "memory", "event"] = "chat"
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = {}
    created_at: datetime
```

### 9.3 Tool Spec

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
```

### 9.4 Tool Call

```python
class ToolCall(BaseModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
```

### 9.5 Tool Result

```python
class ToolResult(BaseModel):
    success: bool
    content: str
    metadata: dict[str, Any] = {}
    error: str | None = None
```

### 9.6 Model Response

```python
class ModelResponse(BaseModel):
    assistant_text: str | None = None
    tool_calls: list[ToolCall] = []
    raw_response: dict[str, Any] = {}
    usage: dict[str, Any] = {}
```

## 10. Persistence Design

V1 should use SQLite as the default local persistence backend.

### 10.1 Tables

Recommended initial tables:

- `sessions`
- `messages`
- `memories`
- `events`

### 10.2 `sessions`

Fields:

- `id`
- `agent_name`
- `status`
- `metadata_json`
- `created_at`
- `updated_at`

### 10.3 `messages`

Fields:

- `id`
- `session_id`
- `role`
- `channel`
- `content`
- `name`
- `tool_call_id`
- `metadata_json`
- `created_at`

Indexes:

- `(session_id, created_at)`

### 10.4 `memories`

Fields:

- `id`
- `session_id`
- `memory_type`
- `content`
- `metadata_json`
- `created_at`
- `updated_at`

For V1, `memory_type` can start with:

- `summary`

### 10.5 `events`

Fields:

- `id`
- `session_id`
- `event_type`
- `channel`
- `payload_json`
- `created_at`

Examples of event types:

- `turn_started`
- `model_called`
- `tool_requested`
- `tool_completed`
- `turn_completed`
- `turn_failed`

## 11. Prompting Strategy

V1 should assemble prompts from explicit parts rather than hardcoding one large string.

Suggested prompt layers:

1. system instruction
2. tool usage instructions
3. memory summary, if any
4. recent conversation history
5. latest user message

Why this matters:

- easier prompt evolution
- easier debugging
- easier provider migration

System prompt responsibilities:

- define assistant identity as `core`
- explain tool-usage rules
- tell the model to prefer direct answers when no tool is needed
- tell the model to avoid inventing tool results

## 12. Tool Calling Strategy

V1 should assume the model may output:

- direct text
- a single tool call
- multiple tool calls

Implementation recommendation:

- internally support multiple calls
- in product behavior, keep the first version conservative

Practical policy:

- allow multiple tool calls if the provider supports it cleanly
- execute sequentially in V1
- reject unknown tools
- reject invalid arguments with explicit tool error messages

Tool execution contract:

1. runtime resolves tool by name
2. arguments are validated
3. tool is invoked
4. result is normalized into `ToolResult`
5. tool result is added to history as a `tool` role message

## 13. Memory Strategy

### 13.1 Short-Term Memory

Source:

- recent session messages from storage

Policy:

- include the most recent N turns directly
- configurable window size

### 13.2 Summary Memory

Trigger:

- conversation exceeds a token or message threshold

Behavior:

- summarize older messages into a compact factual summary
- store that summary in `memories`
- use that summary in future turns

V1 summary content should prioritize:

- user preferences
- ongoing tasks
- unresolved commitments
- important facts established earlier

### 13.3 Deferred Long-Term Memory

Future versions may add:

- semantic retrieval
- user profile memory
- task memory across sessions
- vector search

These should remain optional extensions, not V1 dependencies.

## 14. Channels Strategy

V1 channels are metadata, not transport infrastructure.

### 14.1 Why Keep Channels In V1

They solve three useful problems early:

- message classification
- event routing and observability
- future compatibility with streaming or UI rendering

### 14.2 Minimal Representation

Messages and events carry a `channel` string.

Examples:

- user/assistant dialogue: `chat`
- tool requests and results: `tool`
- injected memory context: `memory`
- runtime traces: `event`

### 14.3 Deferred Channel Features

Not needed in V1:

- pub/sub infra
- separate transports
- distributed event brokers
- channel-level permissions

## 15. Error Handling

V1 should define predictable failure behavior.

### 15.1 Tool Errors

If a tool fails:

- capture exception details internally
- return a normalized tool failure result
- allow the model to respond to that failure

### 15.2 Model Errors

If the model call fails:

- persist a failure event
- return a typed runtime error to the caller

### 15.3 Validation Errors

If the model emits invalid tool arguments:

- do not crash the turn
- surface a structured tool error result
- allow the next model round to recover

## 16. Observability

V1 should record enough execution state to debug runtime behavior.

Recommended observability artifacts:

- structured logs
- persisted runtime events
- tool call ids
- session ids
- optional raw model payload snapshots behind config

This is especially useful when tool calling fails or the model gets stuck in loops.

## 17. Configuration

V1 should centralize configuration in a typed settings object.

Recommended config groups:

- model provider and model name
- API keys / endpoints
- database path
- tool timeouts
- max history window
- summarization thresholds
- debug flags

Suggested implementation:

- Pydantic settings or environment-driven config module

## 18. Suggested Repository Layout

```text
docs/
  v1-architecture-spec.md

src/core/
  agent/
    agent.py
    runtime.py
    loop.py
    config.py
  llm/
    base.py
    openai_client.py
    types.py
  tools/
    base.py
    registry.py
    executor.py
    builtin/
      echo.py
      get_time.py
      calculator.py
  memory/
    base.py
    history.py
    summary.py
  storage/
    base.py
    sqlite/
      sessions.py
      messages.py
      memories.py
      events.py
  channels/
    types.py
    dispatcher.py
  prompts/
    system.py
  schemas/
    session.py
    message.py
    tool.py
  cli/
    main.py

tests/
  agent/
  tools/
  memory/
  storage/
```

## 19. Sequence Diagram

```text
User
  -> CLI/API
  -> Agent
  -> Runtime
  -> Storage(load history)
  -> Memory(load summary)
  -> ModelClient(call model)
  -> ToolRegistry(resolve tool, if needed)
  -> Tool(invoke)
  -> Storage(save tool messages/events)
  -> ModelClient(call model again)
  -> Storage(save final assistant message)
  -> Caller(return final response)
```

## 20. V1 Milestones

### Milestone 1: Runtime Skeleton

Deliverables:

- typed message/session/tool models
- model client interface
- simple agent runtime without persistence
- fake model client for tests

### Milestone 2: Tool Calling

Deliverables:

- tool base class
- tool registry
- 2 to 3 built-in demo tools
- bounded tool-calling loop

### Milestone 3: Persistence

Deliverables:

- SQLite repositories
- session/message/event persistence
- session resume support

### Milestone 4: Memory

Deliverables:

- recent-history loader
- summary memory storage
- basic summarization trigger

### Milestone 5: Entry Point

Deliverables:

- CLI chat interface
- config loading
- debug logging

## 21. Testing Strategy

V1 should ship with tests from the beginning.

Recommended tests:

- message and tool schema validation
- runtime direct-answer path
- runtime single-tool-call path
- invalid tool call recovery
- session persistence and reload
- summary memory insertion behavior

Prefer:

- fake model client for deterministic unit tests
- isolated SQLite temp database for storage tests

## 22. Open Design Decisions

These items can stay open until implementation starts:

1. Whether to use raw `sqlite3` or SQLAlchemy in V1
2. Which provider to target first for tool calling
3. Whether CLI should support streaming in the first milestone
4. Whether summarization is model-driven or rule-driven in V1
5. Whether tool results should keep raw structured payloads in addition to text

## 23. Recommended Implementation Decision For Now

To reduce time-to-first-working-agent, the current recommendation is:

- Python `3.11+`
- Pydantic for schemas
- SQLite for storage
- Typer for CLI
- `httpx` for provider communication
- first provider adapter built for a single model API
- only a few builtin tools in V1

## 24. Summary

`core` V1 should be a **single-agent, session-based, tool-capable conversational runtime** with minimal but durable architecture.

The most important thing to preserve is not feature count, but clean boundaries:

- runtime owns orchestration
- model client owns provider adaptation
- tool layer owns capability execution
- memory layer owns context compression
- storage layer owns persistence

If these boundaries hold, V2 can add richer memory, streaming channels, HTTP services, and more advanced agent behaviors without reworking the foundation.
