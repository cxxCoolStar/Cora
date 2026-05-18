# Cora Evaluation Module Design

## Purpose

This document defines the intended design of Cora's evaluation subsystem.

It is not a note about one helper script. It is a design for a first-class agent evaluation module that can grow with the project.

The goal is to make Cora measurable in a repeatable, engineering-friendly way after every meaningful change to:

- prompts
- tools
- skills
- memory behavior
- retrieval behavior
- runtime orchestration
- model choice

This document is written as a practical architecture guide for future implementation and refactoring.

## North Star

The evaluation subsystem should answer four questions reliably:

1. What agent behaviors do we care about?
2. How do we execute those behaviors in a controlled environment?
3. How do we judge success or failure without relying only on vibe?
4. How do we compare results across runs, models, prompts, and code revisions?

The intended shape is:

`case definition -> isolated eval runtime -> judges -> report -> comparison`

Not:

`one-off script -> string contains -> pass/fail guess`

## Why Cora Needs A Real Eval Subsystem

Cora is not just a chat wrapper. It is an agent with:

- a loop
- tool use
- file and archive state mutation
- retrieval behavior
- delivery behavior
- user memory behavior
- multi-turn clarification

This means ordinary unit tests are necessary but insufficient.

Unit tests can verify:

- parser correctness
- utility functions
- repository behavior
- tool handler edge cases

But they cannot fully answer:

- did the model choose the right tool?
- did the runtime ask for clarification at the right moment?
- did retrieval behavior improve or regress?
- did a prompt change make delivery behavior worse?
- did the system mutate state correctly after a realistic task?

The eval subsystem should exist to measure exactly these agent-level behaviors.

## Non-Goals

The evaluation subsystem should not try to solve everything at once.

It is not intended to:

- replace unit tests
- replace integration tests for individual repositories or services
- be a full production observability platform
- require every case to use a real external model
- reduce all agent behavior to one scalar score

## Design Principles

### 1. Evaluate Behavior, Not Just Text

Agent quality should not be judged only by whether a reply contains a phrase.

The primary evaluation target is behavior:

- tool selection
- action choice
- clarification choice
- retrieval outcome
- state mutation
- final response usefulness

### 2. Prefer Stable Judges First

Rule-based and state-based assertions should be the default.

LLM-based judging is useful, but it should be supplementary because it is:

- less stable
- harder to debug
- more expensive
- harder to compare across runs

### 3. Isolate Every Eval Run

Each case should run in an isolated workspace and isolated state boundary so that:

- cases do not pollute each other
- repeated runs are reproducible
- failures can be debugged cleanly

### 4. Separate Capability Measurement From Regression Protection

There should be a clear distinction between:

- regression evals that must stay stable and fast
- capability evals that measure real-world performance

### 5. Treat Eval Artifacts As Durable Engineering Output

Eval output should be inspectable and comparable later.

That means reports need durable structure, not just terminal text.

## Eval Layers

The evaluation module should be organized into four layers.

### Layer 1: Case Layer

This layer defines what to test.

A case should represent one meaningful task or multi-turn interaction.

Each case should describe:

- the user goal
- setup requirements
- the turn sequence
- expected outcomes
- which judge types apply

This layer is the product-facing truth of the eval subsystem.

### Layer 2: Runtime Layer

This layer defines how to run a case.

It is responsible for:

- creating an isolated workspace
- provisioning database, archive, files, and memory state
- selecting the execution mode
- creating sessions
- replaying steps
- capturing traces, timing, and state snapshots

This layer should be independent from CLI presentation concerns.

### Layer 3: Judge Layer

This layer defines how to score a case or step.

It should support multiple judge families:

- rule judges
- state judges
- LLM judges

The same case may use one or more judge families.

### Layer 4: Report Layer

This layer defines how results are stored, presented, and compared.

It should support:

- human-readable terminal summary
- structured JSON report
- diff/comparison between runs
- failure bundles for debugging

## Eval Types

The subsystem should support at least three eval types.

### 1. Regression Evals

Purpose:

- catch breakage after code or prompt changes

Characteristics:

- relatively small
- stable
- cheap to run
- mostly deterministic judges

Examples:

- user memory add/read round-trip
- delivery request must trigger tool flow
- ambiguous retrieval must not trigger delete

### 2. Capability Evals

Purpose:

- measure how strong the agent actually is at real tasks

Characteristics:

- often use real models
- may be less deterministic
- judge both behavior and usefulness

Examples:

- retrieve a document from content description alone
- choose the correct topic for a file
- ask a good clarification when multiple candidates exist

### 3. Safety Evals

Purpose:

- verify that the agent does not take unsafe or incorrect actions

Characteristics:

- emphasis on forbidden actions and correct fallback behavior

Examples:

- do not delete on vague request
- do not claim file delivery succeeded when it did not
- do not invent a found file when retrieval returned none

## Recommended Directory Structure

The evaluation subsystem should evolve toward this structure:

```text
evals/
  cases/
    regression/
    capability/
    safety/
  fixtures/
    archives/
    memory/
    sessions/
  judges/
    rules.py
    state.py
    llm.py
  runner/
    loader.py
    runtime.py
    executor.py
    report.py
    compare.py
    models.py
  baselines/
    latest.json
    history/
```

### Rationale

- `cases/`
  Stores the task definitions.

- `fixtures/`
  Stores reusable archive state, memory state, and session seeds.

- `judges/`
  Keeps judging logic modular instead of embedding everything in one runner file.

- `runner/`
  Separates loading, execution, reporting, and comparison concerns.

- `baselines/`
  Allows durable run history and comparison workflows.

## Case Schema

Each case should be structured enough to support setup, multi-turn execution, and multiple judge types.

The target schema is:

```json
{
  "id": "retrieve_resume_with_clarification",
  "type": "capability",
  "tags": ["retrieve", "clarify", "wechat"],
  "goal": "When multiple resume candidates exist, the agent should ask a clarification question before delivery.",
  "setup": {
    "archive_fixture": "resume_multiple_candidates",
    "user_memory_fixture": "default",
    "session_fixture": null,
    "channel": "wechat",
    "model_mode": "live"
  },
  "steps": [
    {
      "label": "request delivery",
      "input": {
        "text": "把之前那个简历发给我"
      },
      "expect": {
        "action": null,
        "disposition": "clarify",
        "tool_any": ["skill_run"],
        "tool_none": ["delete_item"],
        "judges": ["rule", "state"]
      }
    },
    {
      "label": "pick candidate",
      "input": {
        "text": "第一份"
      },
      "expect": {
        "action": "deliver",
        "tool_any": ["skill_run"],
        "judges": ["rule", "state"]
      }
    }
  ]
}
```

### Required Fields

- `id`
- `type`
- `goal`
- `steps`

### Strongly Recommended Fields

- `tags`
- `setup`
- `label` on each step

### Setup Section

The `setup` block should describe everything needed before turn execution:

- archive fixture
- user memory fixture
- seeded session history
- channel context
- model mode

The goal is to avoid hiding test semantics inside Python code.

## Judge Schema

Each step should declare which judge families apply.

The intended judge families are:

### Rule Judge

Checks observable behavior from the turn result and trace.

Typical assertions:

- `status`
- `disposition`
- `action`
- `tool_any`
- `tool_all`
- `tool_none`
- `reply_contains_any`
- `reply_contains_all`
- `reply_not_contains`
- `max_steps`

Rule judges are the first line of defense for regression testing.

### State Judge

Checks the resulting persistent or runtime state.

Typical assertions:

- item count increased
- archive record appended
- item metadata exists
- user memory entry added
- pending state created or cleared
- resolved file path exists

This matters because many important failures do not show up in reply text alone.

### LLM Judge

Checks semantic quality where explicit rule checks are too weak.

Examples:

- was the clarification question useful?
- did the answer explain the failure honestly?
- did the retrieval summary sound grounded and not fabricated?

LLM judge outputs should be treated as annotations or secondary scores unless a case is explicitly marked as LLM-judged.

## Runtime Design

The runtime layer should support multiple execution modes.

### 1. Stub Mode

Purpose:

- verify runner wiring
- verify deterministic tool paths
- validate regression plumbing without network dependence

This mode should use a fake or scripted model client.

### 2. Live Mode

Purpose:

- evaluate real behavior against the active model configuration

This mode should use the real configured model client and real agent loop.

### 3. Replay Mode

Purpose:

- replay a stored trajectory or stored model outputs
- compare runtime changes without model noise

This mode is especially useful for debugging prompt and tool execution differences.

## Isolation Strategy

Each case execution should create an isolated sandbox containing:

- a dedicated SQLite database
- a dedicated files directory
- a dedicated archive root
- a dedicated user memory file

This allows:

- case independence
- easier debugging
- reproducible reruns

The runtime should also capture pre-run and post-run snapshots of important state.

## Core Metrics

The reporting system should compute a standard metric set across runs.

### Required Aggregate Metrics

- `case_pass_rate`
- `step_pass_rate`
- `avg_case_latency_seconds`
- `avg_step_latency_seconds`
- `avg_tool_calls_per_step`

### Agent Behavior Metrics

- `tool_selection_accuracy`
- `clarification_rate`
- `clarification_precision`
- `retrieval_success_rate`
- `delivery_success_rate`
- `memory_roundtrip_success_rate`

### Safety Metrics

- `unsafe_action_rate`
- `hallucinated_success_rate`
- `forbidden_tool_invocation_rate`

The exact formulas can evolve, but the category boundaries should stay stable.

## Failure Taxonomy

Every failed step should be assigned one primary failure category.

Recommended categories:

- `routing_failure`
- `tool_failure`
- `retrieval_failure`
- `clarification_failure`
- `memory_failure`
- `state_mutation_failure`
- `response_quality_failure`
- `safety_failure`
- `infrastructure_failure`

This taxonomy is important because it lets future comparisons answer not just "did quality drop?" but "what kind of quality dropped?"

## Report Design

Every eval run should produce three outputs.

### 1. Terminal Summary

Purpose:

- quick developer feedback

It should include:

- total cases
- pass/fail counts
- total steps
- slowest cases
- grouped failure counts by category

### 2. JSON Report

Purpose:

- durable machine-readable result storage

It should include:

- run metadata
- configuration summary
- per-case results
- per-step results
- trace metadata
- metrics
- failure categories

### 3. Failure Bundle

Purpose:

- deep debugging of one failed case

Each failed case bundle should include:

- case definition
- actual inputs
- final replies
- tool trace
- sanitized runtime trace
- relevant state snapshot
- judge failures

## CLI Design

The evaluation subsystem should eventually expose at least these commands.

### `eval-run`

Run one or more case groups.

Suggested options:

- `--cases-dir`
- `--type regression|capability|safety`
- `--mode stub|live|replay`
- `--tags`
- `--report-path`
- `--fail-fast`

### `eval-compare`

Compare two prior reports.

Suggested use cases:

- prompt A vs prompt B
- model A vs model B
- current branch vs previous baseline

### `eval-show`

Inspect one report or one failed case in human-readable form.

## Recommended Implementation Phases

The implementation should proceed in phases rather than trying to land the full design at once.

### Phase 1: Stable Regression Foundation

Deliver:

- isolated runner
- case loader
- rule judge
- JSON report
- terminal summary

Target outcome:

- every meaningful runtime change can be checked locally

### Phase 2: State-Aware Evaluation

Deliver:

- fixtures
- state judges
- state snapshots
- failure taxonomy

Target outcome:

- eval can verify actual persistence and archive effects

### Phase 3: Live Capability Benchmarks

Deliver:

- live-model grouped cases
- baseline comparison
- richer metrics

Target outcome:

- Cora quality can be measured across versions and models

### Phase 4: Semantic And Comparative Evaluation

Deliver:

- LLM judges where useful
- report comparison tooling
- trend tracking

Target outcome:

- changes can be evaluated not just for breakage but for quality movement

## Recommended Priority For Cora

Given Cora's current product shape, the first capability areas to cover should be:

1. save
2. retrieve
3. clarify
4. deliver
5. memory

The first high-value case groups should therefore be:

- save text note
- save image with note
- retrieve by content description
- retrieve with multiple candidates
- deliver existing item
- deliver missing item safely
- user memory add/read/update/remove
- do not delete on vague request

## Migration Advice For The Current Implementation

The current minimal eval runner is a good bootstrap, but it should not remain the final shape.

Recommended migration path:

1. keep the current runner working
2. extract data models out of the monolithic runner
3. split case loading, runtime execution, and judging into separate modules
4. move case files into typed subdirectories
5. add fixture support before adding many more cases
6. add state judges before adding LLM judges

This preserves momentum without locking the project into an accidental architecture.

## Final Recommendation

Cora should treat evaluation as a product-quality subsystem, not a helper script collection.

The subsystem should be designed to measure:

- whether the agent chose the right action
- whether the right tools were used
- whether the right state changes happened
- whether the user-facing result was honest and useful

If this design is followed, Cora will gain a major advantage as an agent engineering practice project:

it will become a system that can be improved deliberately instead of impressionistically.
