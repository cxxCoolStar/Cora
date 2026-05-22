# Phase 4：受控 Subagent 设计草案

> 状态：PR-4a–4g + 4e eval 已落地；Reviewer (PR-3d) 暂停。  
> 前置：Phase 3（Planner / Worker / HITL / plan SQL）+ harness 18/18。

## 1. 目标

在**有硬边界**的前提下，允许主 run 拉起**子 harness run**（subagent），用于：

- 把重探索/子任务隔离到子 session + 子 trace
- 子 run **继承更窄**的 tool policy，不能比父 run 更宽
- 子结果结构化合并回父 run（4d）

本阶段**不做**：并行 subagent 风暴、外部 MCP agent、checkpoint 长时恢复（Phase 5）。

## 2. 核心概念

| 字段 | 含义 |
|------|------|
| `spawn_depth` | 当前 run 在 spawn 树中的深度：`0` = 根（用户会话主 run），`1` = 子 run，`2` = 孙 run |
| `max_spawn_depth` | 本 run **允许的最大深度**；若 `spawn_depth > max_spawn_depth`，harness **拒绝启动** |
| `max_child_runs` | 单父 run 可 spawn 的子 run 上限（4b，本 PR 仅 schema 预留） |
| `parent_run_id` | 已有；子 run 指向父 `run_id`（plan Worker 已使用） |

**深度规则（v1）**

- 默认 `max_spawn_depth = 1`（`CORA_HARNESS_MAX_SPAWN_DEPTH`）：允许 depth `0` 与 `1`，拒绝 `2+`。
- 将来 spawn 工具：`child_depth = parent.spawn_depth + 1`，子 run 继承 `min(parent.max, config_default)`。

## 3. 角色与 PR 切片

| PR | 内容 | 状态 |
|----|------|------|
| **4a** | `spawn_depth` / `max_spawn_depth`、harness 启动前拒绝、`subagent.spawn.denied` trace、eval | ✅ |
| **4b** | `spawn_worker_turn` / `SubagentSpawner`、子 session、`max_child_runs`、parent trace | ✅ |
| **4c** | 继承 tool policy（子 ⊂ 父 allow 集） | ✅ |
| **4d** | 子 run `SubagentResultSpec` 合并回父 orchestrator metadata + 回复 | ✅ |
| **4e** | eval：spawn 超限、policy 继承 deny、merge 失败（含 tool 路径） | ✅ |
| **4f** | 主 agent `spawn_worker` 工具（同步子 run + 结构化合并） | ✅ |
| **4g** | 主 agent `spawn_workers` 并行工具（`asyncio.gather` + 并发上限） | ✅ |

## 4. Harness 行为（4a）

```mermaid
sequenceDiagram
    participant H as DefaultAgentHarness
    participant R as Run record

    H->>R: run.started
    alt spawn_depth > max_spawn_depth
        H->>H: subagent.spawn.denied
        H->>H: cleanup.completed
        H-->>R: status failed, outcome error
    else allowed
        H->>H: prepare → start → resolve → cleanup
    end
```

拒绝时用户可见文案（英文，与 tool budget 一致）：

> Subagent spawn depth exceeded (depth=N, max=M).

## 5. 与 Phase 3 的关系

- Plan Worker 已设 `parent_run_id`；**不**自动提高 `spawn_depth`（仍为 0），直到 4b 显式 spawn。
- Reviewer（PR-3d）继续可选，不阻塞 Phase 4。

## 6. 验收

**4a**

- `spawn_depth_exceeds_max_denied` eval

**4b**

- `ClawBotService.spawn_worker_turn()` → `SubagentSpawner.spawn_worker()`
- 子 session：`session_kind=subagent`，`parent_session_id` 指向父会话
- 子 run：`spawn_depth=parent+1`，`parent_run_id` 指向 orchestrator run
- Parent trace：`subagent.spawned` / `subagent.completed`（拒绝时 `subagent.spawn.denied`）
- Eval：`spawn_worker_child_run_completes`、`spawn_worker_child_run_limit_denied`

**4c**

- `subagent_policy.py`：`parent_effective_allow_set` + `resolve_child_allowed_tool_names`
- Spawn 前拒绝不在父 allow 集内的 tool；子 run metadata `parent_allowed_tool_names` + harness 二次过滤
- Eval：`spawn_worker_policy_inherit_denied`

**4d**

- `SubagentResultSpec` + `subagent_result_from_turn()`；orchestrator `metadata.child_result`
- 父回复：`Subagent completed (status=…, tools=…)` + 子摘要
- `TurnResponse.context.child_result` 供上层合并

**4f / 4g**

- `spawn_worker` / `spawn_workers` 注册于 `subagent` toolset（`cora-cli` / `cora-api`，默认不含 WeChat）
- `RuntimeToolExecutor` + `ClawBotService.spawn_worker_for_tool()` / `spawn_workers_for_tool()`
- Harness 在 `prepare_turn` 后注入 `runtime.metadata`：`agent_run_id`、`spawn_depth`、`parent_run_id`、`run_budget`
- 配置：`CORA_HARNESS_MAX_PARALLEL_SPAWNS`（默认 3）
- Eval：`spawn_worker_tool_completes`、`spawn_workers_parallel_completes`
- `/spawn` 仍保留给 eval `agent_role: spawn` 与手动调试；产品路径为主 agent 工具调用

**4e**

- 已有（`/spawn` 路径）：`spawn_depth_exceeds_max_denied`、`spawn_worker_child_run_limit_denied`、`spawn_worker_policy_inherit_denied`
- 补充（`spawn_worker` / `spawn_workers` 工具路径）：
  - `spawn_worker_tool_policy_inherit_denied`
  - `spawn_worker_tool_spawn_depth_denied`
  - `spawn_workers_tool_batch_limit_denied`
  - `spawn_worker_tool_failed_child_merge`（子任务错误输出合并回父回复）
  - 同会话二次限额仍由 `/spawn` 路径 `spawn_worker_child_run_limit_denied` 覆盖

**验收**

- `.\scripts\run_harness_evals.cmd` 全绿（28 cases）

## 7. 参考

- [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) — Phase 4 总览
- [cora-phase3-planning-design.md](./cora-phase3-planning-design.md) — 顺序 plan 执行
