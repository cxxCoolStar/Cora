# Cora Hermes-Lite Phase 1 Learning Plan

## Goal

This plan is for a two-track effort:

1. build a Hermes-lite runtime inside Cora
2. learn the design logic behind Hermes modules while implementing

Phase 1 stays intentionally narrow. The only supported product shell is WeChat.

## Phase 1 Scope

In scope:

- prompt layering
- session summary vs long-term memory boundaries
- coarse tool management
- WeChat-driven runtime flow

Out of scope:

- Hermes CLI or TUI
- MCP
- plugins
- subagents
- generic browser or terminal tool families

## Working Method

For each module, answer four questions:

1. how Cora works today
2. what Hermes is optimizing for
3. what Hermes-lite should adopt now
4. what should be deferred and why

The point is not to copy Hermes line by line. The point is to learn the module boundary and keep only the parts that fit Cora's current product shell.

## Module Order

### 1. Prompt Layer

Files:

- [src/core/agent/prompt_builder.py](C:/Users/asta1/ai-project/Cora/src/core/agent/prompt_builder.py)
- [tests/test_agent_runtime.py](C:/Users/asta1/ai-project/Cora/tests/test_agent_runtime.py)
- [website/docs/developer-guide/prompt-assembly.md](C:/Users/asta1/ai-project/hermes-agent/website/docs/developer-guide/prompt-assembly.md)

Learn:

- why Hermes keeps a stable layered system prompt
- why user memory and session summary are different prompt layers
- why tool-use guidance belongs near the top of the prompt

Implement:

- explicit section ordering
- clearer labels for each layer
- stable injection of runtime summary, user memory, skills summary, and structured state

Done when:

- prompt section order is test-covered
- user memory appears separately from session summary
- the prompt reads like a general runtime, not an archive-only bot

### 2. Memory Boundaries

Files:

- [src/core/agent/context_manager.py](C:/Users/asta1/ai-project/Cora/src/core/agent/context_manager.py)
- [src/core/user_memory/store.py](C:/Users/asta1/ai-project/Cora/src/core/user_memory/store.py)
- [tests/test_agent_runtime.py](C:/Users/asta1/ai-project/Cora/tests/test_agent_runtime.py)
- [tests/test_clawbot_api.py](C:/Users/asta1/ai-project/Cora/tests/test_clawbot_api.py)

Learn:

- why session summary is handoff context rather than persistent memory
- why USER.md should only hold stable user facts
- why memory writes should be explicit tool actions

Implement:

- stricter summary wording so it is treated as background context only
- tighter distinction between runtime state, session summary, and USER.md
- tests that protect these boundaries

Done when:

- summary text is clearly marked as temporary reference context
- USER.md remains durable and explicitly separate
- no prompt layer suggests that session summary is long-term memory

### 3. Tool Surface

Files:

- [src/core/tools/registry.py](C:/Users/asta1/ai-project/Cora/src/core/tools/registry.py)
- [src/core/tools/manager.py](C:/Users/asta1/ai-project/Cora/src/core/tools/manager.py)
- [src/core/tools/toolsets.py](C:/Users/asta1/ai-project/Cora/src/core/tools/toolsets.py)
- [src/core/clawbot/tools.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/tools.py)

Learn:

- why Hermes separates registry, grouping, and dispatch
- why the model should see a coarse tool surface rather than product-internal operations
- why execution code should not own prompt policy

Implement:

- keep `archive`, `archive_state`, and `user_memory` as the product-facing core
- treat other tools as support surfaces, not the main mental model
- make toolset selection easy to inspect and test

Done when:

- the tool surface is small and explicit
- prompt wording matches the available coarse tools
- archive still works without leaking too much product logic into the runtime core

### 4. Service and Orchestration Cleanup

Files:

- [src/core/clawbot/service.py](C:/Users/asta1/ai-project/Cora/src/core/clawbot/service.py)
- [src/core/agent/orchestrator.py](C:/Users/asta1/ai-project/Cora/src/core/agent/orchestrator.py)
- [src/core/agent/loop.py](C:/Users/asta1/ai-project/Cora/src/core/agent/loop.py)

Learn:

- why Hermes keeps the outer shell thin and lets the agent loop do the agent work
- why runtime building and prompt building should stay separate
- why side effects should flow through tool execution rather than ad hoc branches

Implement:

- keep WeChat as the only shell
- make service code focus on intake, runtime construction, persistence, and response delivery
- move agent policy toward prompt plus tools, not service conditionals

Done when:

- service responsibilities are easier to explain in one paragraph
- orchestration reads as a runtime pipeline
- WeChat behavior still works after refactors

## First Slice

Start here:

1. tighten prompt section tests
2. tighten session summary boundary tests
3. refactor `prompt_builder.py`
4. run focused tests

This keeps the first implementation slice small while teaching the core Hermes prompt and memory design ideas.
