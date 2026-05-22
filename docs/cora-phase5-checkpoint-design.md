# Phase 5：Plan 执行 Checkpoint（PR-5a）

> 状态：PR-5a 已落地（失败断点持久化 + `/execute resume|restart` + eval）。  
> 前置：Phase 3 计划执行 + HITL 暂停；Phase 4 subagent。

## 1. 目标

在 **不重新 `/plan`** 的前提下，当多步计划执行在中途失败时：

- 持久化已完成 task 结果与暂停位置
- 用户可用 `/execute resume` 从失败 task 重试
- 用户可用 `/execute restart` 清空 checkpoint 后整 plan 重跑

与 **HITL 暂停** 的区别：

| 机制 | 触发 | 恢复方式 |
|------|------|----------|
| HITL | Worker 步触发 `policy_ask` | 用户「确认」→ `approve_plan_execution_hitl_and_resume` |
| Checkpoint | Worker 步 `failed` | `/execute resume` 或失败后再次 `/execute`（auto-resume） |

## 2. 数据模型

`StoredPlanExecution`（`execution_state_json`）字段：

| 字段 | 含义 |
|------|------|
| `task_index` | 暂停时的 task 下标 |
| `task_results` | 已完成（含失败）task 摘要列表 |
| `pause_reason` | `hitl` \| `failed` \| `timeout` |
| `checkpoint_id` | 稳定断点 ID（`ckpt-…`） |
| `pending_hitl_id` | HITL 时非空；失败 checkpoint 为空 |

## 3. 命令

| 命令 | 行为 |
|------|------|
| `/execute` | 首次执行；若存在 `pause_reason=failed` 的 checkpoint 则 **自动续跑** |
| `/execute resume` | 显式从 checkpoint 续跑（HITL pending 时拒绝） |
| `/execute restart` | 清除 checkpoint 后从头执行 |

解析：`core/agent/plan_execute.py` → `parse_execute_plan_command`。

## 4. 执行路径

`ClawBotService.execute_plan_outcome`：

1. 解析命令 / 读取 checkpoint
2. `resume` / auto-resume → `_resume_plan_from_checkpoint`
3. 否则 `PlanExecutor.execute` 全量或续跑（`start_task_index` + `initial_task_results`）
4. `_persist_plan_execution_state`：HITL / failed 写入，成功则 `clear_execution`

续跑时去掉末尾 `status=failed` 的 task 结果，从 `task_index` 重试该步。

## 5. Eval / Stub

- Planner stub：`CORA_EVAL_PLANNER_STUB=two_step`（3 步计划）
- Task-2 失败：dev worker 对 `task-2` 发起 `write_file`（超出 allow list）→ `policy_denied` → task failed
- Eval：`plan_checkpoint_resume_after_failure`

可选：`CORA_EVAL_WORKER_FAIL_TASK_ID=task-2` 强制指定 task 失败。

## 6. Remaining（Phase 5 其它项）

- Mutating tool 幂等
- Retry backoff / 补偿
- MCP 工具挂载
- Run replay / 观测报告
