# Cora Mini-Hermes Phase 1

## Goal

Phase 1 turns Cora into a clearer Hermes-lite runtime without replacing the current WeChat and archive product shell.

The purpose of this phase is to improve the agent core, not to rebuild the full Hermes platform.

## In Scope

Phase 1 includes:

- a layered system prompt structure
- explicit long-term memory guidance around `user-memory/USER.md`
- clearer separation between session summary and long-term user memory
- a more general tool surface that still preserves archive as a first-class capability
- minimal developer-facing documentation for follow-up phases

## Out Of Scope

Phase 1 does not include:

- Hermes CLI or TUI migration
- MCP or plugin architecture
- external memory providers
- subagent delegation
- terminal/browser/web/file general-purpose tool families
- full session search across historical conversations
- replacing Cora's current channel shell or archive storage model

## Architectural Direction

The target layering for Cora after Phase 1 is:

`channel shell -> runtime state -> prompt builder -> main tool loop -> coarse toolsets -> archive/memory capabilities`

This differs from current Cora mainly in one way:

- the agent should no longer feel like an archive-specific assistant with a few add-ons

It should feel like:

- a general assistant runtime that currently happens to expose archive and user-memory capabilities

## Prompt Design

Phase 1 prompt structure should follow a Hermes-lite shape:

1. Agent identity
2. Execution discipline
3. Long-term memory guidance
4. Skills guidance
5. Runtime state summary
6. User memory snapshot
7. Shared skills summary
8. Upload hint
9. Structured conversation state block

Important boundaries:

- `USER.md` is durable user memory
- session summary is temporary turn-compression context
- runtime state is operational turn state

These should remain distinct in both prompt wording and implementation.

## Memory Boundary

Long-term user memory should remain file-backed for now:

- `user-memory/USER.md`

Allowed contents:

- user preferences
- recurring corrections
- stable habits
- easily forgotten but durable personal facts

Disallowed contents:

- temporary task progress
- one-off requests
- conversational filler
- speculative health or identity inference

## Tool Surface Direction

Phase 1 keeps current coarse tools but reframes them:

- `archive`
- `archive_state`
- `user_memory`

This is the minimum viable Hermes-lite surface for Cora.

Later phases may add more generic toolsets such as:

- `file`
- `web`
- `terminal`
- `skills_manage`

But those are intentionally deferred.

## Phase 2 Preview

If Phase 1 is stable, Phase 2 should focus on:

- planner de-biasing away from archive-first assumptions
- better skill selection and conditional loading
- a lightweight memory/session recall distinction
- cleaning product-specific wording out of the core planner and prompt layer

## Acceptance Criteria

Phase 1 is successful when:

- the system prompt is clearly layered and test-covered
- user memory is injected consistently and separately from runtime summary
- the model is explicitly guided to use `user_memory` for stable user facts
- current archive flows still work
- no Hermes-heavy subsystems are introduced prematurely
