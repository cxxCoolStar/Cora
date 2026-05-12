# Hermes-Lite Learning Notes: Prompt And Memory Boundaries

## Why write this down

This document turns the ongoing Cora refactor into explicit learning material.

The goal is not only to make Cora more like a Hermes-lite runtime.
The goal is also to preserve the design reasoning, so Cora can later serve as a compact learning sample for understanding Hermes module boundaries.

Current focus:

- prompt layering
- session summary boundaries
- long-term user memory boundaries
- why these concerns should not be mixed together

Related files:

- [src/core/agent/prompt_builder.py](C:/Users/asta1/ai-project/Cora/src/core/agent/prompt_builder.py)
- [src/core/agent/context_manager.py](C:/Users/asta1/ai-project/Cora/src/core/agent/context_manager.py)
- [src/core/user_memory/store.py](C:/Users/asta1/ai-project/Cora/src/core/user_memory/store.py)
- [tests/test_agent_runtime.py](C:/Users/asta1/ai-project/Cora/tests/test_agent_runtime.py)
- [website/docs/developer-guide/prompt-assembly.md](C:/Users/asta1/ai-project/hermes-agent/website/docs/developer-guide/prompt-assembly.md)

## The first important idea

Hermes does not treat the system prompt as a block of product copy.
It treats the prompt as runtime structure.

That difference matters.

If the system prompt is only treated as wording, it becomes tempting to keep appending mixed instructions until the model "usually does the right thing".
That often works at first, but the runtime becomes hard to reason about.

Hermes takes a different path:

- each prompt layer has a distinct responsibility
- layers are ordered intentionally
- short-lived context and durable context are kept separate

This improves three things:

- stability
- debuggability
- memory correctness

## Why prompt layers exist

Different information inside an agent runtime has different lifetimes and different meanings.

Examples:

- agent identity is highly stable
- tool-use discipline is stable but operational
- session summary is temporary
- user memory is durable
- runtime state is only for the current turn or near-current turn

If these are mixed together, the model gets a blurred view of what is:

- an instruction
- a durable fact
- temporary context
- current execution state

That blur causes bad behavior over time.

## The Hermes-lite prompt shape used in Cora

The current Phase 1 target shape is:

1. agent identity
2. execution guidance
3. memory guidance
4. skills guidance
5. platform hints
6. runtime summary
7. user memory snapshot
8. shared skills summary
9. upload hint
10. structured conversation state

This is implemented in [src/core/agent/prompt_builder.py](C:/Users/asta1/ai-project/Cora/src/core/agent/prompt_builder.py).

### Why this order

The rough logic is:

- first define who the agent is
- then define how it should behave
- then define how memory should be treated
- then inject situational context

This avoids a common problem where transient details are placed too early and begin to feel like core operating instructions.

## Why session summary is not user memory

This is one of the most important boundaries in Hermes-style design.

Session summary answers:

"What does another assistant need to know to continue this conversation?"

User memory answers:

"What stable facts about the user should still matter later, across tasks or sessions?"

These are not the same.

### Session summary

Session summary is:

- temporary
- compressed
- handoff-oriented
- allowed to be lossy

Session summary should contain things like:

- the current unresolved ask
- recent decisions
- blockers
- concrete references that still matter in this session

### User memory

User memory is:

- durable
- selective
- cross-session
- meant to survive beyond the current task

User memory should contain things like:

- language preference
- recurring correction
- stable work habit
- persistent personal reference fact that the user explicitly wants remembered

### Why mixing them is bad

If session summary is treated like long-term memory:

- temporary tasks get remembered forever
- the memory file becomes noisy
- future prompts are polluted by stale context

If user memory is treated like session summary:

- durable preferences disappear too easily
- the runtime loses continuity across sessions

## Why tool results are not the same as history

Another useful Hermes design lesson is that not all runtime information belongs in plain conversation history.

There are at least four different things in play:

- raw conversation history
- tool execution results
- session summary
- long-term user memory

These should not be treated as interchangeable.

### Raw conversation history

This is the original exchange between user and assistant.

### Tool results

These are execution facts.
They are closer to evidence than to ordinary dialogue.

If tool results are flattened into ordinary chat text without structure, the model may confuse:

- a planned action
- a completed action
- a user statement
- a system fact

### Session summary

This is a compressed handoff layer, not a first-class replacement for raw history.

### User memory

This is curated durable knowledge, not a side effect of chat logging.

## Why Hermes does not put everything into message history

The easy design is to store everything as messages and let the model sort it out.

Hermes avoids that because message history would then contain mixed semantic types:

- instructions
- actions
- evidence
- temporary state
- durable facts

Once mixed together, the runtime becomes harder to control.

The better model is:

- history keeps the conversation
- summary compresses older context
- memory stores durable user facts
- runtime state carries current operational state

This is the model Cora should gradually move toward.

## How this maps to Cora

Current mapping:

- conversation history: `MessageRepository`
- session handoff summary: `SessionSummaryRepository`
- long-term user memory: `user-memory/USER.md`
- current operational state: `ConversationRuntimeState`

This structure is smaller than Hermes, but it follows the same design instinct:

- keep different information types in different containers
- make their purposes explicit
- avoid letting service code blur those boundaries

## What changed in Phase 1

The first Phase 1 implementation slice made these changes:

- prompt sections were renamed into clearer Hermes-lite layers
- user memory is now explicitly labeled as a snapshot
- session summary wording now explicitly says it is temporary context, not long-term memory
- tests now verify prompt section order and memory boundary wording

Relevant tests:

- [tests/test_agent_runtime.py](C:/Users/asta1/ai-project/Cora/tests/test_agent_runtime.py)

## What to keep learning next

After prompt and memory boundaries, the next learning question is:

"What belongs in the service layer, and what should belong in runtime, tools, or memory systems instead?"

That question matters because business-shaped agents often accumulate too much intelligence inside service code.
Hermes is valuable partly because it resists that tendency through stronger module boundaries.

## Practical takeaway

The most important sentence in this note is:

Hermes does not write prompts as copy.
It writes prompts as runtime structure.

Once that idea clicks, the next refactor decisions become much easier:

- summary is not memory
- memory is not history
- runtime state is not summary
- tool results are not ordinary conversation text
