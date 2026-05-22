# Cora Production Multi-Agent Harness Design

## 1. Purpose

This document redesigns Cora's multi-agent harness plan around the current
general-agent direction in `docs/cora-general-agent-direction.md`.

Cora is evolving from an archive-centered assistant into a general-purpose
agent runtime. Multi-agent support should therefore not be a prompt trick or a
single `spawn_agent` tool. It should be a controlled runtime layer that can run,
limit, observe, resume, and evaluate agent work.

The north star is:

```text
shell -> orchestrator -> harness lifecycle -> agent roles -> governed tools -> state / memory / evals
```

The first milestone is not "many agents". The first milestone is a reliable
single-agent harness whose lifecycle, tool policy, state, budget, and trace
contracts are explicit. Multi-agent behavior should be added only after that
foundation exists.

Current baseline:

- Phase 1 is implemented for the WeChat production path and the current
  single-agent loop.
- The harness smoke gate is `.\scripts\run_harness_evals.cmd`.
- The current smoke baseline is `10/10` harness cases passing, including
  WeChat entry-path success, permission denial, timeout, and failure records.
- Phase 2/3 work should not start by adding Planner, Worker, Reviewer, spawn,
  or direct tool-plan behavior. It should start by designing the unified tool
  policy decision layer.

## 2. Reference Projects

The examples folder contains useful reference designs.

### OpenClaw: Primary Reference

`examples/openclaw` is the best production-harness reference. It has:

- an explicit `AgentHarness` abstraction
- a V2 lifecycle: `prepare -> start -> send -> resolveOutcome -> cleanup`
- harness selection and runtime fallback policy
- subagent run records with requester, child session, timeout, outcome,
  pending delivery, retry, suspension, and cleanup metadata
- spawn policy, sandbox policy, inherited tool policy, and ACP/external-agent
  integration
- extensive tests around subagent lifecycle, sandbox restrictions, inherited
  denies, delivery, and persistence

Cora should borrow the lifecycle shape and the durable subagent run model, but
keep the first implementation smaller.

### Codex: Security And Execution Policy Reference

`examples/codex` is strongest in command execution safety:

- sandbox modes
- approval policy
- executable prefix rules
- spawned thread graph storage
- OpenTelemetry-style traces

Cora should borrow the idea of an execution policy layer between tool request
and tool execution, especially for terminal, file mutation, and network access.

### OpenCode: Role And Permission Reference

`examples/opencode` has a clean role model:

- primary agents such as `build` and `plan`
- subagents such as `general`, `explore`, and `scout`
- per-agent permission rules
- explicit task and task-status tools

Cora should borrow this for agent role definitions: roles are configuration
plus permissions, not only prompt text.

### Hermes Agent: Memory And Skill Reference

`examples/hermes-agent` is useful for:

- procedural skills
- persistent memory
- delegation
- trajectory generation
- scheduled work

Cora should borrow memory and skill ideas later, but not let self-improvement
or autonomous delegation outrun the harness safety model.

## 3. Current Cora State

Cora already has useful foundations:

- `src/core/agent/turn_runner.py`
  prepares runtime state, resolves tool specs, invokes the orchestrator, handles
  forced tools and retry fallback.
- `src/core/agent/orchestrator.py`
  builds prompt messages and delegates to the loop.
- `src/core/agent/loop.py`
  implements the model/tool loop and has `max_steps`.
- `src/core/agent/context_budget.py`
  estimates prompt size and supports context slicing.
- `src/core/tools/registry.py`
  registers tools with basic metadata.
- `src/core/evals`
  already contains the beginning of an eval subsystem.

The gap is that these pieces are still shaped like a single-agent tool loop.
They do not yet define:

- a harness lifecycle
- structured agent roles
- structured planning and result contracts
- tool risk and permission policy
- subagent run persistence
- durable checkpoints
- standardized trace and failure taxonomy
- multi-agent eval cases

## 4. Design Principles

### 4.1 Harness First, Multi-Agent Second

The runtime should first make a single agent run controllable. Only then should
it add Planner, Worker, Reviewer, or external agents.

### 4.2 Orchestrator Owns Control

Agents may propose work. The orchestrator decides:

- which role runs
- which tools are exposed
- whether a step can start
- whether a result is accepted
- whether to retry, review, ask the user, or abort

### 4.3 Structured Contracts Over Free Text

Planning, task dispatch, tool traces, and results must use typed schemas.
Natural language can explain intent, but it must not be the only machine
contract.

### 4.4 Least Context Sharing

Subagents should receive the smallest sufficient context:

- task goal
- relevant facts
- explicit constraints
- allowed tools
- expected output schema

They should not automatically receive the full parent conversation.

### 4.5 Safety Is A Runtime Property

Safety cannot live only in the prompt. Tool policy, sandbox policy, approval,
rate limits, and audit logs must be enforced outside model text.

### 4.6 Evals Are Part Of The Harness

Every new harness behavior should be paired with eval coverage:

- tool selection
- trajectory quality
- permission denial
- retry behavior
- budget cutoff
- subagent result merging

## 5. Target Architecture

```text
Shells
  WeChat / CLI / HTTP API
      |
      v
AgentTurnRunner
  request normalization
  runtime snapshot
  platform preset
      |
      v
AgentOrchestrator
  state machine
  harness selection
  policy enforcement
  trace emission
      |
      v
AgentHarness
  prepare
  start
  send
  resolve
  cleanup
      |
      v
Agent Roles
  Primary
  Planner
  Worker
  Reviewer
  Memory Curator
      |
      v
Governed Tool Runtime
  registry
  permissions
  risk levels
  sandbox / HITL
  retry / timeout
      |
      v
State, Memory, Evals, Observability
```

## 6. Core Runtime Contracts

### 6.1 AgentHarness

Cora should introduce a small harness interface inspired by OpenClaw's V2
lifecycle.

```python
class AgentHarness(Protocol):
    id: str
    label: str

    async def prepare(self, params: HarnessAttemptParams) -> PreparedHarnessRun: ...
    async def start(self, prepared: PreparedHarnessRun) -> HarnessSession: ...
    async def send(self, session: HarnessSession) -> HarnessAttemptResult: ...
    async def resolve(self, session: HarnessSession, result: HarnessAttemptResult) -> HarnessAttemptResult: ...
    async def cleanup(self, params: HarnessCleanupParams) -> None: ...
```

Initial implementation can wrap the existing `AgentLoop`. The value is not a
new model loop; the value is a standard lifecycle boundary.

### 6.2 HarnessAttemptParams

Minimum fields:

- `run_id`
- `session_id`
- `source_message_id`
- `user_text`
- `agent_role`
- `platform`
- `tool_policy`
- `budget`
- `context_snapshot`
- `memory_snapshot`
- `trace_id`
- `parent_run_id`
- `spawn_depth`

### 6.3 HarnessAttemptResult

Minimum fields:

- `status`: `completed | incomplete | failed | aborted | needs_user`
- `disposition`: `respond | clarify | handoff | silent`
- `reply`
- `role`
- `tool_trace`
- `artifacts`
- `usage`
- `failure_category`
- `confidence`
- `state_patch`

### 6.4 AgentRunRecord

Every primary run and subagent run should have a durable record.

Minimum fields:

- `run_id`
- `session_id`
- `parent_run_id`
- `requester_session_id`
- `agent_role`
- `task`
- `status`
- `created_at`
- `started_at`
- `ended_at`
- `spawn_depth`
- `run_timeout_seconds`
- `budget_initial`
- `budget_remaining`
- `tool_policy_id`
- `checkpoint_id`
- `outcome`
- `failure_category`
- `pending_delivery`
- `cleanup_status`

For Phase 1 this may be persisted in SQLite or a small repository abstraction.
Do not keep it only in memory.

## 7. Agent Roles

Cora should define roles as config plus permissions plus prompt contribution.

### 7.1 Primary Agent

Purpose:

- handles ordinary user turns
- may use normal tools according to platform preset
- may request planning for complex work after Phase 2

Default permissions:

- `file.read`, `session_search`, `user_memory.read`, `skills.run`
- `web.search` when platform allows
- write/terminal tools require policy approval depending on shell

### 7.2 Planner

Purpose:

- decomposes complex tasks into a structured `PlanSpec`
- does not execute risky tools
- does not mutate files, memory, or external systems

Default permissions:

- read-only context
- optional search tools
- no write tools
- no terminal mutation

### 7.3 Worker

Purpose:

- executes one assigned `TaskSpec`
- returns a structured `ResultSpec`
- cannot modify the global plan

Default permissions:

- only tools explicitly granted by the orchestrator
- no subagent spawning by default
- may use write/terminal only if task policy allows

### 7.4 Reviewer

Purpose:

- reviews high-risk or low-confidence results
- checks plan/result consistency
- recommends accept, retry, ask user, or abort

Default permissions:

- read-only traces and artifacts
- no external mutation

### 7.5 Memory Curator

Purpose:

- decides whether a fact should enter durable memory
- separates temporary session state from long-term memory
- can summarize or prune memory

Default permissions:

- memory read/write through governed memory tools
- no file/terminal mutation

## 8. Planning And Result Schemas

### 8.1 PlanSpec

```json
{
  "goal": "string",
  "assumptions": ["string"],
  "steps": [
    {
      "id": "step-1",
      "title": "string",
      "role": "worker",
      "task": "string",
      "depends_on": [],
      "allowed_tools": ["file.read"],
      "risk": "low|medium|high",
      "parallel_group": "group-a",
      "expected_output": "summary|patch|data|decision",
      "requires_review": false
    }
  ],
  "stop_conditions": ["string"]
}
```

### 8.2 TaskSpec

```json
{
  "task_id": "string",
  "goal": "string",
  "context": ["necessary fact"],
  "constraints": ["string"],
  "allowed_tools": ["string"],
  "forbidden_tools": ["string"],
  "output_schema": "ResultSpec",
  "timeout_seconds": 120,
  "max_tool_calls": 8,
  "risk": "low|medium|high"
}
```

### 8.3 ResultSpec

```json
{
  "task_id": "string",
  "status": "completed|failed|needs_user|partial",
  "summary": "string",
  "artifacts": [],
  "facts": ["string"],
  "risks": ["string"],
  "confidence": "low|medium|high",
  "next_action": "accept|retry|review|ask_user|abort"
}
```

### 8.4 ErrorSpec

```json
{
  "category": "planning_error|tool_failure|permission_denied|timeout|budget_exhausted|invalid_result|safety_blocked|infrastructure_failure",
  "message": "string",
  "retryable": false,
  "safe_user_message": "string",
  "debug": {}
}
```

## 9. Orchestrator State Machine

The orchestrator should use an explicit state machine.

```text
received
  -> prepare_context
  -> select_role
  -> build_or_reuse_plan
  -> dispatch_step
  -> execute_step
  -> resolve_result
  -> review_if_needed
  -> merge_state
  -> complete
```

Failure edges:

```text
execute_step -> retry_step
execute_step -> ask_user
execute_step -> abort
resolve_result -> review_if_needed
review_if_needed -> retry_step
review_if_needed -> abort
any_state -> budget_cutoff
any_state -> timeout_cutoff
```

Phase 1 may skip planning and run:

```text
received -> prepare_context -> execute_primary -> resolve_result -> complete
```

The important point is that even the simple path should emit the same trace
shape and run record shape as later multi-agent paths.

## 10. Tool Governance

### 10.1 Tool Metadata

Extend the tool registry beyond schema and description.

Recommended fields:

- `toolset`
- `read_only`
- `risk`: `low | medium | high`
- `allowed_roles`
- `requires_confirmation`
- `requires_sandbox`
- `timeout_seconds`
- `rate_limit`
- `retry_policy`
- `idempotency_required`
- `audit_level`

### 10.2 Policy Decision

Every tool call should pass through a policy decision:

```text
allow -> execute
ask -> create HITL request
deny -> return policy error
sandbox -> execute in restricted runtime
```

This decision should consider:

- platform shell
- agent role
- sender/session trust
- tool risk
- file path or command target
- sandbox status
- user approvals
- inherited parent policy

### 10.3 Initial Risk Defaults

- Low risk: search, read, list, summarize, memory read.
- Medium risk: web fetch, browser navigation, memory write, file write in
  workspace.
- High risk: terminal execution, delete, external delivery, network mutation,
  writing outside workspace, credential access.

## 11. State And Memory

### 11.1 Runtime State

Short-lived operational data:

- current run
- current step
- budget remaining
- tool trace
- pending clarification
- active subagent runs

### 11.2 Session State

Conversation-scoped state:

- session summary
- active plan
- unresolved user choices
- run records for this session

### 11.3 Durable Memory

Long-lived user or domain knowledge:

- user preferences
- recurring project facts
- stable domain rules
- important prior outcomes

Durable memory writes should be explicit and auditable. A Worker should not
write durable memory directly unless granted a memory-curator role or tool.

### 11.4 Memory Injection Policy

Use a hybrid strategy:

- inject only a small high-confidence memory snapshot into prompts
- expose `memory_search` for explicit retrieval
- write memory only through a governed tool
- keep memory provenance and timestamps

## 12. Budget And Limits

Every run should have a budget object:

- `max_steps`
- `max_tool_calls`
- `max_duration_seconds`
- `max_prompt_tokens`
- `max_completion_tokens`
- `max_total_tokens`
- `max_spawn_depth`
- `max_child_runs`

Budget zones:

- Green: normal operation.
- Yellow: compress context and prefer cheaper model/role.
- Red: stop spawning and disallow nonessential tools.
- Cutoff: return partial result or ask user to continue.

Existing `context_budget.py` should become one component of this broader
runtime budget, not the entire budget system.

## 13. Failure Recovery

### 13.1 Checkpoints

Create checkpoints at:

- run start
- plan accepted
- before high-risk tool execution
- after external mutation
- after each completed subagent result
- before final response

### 13.2 Idempotency

Every mutating step should have an idempotency key:

```text
run_id + step_id + tool_name + semantic_target
```

For tools that cannot enforce idempotency, record the limitation and require
review or approval for retries.

### 13.3 Retry Policy

Retry only when the error is classified as retryable:

- transient network failure
- timeout
- rate limit with backoff
- model transport failure

Do not retry:

- permission denial
- invalid arguments after schema validation
- user rejection
- safety block
- irreversible mutation failure without compensation

## 14. Observability

Every run should emit structured events:

- `run.started`
- `run.completed`
- `run.failed`
- `step.started`
- `step.completed`
- `tool.requested`
- `tool.allowed`
- `tool.denied`
- `tool.completed`
- `budget.warning`
- `checkpoint.saved`
- `subagent.spawned`
- `subagent.completed`

Trace identifiers:

- `trace_id`
- `run_id`
- `parent_run_id`
- `session_id`
- `step_id`
- `tool_call_id`

Failure taxonomy should align with evals:

- `planning_error`
- `routing_failure`
- `tool_failure`
- `permission_denied`
- `timeout`
- `budget_exhausted`
- `memory_failure`
- `invalid_result`
- `safety_blocked`
- `infrastructure_failure`

## 15. MCP And External Agent Integration

MCP servers and external agent runtimes should never bypass Cora policy.

Rules:

- MCP tools are mounted through Cora's tool registry.
- External agent runtimes are mounted as harnesses.
- Third-party tools receive scoped credentials.
- Tool allowlists and denylists apply before model exposure.
- Sensitive outputs are redacted before being added to trace or memory.
- Sandboxed sessions cannot call host-only external runtimes unless explicitly
  approved.

This mirrors the OpenClaw approach of putting plugin/ACP runtimes behind the
same harness and tool policy boundary.

## 16. Eval Requirements

Extend `docs/cora-evaluation-module-design.md` with harness-specific cases.

Minimum eval groups:

- single-agent run completes with correct tool trace
- `max_steps` cutoff returns incomplete result
- denied tool is not executed
- high-risk tool creates approval request
- planner produces valid `PlanSpec`
- invalid `PlanSpec` is rejected
- worker receives only allowed context
- subagent spawn depth is enforced
- subagent result is merged only after schema validation
- retryable tool failure retries once
- non-retryable permission denial does not retry
- budget cutoff stops additional tool calls

Trajectory judges should inspect:

- repeated tool calls
- unnecessary fan-out
- missing review for high-risk results
- hallucinated success after tool failure

## 17. Implementation Roadmap

### Phase 0: Design Alignment

Deliver:

- this document
- agreement that Cora builds single-agent harness first
- no new archive-first assumptions in harness code

### Phase 1: Single-Agent Harness Foundation

Goal:

- make current Cora loop run through a standard harness lifecycle.

Actions:

1. Introduce `AgentHarness` interfaces and dataclasses.
2. Wrap current `AgentLoop` as `DefaultAgentHarness`.
3. Add `AgentRunRecord` persistence.
4. Move `max_steps` into a broader `RunBudget`.
5. Emit structured run and tool trace events.
6. Add evals for single-agent lifecycle, cutoff, and trace shape.

Current status:

- Implemented for the WeChat path and the current primary single-agent loop.
- `AgentTurnRunner.run_turn()` now runs through `DefaultAgentHarness`.
- `AgentRunRecord` is persisted in SQLite and exposed through the session run
  query API.
- Run records include `trace_id`, `agent_role`, `budget`,
  `failure_category`, `cleanup_status`, input metadata, and trace events.
- `RunBudget` covers `max_steps`, `timeout_seconds`, `max_tool_calls`,
  allowlists, denylists, and named policy profiles.
- Harness trace events cover lifecycle, tool policy, tool completion,
  tool denial, timeout, and failure.
- The smoke gate covers normal completion, tool trace, timeout, failure,
  allow/deny governance, named profiles, and WeChat entry-path behavior.

Phase 1 non-goals:

- No Planner, Worker, Reviewer, or subagent execution.
- No direct tool-plan harnessification.
- No complete HITL approval workflow.
- No sandbox decision engine.
- No checkpoint/resume or run replay.

### Phase 2: Tool Policy And Approval

Goal:

- make tool execution governed outside the model prompt.

Actions:

1. Extend `ToolSpec` metadata.
2. Add `ToolPolicyDecision`.
3. Enforce role/platform-based tool allowlists.
4. Add HITL request objects for high-risk tools.
5. Add sandbox-required markers for file and terminal tools.
6. Add evals for allow, ask, deny, and sandbox decisions.

Phase 2 first slice:

Do not start Phase 2 by adding more per-entry special cases. Start with a
small design and test slice for a single policy decision object:

```python
class ToolPolicyDecision:
    decision: Literal["allow", "ask", "deny", "sandbox"]
    reason: str
    tool_name: str
    risk: Literal["low", "medium", "high"]
    policy_profile: str | None
    requires_confirmation: bool
    requires_sandbox: bool
    safe_user_message: str
    audit_metadata: dict[str, object]
```

The first implementation should only wrap existing WeChat-path behavior:

- convert current profile allow/deny checks into `ToolPolicyDecision`
- preserve existing `wechat_safe` behavior
- emit the decision into trace metadata
- keep the current tool result text stable
- add evals for `allow` and `deny`

Only after this object is stable should Phase 2 add `ask`/HITL and `sandbox`.
Phase 3 must wait until tool policy decisions can reliably grant a Worker a
narrow tool set.

### Phase 3: Structured Planning Without Parallelism

Goal:

- add Planner and structured plans, but execute steps sequentially first.

Actions:

1. Add `PlanSpec`, `TaskSpec`, `ResultSpec`, and `ErrorSpec`.
2. Add Planner role with read-only permissions.
3. Validate planner output before execution.
4. Dispatch one Worker step at a time.
5. Add Reviewer for high-risk or low-confidence results.
6. Add evals for valid/invalid plans and result validation.

### Phase 4: Controlled Subagents

Goal:

- allow bounded Worker subagents.

Actions:

1. Add `spawn_depth` and `max_child_runs`.
2. Add durable subagent run records.
3. Support isolated and forked context modes.
4. Enforce inherited tool policy.
5. Add completion delivery and cleanup.
6. Add evals for spawn limits, inherited denies, and result merge.

### Phase 5: Advanced Runtime Features

Goal:

- make the system operationally robust.

Actions:

1. Add checkpoints and resume.
2. Add idempotency keys for mutating tools.
3. Add retry backoff and compensation hooks.
4. Add MCP tool mounting through registry.
5. Add external harness selection.
6. Add observability reports and run replay.

## 18. Current Phase 1 Baseline

Phase 1 is considered complete when this command passes:

```powershell
.\scripts\run_harness_evals.cmd
```

The expected baseline is:

```text
Cases: 10/10 passed
Steps: 10/10 passed
```

The harness smoke gate currently includes:

- single-agent lifecycle and durable run records
- tool trace recording
- timeout cutoff
- expected failure recording
- per-run allow policy denial
- max tool call budget denial
- named policy profile denial
- WeChat entry-path policy denial
- WeChat entry-path timeout recording
- WeChat entry-path failure recording

This baseline is intentionally narrow. It proves that the production WeChat
path and the current single-agent loop are controllable and observable before
Cora adds planners, workers, reviewers, or subagents.

## 19. Recommended First Code Changes

The smallest useful first slice:

1. Add `src/core/agent/harness.py`.
2. Add `src/core/schemas/harness.py`.
3. Add `src/core/agent/run_records.py`.
4. Wrap existing `AgentLoop.run()` in `DefaultAgentHarness`.
5. Change `AgentTurnRunner.run_turn()` to call the harness lifecycle.
6. Keep behavior equivalent at first.
7. Add tests that prove the new lifecycle preserves current behavior.

This creates a stable seam before adding any new multi-agent behavior.

Status: complete for Phase 1.

## 20. Acceptance Criteria

Cora's harness is moving in the right direction when:

1. a turn has a durable run record and trace
2. tool calls are governed by runtime policy, not only prompt guidance
3. risky tools can be denied or approved without special-casing each caller
4. a future Planner can only output structured plans
5. Workers can be given narrow context and narrow tools
6. subagent runs have bounded depth, timeout, budget, and cleanup
7. evals can catch tool-policy, trajectory, and budget regressions
8. archive remains a domain skill rather than the harness identity

The immediate objective is a boring, controllable harness. Interesting
multi-agent behavior should be built on top of that, not instead of it.
