# Phase 5e：Run Replay（PR-5e）

> 状态：✅ 已完成  
> 前置：Phase 5a（checkpoint resume）、Phase 5b（idempotency）、Phase 5c（retry backoff）

## 1. 目标

提供 **plan 执行回放（replay）** 功能，让用户能够：
- 查看完整的执行历史（包括重试、跳过的操作）
- 理解为什么某些操作被跳过（idempotency）
- 分析重试行为和失败原因
- 调试 plan 执行问题

### 问题场景

```
用户执行 plan 后看到：
- Task-1 [completed]: Tool search_files returned: ...
- Task-2 [completed]: Tool search_files returned: ...
- Task-3 [completed]: Tool search_files returned: ...

但实际上：
- Task-1 执行了 write_file，但 resume 时被跳过（idempotency）
- Task-2 失败了 2 次（timeout），第 3 次才成功
- Task-3 正常执行

用户无法看到这些细节，难以理解执行过程
```

### 解决方案

提供 **详细的执行报告**，包含：
- 所有 task 的执行历史（包括重试）
- 跳过的操作（idempotency key）
- 重试事件（错误类型、延迟时间）
- 时间线视图（每个操作的开始/结束时间）

---

## 2. 核心概念

### 2.1 Replay 信息层级

| 层级 | 内容 | 用途 |
|------|------|------|
| **Plan 级别** | 总执行时间、总重试次数、checkpoint 信息 | 整体概览 |
| **Task 级别** | 每个 task 的状态、重试次数、执行时间 | Task 粒度分析 |
| **Operation 级别** | 每个工具调用的详情、是否跳过、重试信息 | 操作粒度调试 |
| **Event 级别** | 所有事件（tool.started, tool.completed, task.retry, etc.） | 完整时间线 |

### 2.2 Replay 数据来源

| 数据源 | 包含信息 |
|--------|----------|
| `PlanExecutionResult` | Plan 状态、总重试次数、暂停原因 |
| `TaskResultSpec` | Task 状态、重试次数、错误信息、completed_operations |
| `tool_trace` | 工具调用历史、idempotency key、重试事件 |
| `StoredPlanExecution` | Checkpoint 数据、completed_operations_cache |

### 2.3 Replay 展示格式

**选项 A：文本报告（Markdown）**
```markdown
# Plan Execution Report

**Plan ID**: plan-abc123
**Status**: completed
**Total Time**: 45.2s
**Total Retries**: 3

## Task-1: Search workspace
- **Status**: completed
- **Retries**: 0
- **Time**: 2.1s

### Operations:
1. [00:00.0] search_files(path="src") → completed (1.2s)
2. [00:01.2] write_file(path="config.py") → completed (0.9s)

## Task-2: Process data
- **Status**: completed
- **Retries**: 2
- **Time**: 15.3s

### Operations:
1. [00:02.1] read_file(path="data.txt") → completed (0.5s)
2. [00:02.6] call_api() → failed (timeout) → retry after 1.2s
3. [00:04.3] call_api() → failed (timeout) → retry after 2.5s
4. [00:07.3] call_api() → completed (3.2s)

## Task-3: Finalize (resumed)
- **Status**: completed
- **Retries**: 0
- **Time**: 1.8s

### Operations:
1. [00:10.5] write_file(path="config.py") → skipped (idempotency: run-abc:task-1:write_file:config.py)
2. [00:10.5] send_message() → completed (1.8s)
```

**选项 B：JSON 报告（机器可读）**
```json
{
  "plan_id": "plan-abc123",
  "status": "completed",
  "total_time_seconds": 45.2,
  "total_retries": 3,
  "tasks": [
    {
      "task_id": "task-1",
      "status": "completed",
      "retry_count": 0,
      "time_seconds": 2.1,
      "operations": [
        {
          "timestamp": 0.0,
          "tool_name": "search_files",
          "arguments": {"path": "src"},
          "status": "completed",
          "duration_seconds": 1.2
        },
        {
          "timestamp": 1.2,
          "tool_name": "write_file",
          "arguments": {"path": "config.py"},
          "status": "completed",
          "duration_seconds": 0.9,
          "idempotency_key": "run-abc:task-1:write_file:config.py"
        }
      ]
    }
  ]
}
```

**选项 C：HTML 报告（可视化）**
- 时间线视图（类似 Chrome DevTools Timeline）
- 可折叠的 task 和 operation
- 颜色编码（成功=绿色、失败=红色、跳过=灰色、重试=黄色）

---

## 3. 数据模型

### 3.1 ReplayReport 数据结构

```python
@dataclass
class OperationReplay:
    """单个操作的回放信息"""
    timestamp: float  # 相对于 plan 开始的时间（秒）
    tool_name: str
    arguments: dict[str, Any]
    status: str  # completed | failed | skipped
    duration_seconds: float
    idempotency_key: str | None = None
    skipped_reason: str | None = None  # "idempotency" | None
    error_message: str | None = None
    retry_info: dict[str, Any] | None = None  # {"retry_count": 2, "delay": 2.5}


@dataclass
class TaskReplay:
    """单个 task 的回放信息"""
    task_id: str
    title: str
    status: str  # completed | failed | pending
    retry_count: int
    start_time: float  # 相对于 plan 开始的时间（秒）
    end_time: float
    duration_seconds: float
    operations: list[OperationReplay]
    error_category: str | None = None
    last_error: str | None = None


@dataclass
class PlanReplay:
    """整个 plan 的回放信息"""
    plan_id: str
    goal: str
    status: str  # completed | failed | waiting_hitl
    total_time_seconds: float
    total_retries: int
    checkpoint_count: int  # 经历了多少次 checkpoint
    tasks: list[TaskReplay]
    events: list[dict[str, Any]]  # 完整的事件时间线
```

### 3.2 从现有数据构建 Replay

```python
def build_plan_replay(
    *,
    plan: PlanSpec,
    execution_result: PlanExecutionResult,
    start_time: float,
) -> PlanReplay:
    """从 PlanExecutionResult 构建回放报告"""
    tasks_replay: list[TaskReplay] = []
    current_time = 0.0
    
    for task_result in execution_result.plan_run.task_results:
        # 从 tool_trace 提取操作信息
        operations = _extract_operations_from_trace(
            tool_trace=execution_result.tool_trace,
            task_id=task_result.task_id,
            start_time=current_time,
        )
        
        task_duration = sum(op.duration_seconds for op in operations)
        
        tasks_replay.append(TaskReplay(
            task_id=task_result.task_id,
            title=_find_task_title(plan, task_result.task_id),
            status=task_result.status,
            retry_count=task_result.retry_count,
            start_time=current_time,
            end_time=current_time + task_duration,
            duration_seconds=task_duration,
            operations=operations,
            error_category=task_result.error_category,
            last_error=task_result.last_error,
        ))
        
        current_time += task_duration
    
    return PlanReplay(
        plan_id=plan.plan_id,
        goal=plan.goal,
        status=execution_result.status,
        total_time_seconds=current_time,
        total_retries=execution_result.total_retry_count,
        checkpoint_count=_count_checkpoints(execution_result),
        tasks=tasks_replay,
        events=execution_result.tool_trace,
    )
```

---

## 4. 实现路径

### 4.1 Replay 数据收集

在 `PlanExecutor.execute` 里记录时间戳：

```python
async def execute(
    self,
    *,
    session_id: str,
    plan: PlanSpec,
    planner_run_id: str,
    source_message_id: str,
    context_snapshot: RuntimeContextSnapshot,
    run_metadata: dict[str, Any] | None = None,
    start_task_index: int = 0,
    initial_task_results: list[TaskResultSpec] | None = None,
    initial_tool_trace: list[dict[str, Any]] | None = None,
) -> PlanExecutionResult:
    plan_start_time = time.time()
    
    # ... 执行逻辑 ...
    
    # 在返回时添加时间戳信息
    return PlanExecutionResult(
        plan_run=plan_run,
        reply=format_plan_execution_reply(plan=plan, plan_run=plan_run),
        status="completed",
        disposition="respond",
        tool_trace=aggregated_tool_trace,
        total_retry_count=total_retry_count,
        execution_start_time=plan_start_time,  # 新增
        execution_end_time=time.time(),  # 新增
    )
```

### 4.2 Replay 报告生成

```python
# src/core/agent/plan_replay.py

def generate_replay_report(
    *,
    plan: PlanSpec,
    execution_result: PlanExecutionResult,
    format: str = "markdown",  # markdown | json | html
) -> str:
    """生成 plan 执行回放报告"""
    replay = build_plan_replay(
        plan=plan,
        execution_result=execution_result,
        start_time=execution_result.execution_start_time,
    )
    
    if format == "json":
        return _format_replay_as_json(replay)
    elif format == "html":
        return _format_replay_as_html(replay)
    else:
        return _format_replay_as_markdown(replay)


def _format_replay_as_markdown(replay: PlanReplay) -> str:
    """格式化为 Markdown 报告"""
    lines = [
        f"# Plan Execution Report",
        "",
        f"**Plan ID**: {replay.plan_id}",
        f"**Goal**: {replay.goal}",
        f"**Status**: {replay.status}",
        f"**Total Time**: {replay.total_time_seconds:.1f}s",
        f"**Total Retries**: {replay.total_retries}",
        "",
    ]
    
    for task in replay.tasks:
        lines.append(f"## {task.task_id}: {task.title}")
        lines.append(f"- **Status**: {task.status}")
        lines.append(f"- **Retries**: {task.retry_count}")
        lines.append(f"- **Time**: {task.duration_seconds:.1f}s")
        lines.append("")
        
        if task.operations:
            lines.append("### Operations:")
            for op in task.operations:
                timestamp_str = f"[{op.timestamp:06.1f}]"
                args_str = ", ".join(f"{k}={v!r}" for k, v in list(op.arguments.items())[:2])
                
                if op.status == "skipped":
                    lines.append(f"{timestamp_str} {op.tool_name}({args_str}) → skipped ({op.skipped_reason})")
                elif op.status == "failed":
                    lines.append(f"{timestamp_str} {op.tool_name}({args_str}) → failed ({op.error_message})")
                    if op.retry_info:
                        lines.append(f"  → retry after {op.retry_info['delay']:.1f}s")
                else:
                    lines.append(f"{timestamp_str} {op.tool_name}({args_str}) → {op.status} ({op.duration_seconds:.1f}s)")
            lines.append("")
    
    return "\n".join(lines)
```

### 4.3 命令行集成

添加 `/replay` 命令：

```python
# src/core/agent/plan_execute.py

def parse_replay_command(text: str) -> dict[str, Any] | None:
    """解析 /replay 命令"""
    text = str(text or "").strip().lower()
    if not text.startswith("/replay"):
        return None
    
    parts = text.split()
    format_type = "markdown"  # 默认
    if len(parts) > 1 and parts[1] in ("json", "html", "markdown"):
        format_type = parts[1]
    
    return {
        "command": "replay",
        "format": format_type,
    }
```

在 `ClawBotService` 里处理：

```python
async def execute_plan_outcome(
    self,
    *,
    session_id: str,
    source_message_id: str,
    text: str,
    upload: UploadFile | None,
    context_snapshot: RuntimeContextSnapshot,
) -> AgentOutcome:
    # ... 现有逻辑 ...
    
    # 新增：处理 /replay 命令
    replay_cmd = parse_replay_command(text)
    if replay_cmd is not None:
        return await self._handle_replay_command(
            session_id=session_id,
            format_type=replay_cmd["format"],
        )
    
    # ... 其余逻辑 ...


async def _handle_replay_command(
    self,
    *,
    session_id: str,
    format_type: str,
) -> AgentOutcome:
    """处理 /replay 命令"""
    # 获取最近的 plan execution
    stored_plan = self.plan_store.get_validated_plan(session_id=session_id)
    if stored_plan is None:
        return AgentOutcome(
            reply="No plan found for this session.",
            status="failed",
            disposition="respond",
        )
    
    stored_execution = self.plan_store.get_execution(session_id=session_id)
    if stored_execution is None:
        return AgentOutcome(
            reply="No execution history found for this plan.",
            status="failed",
            disposition="respond",
        )
    
    # 重建 PlanExecutionResult（从 checkpoint）
    execution_result = _rebuild_execution_result_from_checkpoint(stored_execution)
    
    # 生成 replay 报告
    report = generate_replay_report(
        plan=stored_plan.plan,
        execution_result=execution_result,
        format=format_type,
    )
    
    return AgentOutcome(
        reply=report,
        status="completed",
        disposition="respond",
    )
```

---

## 5. 用户交互

### 5.1 基本用法

```
用户: /execute
Cora: Plan execution completed successfully.
      - task-1 [completed]: ...
      - task-2 [completed]: ...
      - task-3 [completed]: ...

用户: /replay
Cora: # Plan Execution Report
      
      **Plan ID**: plan-abc123
      **Status**: completed
      **Total Time**: 45.2s
      **Total Retries**: 3
      
      ## Task-1: Search workspace
      - **Status**: completed
      - **Retries**: 0
      - **Time**: 2.1s
      
      ### Operations:
      [00:00.0] search_files(path="src") → completed (1.2s)
      [00:01.2] write_file(path="config.py") → completed (0.9s)
      
      ...
```

### 5.2 指定格式

```
用户: /replay json
Cora: {
        "plan_id": "plan-abc123",
        "status": "completed",
        ...
      }

用户: /replay html
Cora: <html>...</html>  (或者返回一个链接)
```

---

## 6. 实现切片（小步 PR）

### PR-5e-1: 数据模型与时间戳记录 ✅

- [x] `PlanExecutionResult` 扩展：`execution_start_time`, `execution_end_time`
- [x] `OperationReplay`, `TaskReplay`, `PlanReplay` 数据结构
- [x] 在 `PlanExecutor.execute` 记录时间戳
- [x] 单元测试：`test_plan_execution_result_with_timestamps`

### PR-5e-2: Replay 数据构建 ✅

- [x] `build_plan_replay()` 函数
- [x] `_extract_operations_from_trace()` 辅助函数
- [x] 从 `tool_trace` 提取操作信息
- [x] 识别跳过的操作（idempotency）
- [x] 识别重试事件
- [x] 单元测试：`test_build_plan_replay_from_execution_result`

### PR-5e-3: Markdown 报告生成 ✅

- [x] `generate_replay_report()` 函数
- [x] `_format_replay_as_markdown()` 实现
- [x] 格式化 task、operation、retry 信息
- [x] 单元测试：`test_format_replay_as_markdown`

### PR-5e-4: JSON 报告生成 ✅

- [x] `_format_replay_as_json()` 实现
- [x] JSON 序列化
- [x] 单元测试：`test_format_replay_as_json`

### PR-5e-5: 命令行集成 ✅

- [x] `parse_replay_command()` 函数
- [x] `ClawBotService._handle_replay_command()` 实现
- [x] 从 checkpoint 重建 execution result
- [x] 单元测试：`test_parse_replay_command`

### PR-5e-6: Eval 与集成测试 ✅

- [x] `plan_replay_shows_retry_history.json`
- [x] `plan_replay_shows_skipped_operations.json`
- [x] 运行 `.\scripts\run_harness_evals.cmd` 确保全绿（39/39 通过）

---

## 7. 边界情况

### 7.1 Checkpoint Resume 后的 Replay

Resume 后的 replay 应该包含：
- Resume 前的所有操作（从 checkpoint 恢复）
- Resume 后的新操作
- 标记哪些 task 是 resumed

### 7.2 大型 Plan 的 Replay

对于包含大量 task 的 plan：
- Markdown 报告可能很长，考虑分页或折叠
- JSON 报告可能很大，考虑流式输出
- HTML 报告可以使用可折叠的 UI

### 7.3 Replay 数据持久化

当前设计从 `PlanExecutionResult` 和 `tool_trace` 构建 replay。
如果需要持久化 replay 数据：
- 可以在 checkpoint 里保存 `replay_data`
- 或者在执行完成后保存到单独的 replay 文件

---

## 8. 验收标准

- [x] `PlanExecutionResult` 包含时间戳字段
- [x] `PlanReplay` 数据结构完整
- [x] 能从 `PlanExecutionResult` 构建 replay
- [x] Markdown 报告格式清晰易读
- [x] JSON 报告结构完整
- [x] `/replay` 命令正常工作
- [x] Replay 包含重试历史
- [x] Replay 包含跳过的操作（idempotency）
- [x] Eval 通过：
  - `plan_replay_shows_retry_history` ✅
  - `plan_replay_shows_skipped_operations` ✅
- [x] 现有 37 个 harness eval 仍然全绿（现在是 39 个全绿）
- [x] 单元测试覆盖 replay 构建和格式化逻辑

---

## 9. 未来扩展

- **HTML 可视化报告**：时间线视图、交互式 UI
- **Replay 导出**：导出为文件（.md, .json, .html）
- **Replay 比较**：对比两次执行的差异
- **Replay 过滤**：只显示失败的 task、只显示重试的操作等

---

## 10. 参考

- [cora-phase5-checkpoint-design.md](./cora-phase5-checkpoint-design.md) — Phase 5a checkpoint 基础
- [cora-phase5b-idempotency-design.md](./cora-phase5b-idempotency-design.md) — Phase 5b 幂等性
- [cora-phase5c-retry-backoff-design.md](./cora-phase5c-retry-backoff-design.md) — Phase 5c 重试机制
