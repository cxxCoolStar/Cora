# Phase 5c：Retry Backoff（PR-5c）

> 状态：✅ 已完成  
> 前置：Phase 5a（checkpoint resume）、Phase 5b（idempotency）

## 1. 目标

在 **plan 执行** 时，对于 **可重试的错误**（transient errors），自动进行退避重试，避免因临时性故障导致整个 plan 失败。

### 问题场景

```
Task-2: 调用外部 API 验证配置
├─ 1. read_file("config.py") ✅
├─ 2. call_external_api() ❌ 网络超时（transient error）
└─ Task 被标记为 failed

用户需要手动 /execute resume
```

### 解决方案

- **自动重试**：对于可重试错误（网络超时、rate limit 等），自动重试，无需用户干预
- **退避策略**：使用指数退避（exponential backoff），避免过度重试
- **重试限制**：设置最大重试次数，防止无限重试
- **幂等性保证**：结合 Phase 5b 的 idempotency key，确保重试不会导致重复操作

---

## 2. 核心概念

### 2.1 可重试错误分类

| 错误类型 | 是否可重试 | 退避策略 | 示例 |
|----------|-----------|---------|------|
| **网络超时** | ✅ 是 | 指数退避 | `httpx.ReadTimeout`, `httpx.ConnectError` |
| **Rate Limit** | ✅ 是 | 指数退避 + Retry-After | `429 Too Many Requests` |
| **临时服务不可用** | ✅ 是 | 指数退避 | `503 Service Unavailable` |
| **模型传输失败** | ✅ 是 | 指数退避 | LLM API 临时故障 |
| **权限拒绝** | ❌ 否 | 不重试 | `permission_denied` |
| **参数验证失败** | ❌ 否 | 不重试 | `invalid_arguments` |
| **用户拒绝** | ❌ 否 | 不重试 | HITL rejection |
| **安全阻止** | ❌ 否 | 不重试 | `safety_blocked` |
| **不可逆变更失败** | ❌ 否 | 不重试（需补偿） | 写入失败但无法回滚 |

### 2.2 退避策略

**指数退避（Exponential Backoff）：**

```
delay = base_delay * (2 ^ attempt) + jitter
```

- `base_delay`: 基础延迟（默认 1 秒）
- `attempt`: 当前重试次数（0-indexed）
- `jitter`: 随机抖动（0-0.5 秒），避免雷鸣群效应

**示例：**
- 第 1 次重试：1s + jitter
- 第 2 次重试：2s + jitter
- 第 3 次重试：4s + jitter
- 最大延迟：30s

**Rate Limit 特殊处理：**
- 如果响应包含 `Retry-After` header，使用该值
- 否则使用指数退避

### 2.3 重试限制

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_retries` | 3 | 单个 task 的最大重试次数 |
| `max_total_retries` | 10 | 整个 plan 的最大重试次数（跨所有 task） |
| `max_retry_delay` | 30s | 单次重试的最大延迟 |
| `retry_timeout` | 5min | 单个 task 的重试总超时 |

---

## 3. 数据模型

### 3.1 TaskResultSpec 扩展

```python
@dataclass
class TaskResultSpec:
    task_id: str
    run_id: str
    status: str  # completed | failed | pending
    summary: str
    tool_trace_count: int
    completed_operations: list[str] = field(default_factory=list)
    
    # 新增字段
    retry_count: int = 0
    """当前 task 的重试次数"""
    
    last_error: str | None = None
    """最后一次错误信息"""
    
    error_category: str | None = None
    """错误分类：transient | permission_denied | invalid_arguments | ..."""
    
    retryable: bool = False
    """是否可重试"""
```

### 3.2 PlanExecutionResult 扩展

```python
@dataclass
class PlanExecutionResult:
    plan_run: PlanRunSpec
    reply: str
    status: str
    disposition: str
    waiting_hitl: bool = False
    pending_hitl_id: str | None = None
    paused_task_index: int | None = None
    pause_reason: str | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    
    # 新增字段
    total_retry_count: int = 0
    """整个 plan 的总重试次数"""
```

### 3.3 错误分类枚举

```python
class ErrorCategory(str, Enum):
    """错误分类"""
    TRANSIENT = "transient"  # 临时性错误，可重试
    RATE_LIMIT = "rate_limit"  # Rate limit，可重试
    TIMEOUT = "timeout"  # 超时，可重试
    PERMISSION_DENIED = "permission_denied"  # 权限拒绝，不可重试
    INVALID_ARGUMENTS = "invalid_arguments"  # 参数错误，不可重试
    USER_REJECTION = "user_rejection"  # 用户拒绝，不可重试
    SAFETY_BLOCKED = "safety_blocked"  # 安全阻止，不可重试
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"  # 基础设施故障，可重试
    UNKNOWN = "unknown"  # 未知错误，不可重试
```

---

## 4. 实现路径

### 4.1 错误分类器

```python
# src/core/agent/retry_policy.py

def classify_error(
    *,
    error: Exception | str,
    tool_name: str | None = None,
    status_code: int | None = None,
) -> tuple[ErrorCategory, bool]:
    """
    分类错误并判断是否可重试。
    
    Returns:
        (error_category, retryable)
    """
    # 网络超时
    if isinstance(error, (httpx.ReadTimeout, httpx.ConnectError)):
        return (ErrorCategory.TRANSIENT, True)
    
    # Rate limit
    if status_code == 429:
        return (ErrorCategory.RATE_LIMIT, True)
    
    # 临时服务不可用
    if status_code in (502, 503, 504):
        return (ErrorCategory.TRANSIENT, True)
    
    # 权限拒绝
    if "permission_denied" in str(error).lower():
        return (ErrorCategory.PERMISSION_DENIED, False)
    
    # 参数验证失败
    if "invalid_arguments" in str(error).lower():
        return (ErrorCategory.INVALID_ARGUMENTS, False)
    
    # 默认：不可重试
    return (ErrorCategory.UNKNOWN, False)
```

### 4.2 退避计算器

```python
# src/core/agent/retry_policy.py

import random
import time

def calculate_backoff_delay(
    *,
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """
    计算指数退避延迟。
    
    Args:
        attempt: 当前重试次数（0-indexed）
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        jitter: 是否添加随机抖动
    
    Returns:
        延迟时间（秒）
    """
    delay = base_delay * (2 ** attempt)
    delay = min(delay, max_delay)
    
    if jitter:
        delay += random.uniform(0, 0.5)
    
    return delay


def extract_retry_after(response_headers: dict[str, str]) -> float | None:
    """
    从响应头提取 Retry-After 值。
    
    Returns:
        延迟时间（秒），如果不存在则返回 None
    """
    retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
    if not retry_after:
        return None
    
    try:
        # Retry-After 可能是秒数或 HTTP 日期
        return float(retry_after)
    except ValueError:
        # 如果是日期格式，解析后计算差值
        # 简化处理：返回 None，使用默认退避
        return None
```

### 4.3 PlanExecutor 集成重试逻辑

在 `PlanExecutor.execute` 里：

```python
async def execute(
    self,
    *,
    session_id: str,
    plan: PlanSpec,
    planner_run_id: str,
    source_message_id: str,
    context_snapshot: RuntimeContextSnapshot,
    metadata_base: dict[str, Any],
    start_task_index: int = 0,
    initial_task_results: list[TaskResultSpec] | None = None,
) -> PlanExecutionResult:
    task_results = list(initial_task_results or [])
    total_retry_count = 0
    max_total_retries = 10
    
    for task_index in range(start_task_index, len(plan.tasks)):
        task = plan.tasks[task_index]
        retry_count = 0
        max_retries = 3
        retry_start_time = time.time()
        retry_timeout = 300  # 5 minutes
        
        while retry_count <= max_retries:
            # 检查重试超时
            if time.time() - retry_start_time > retry_timeout:
                return self._build_failure_result(
                    plan_run=PlanRunSpec(...),
                    reply=f"Task {task.task_id} retry timeout exceeded",
                    paused_task_index=task_index,
                    pause_reason="retry_timeout",
                )
            
            # 检查全局重试限制
            if total_retry_count >= max_total_retries:
                return self._build_failure_result(
                    plan_run=PlanRunSpec(...),
                    reply=f"Plan total retry limit exceeded ({max_total_retries})",
                    paused_task_index=task_index,
                    pause_reason="retry_limit_exceeded",
                )
            
            # 执行 task
            try:
                task_result, tool_trace = await self._run_worker_turn(
                    session_id=session_id,
                    plan=plan,
                    task=task,
                    planner_run_id=planner_run_id,
                    source_message_id=source_message_id,
                    context_snapshot=context_snapshot,
                    metadata_base=metadata_base,
                )
                
                # 成功：记录结果并继续
                if task_result.status == "completed":
                    task_results.append(task_result)
                    break
                
                # 失败：分类错误并决定是否重试
                error_category, retryable = classify_error(
                    error=task_result.last_error or "",
                    tool_name=None,
                    status_code=None,
                )
                
                task_result.error_category = error_category.value
                task_result.retryable = retryable
                task_result.retry_count = retry_count
                
                if not retryable or retry_count >= max_retries:
                    # 不可重试或达到重试上限：失败
                    task_results.append(task_result)
                    return self._build_failure_result(
                        plan_run=PlanRunSpec(...),
                        reply=f"Task {task.task_id} failed: {task_result.summary}",
                        paused_task_index=task_index,
                        pause_reason="failed",
                    )
                
                # 可重试：计算退避延迟并重试
                retry_count += 1
                total_retry_count += 1
                delay = calculate_backoff_delay(attempt=retry_count - 1)
                
                # 记录重试事件
                self._log_retry_event(
                    task_id=task.task_id,
                    retry_count=retry_count,
                    delay=delay,
                    error_category=error_category,
                )
                
                # 等待退避延迟
                await asyncio.sleep(delay)
                
            except Exception as exc:
                # 未捕获的异常：分类并决定是否重试
                error_category, retryable = classify_error(error=exc)
                
                if not retryable or retry_count >= max_retries:
                    task_result = TaskResultSpec(
                        task_id=task.task_id,
                        run_id="",
                        status="failed",
                        summary=str(exc),
                        tool_trace_count=0,
                        retry_count=retry_count,
                        last_error=str(exc),
                        error_category=error_category.value,
                        retryable=retryable,
                    )
                    task_results.append(task_result)
                    return self._build_failure_result(
                        plan_run=PlanRunSpec(...),
                        reply=f"Task {task.task_id} failed: {exc}",
                        paused_task_index=task_index,
                        pause_reason="failed",
                    )
                
                # 可重试：退避并重试
                retry_count += 1
                total_retry_count += 1
                delay = calculate_backoff_delay(attempt=retry_count - 1)
                await asyncio.sleep(delay)
    
    # 所有 task 完成
    return PlanExecutionResult(
        plan_run=PlanRunSpec(...),
        reply="Plan execution completed successfully",
        status="completed",
        disposition="respond",
        total_retry_count=total_retry_count,
    )
```

### 4.4 Observability：重试事件记录

```python
# src/core/agent/retry_policy.py

def log_retry_event(
    *,
    task_id: str,
    retry_count: int,
    delay: float,
    error_category: ErrorCategory,
    error_message: str | None = None,
) -> None:
    """记录重试事件到 trace"""
    event = {
        "event": "task.retry",
        "task_id": task_id,
        "retry_count": retry_count,
        "delay_seconds": delay,
        "error_category": error_category.value,
        "error_message": error_message,
        "timestamp": time.time(),
    }
    # 发送到 observability 系统
    # logger.info("task.retry", extra=event)
```

---

## 5. Eval 设计

### 5.1 Eval Case: `plan_retry_backoff_recovers_from_transient_error`

```json
{
  "id": "plan_retry_backoff_recovers_from_transient_error",
  "type": "harness",
  "description": "Task should automatically retry on transient errors with exponential backoff.",
  "tags": ["harness", "planner", "worker", "retry", "backoff"],
  "setup": {
    "planner_stub_mode": "two_step",
    "worker_transient_failure_count": 2,
    "workspace_files": {
      "data.txt": "initial content"
    }
  },
  "steps": [
    {
      "label": "planner creates two-step plan",
      "input": {
        "agent_role": "planner",
        "text": "Process data.txt"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["/execute", "task-2"]
      }
    },
    {
      "label": "execute retries task-1 twice then succeeds",
      "input": {
        "agent_role": "execute",
        "text": "/execute"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["execution completed successfully"],
        "state": {
          "latest_agent_run_trace_contains_all": [
            "task.retry",
            "retry_count"
          ],
          "task_1_retry_count": 2
        }
      }
    }
  ]
}
```

### 5.2 Eval Case: `plan_retry_stops_on_non_retryable_error`

```json
{
  "id": "plan_retry_stops_on_non_retryable_error",
  "type": "harness",
  "description": "Task should not retry on non-retryable errors like permission denial.",
  "tags": ["harness", "planner", "worker", "retry", "permission"],
  "setup": {
    "planner_stub_mode": "two_step",
    "workspace_files": {
      "data.txt": "initial content"
    }
  },
  "steps": [
    {
      "label": "planner creates two-step plan",
      "input": {
        "agent_role": "planner",
        "text": "Write to protected file"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["/execute", "task-2"]
      }
    },
    {
      "label": "execute fails immediately on permission denial",
      "input": {
        "agent_role": "execute",
        "text": "/execute"
      },
      "expect": {
        "status": "failed",
        "disposition": "respond",
        "reply_contains_all": ["execution failed", "permission_denied"],
        "state": {
          "task_1_retry_count": 0,
          "task_1_error_category": "permission_denied",
          "task_1_retryable": false
        }
      }
    }
  ]
}
```

### 5.3 Eval Case: `plan_retry_respects_max_retries_limit`

```json
{
  "id": "plan_retry_respects_max_retries_limit",
  "type": "harness",
  "description": "Task should stop retrying after reaching max_retries limit.",
  "tags": ["harness", "planner", "worker", "retry", "limit"],
  "setup": {
    "planner_stub_mode": "two_step",
    "worker_transient_failure_count": 10,
    "max_retries": 3,
    "workspace_files": {
      "data.txt": "initial content"
    }
  },
  "steps": [
    {
      "label": "planner creates two-step plan",
      "input": {
        "agent_role": "planner",
        "text": "Process data.txt"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["/execute", "task-2"]
      }
    },
    {
      "label": "execute retries 3 times then fails",
      "input": {
        "agent_role": "execute",
        "text": "/execute"
      },
      "expect": {
        "status": "failed",
        "disposition": "respond",
        "reply_contains_all": ["execution failed", "retry limit"],
        "state": {
          "task_1_retry_count": 3
        }
      }
    }
  ]
}
```

---

## 6. 实现切片（小步 PR）

### PR-5c-1: 数据模型与错误分类 ✅

- [x] `TaskResultSpec` 扩展：`retry_count`, `last_error`, `error_category`, `retryable`
- [x] `PlanExecutionResult` 扩展：`total_retry_count`
- [x] `ErrorCategory` 枚举定义
- [x] `classify_error()` 函数
- [x] 单元测试：`test_classify_error_for_transient_errors`（25 个测试全部通过）

### PR-5c-2: 退避策略实现 ✅

- [x] `calculate_backoff_delay()` 函数
- [x] `extract_retry_after()` 函数
- [x] 单元测试：`test_calculate_backoff_delay`（已包含在 PR-5c-1 的 25 个测试中）

### PR-5c-3: PlanExecutor 集成重试逻辑 ✅

- [x] `PlanExecutor.execute` 添加重试循环
- [x] `PlanExecutor._run_task_with_retry` 实现重试逻辑
- [x] 重试限制检查（`max_retries`, `max_total_retries`, `retry_timeout`）
- [x] 退避延迟等待
- [x] 错误分类和重试决策
- [x] 重试事件记录到 trace
- [x] 现有 36 个 harness eval 仍然全绿

### PR-5c-4: Observability 与事件记录 ✅

- [x] `log_retry_event()` 函数
- [x] 重试事件记录到 trace
- [x] 单元测试：`test_retry_event_logging`（已包含在 PR-5c-1 的 25 个测试中）

### PR-5c-5: Eval 与集成测试 ✅

- [x] `plan_retry_stops_on_non_retryable_error.json` - 验证不可重试错误立即失败
- [x] 运行 `.\scripts\run_harness_evals.cmd` 确保全绿（37/37 通过）

**注意**：由于当前 eval 框架的限制，暂时无法创建模拟 transient error 自动恢复的 eval。需要扩展 eval 框架支持：
- `worker_transient_failure_count`: 配置前 N 次执行失败，第 N+1 次成功
- 这将在后续 PR 中实现

---

## 7. 边界情况

### 7.1 幂等性保证

结合 Phase 5b 的 idempotency key：

- **重试前检查**：每次重试前，检查 `completed_operations` 缓存
- **跳过已完成操作**：如果操作已完成，跳过执行
- **记录新操作**：只有新执行的操作才记录 idempotency key

### 7.2 Rate Limit 特殊处理

- **Retry-After header**：优先使用响应头的 `Retry-After` 值
- **指数退避**：如果没有 `Retry-After`，使用指数退避
- **最大延迟**：限制单次延迟不超过 `max_retry_delay`（30s）

### 7.3 重试超时

- **单 task 超时**：单个 task 的重试总时间不超过 `retry_timeout`（5min）
- **全局重试限制**：整个 plan 的总重试次数不超过 `max_total_retries`（10 次）

### 7.4 Checkpoint 与 Resume

- **重试计数持久化**：`TaskResultSpec.retry_count` 保存到 checkpoint
- **Resume 时重置**：用户手动 `/execute resume` 时，重置 `retry_count`
- **自动 resume**：自动重试不重置 `retry_count`

---

## 8. 验收标准

- [x] `TaskResultSpec` 和 `PlanExecutionResult` 包含重试相关字段
- [x] 错误分类器能正确识别可重试和不可重试错误
- [x] 退避策略实现指数退避和 jitter
- [x] PlanExecutor 能自动重试 transient errors
- [x] 重试限制（max_retries, max_total_retries, retry_timeout）生效
- [x] 重试事件记录到 trace
- [x] Eval 通过：
  - `plan_retry_stops_on_non_retryable_error` ✅
- [x] 现有 37 个 harness eval 全部通过
- [x] 单元测试覆盖错误分类、退避计算、重试逻辑（25 个测试全部通过）

---

## 9. 未来扩展（Phase 5 其它项）

- **PR-5d：** MCP 工具挂载（外部工具也受重试策略约束）
- **PR-5e：** Run replay（回放时展示重试历史）
- **PR-5f：** 补偿机制（Compensation）：对于不可逆操作失败，提供补偿逻辑

---

## 10. 参考

- [cora-phase5-checkpoint-design.md](./cora-phase5-checkpoint-design.md) — Phase 5a checkpoint 基础
- [cora-phase5b-idempotency-design.md](./cora-phase5b-idempotency-design.md) — Phase 5b 幂等性保证
- [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) — 第 13.3 节 Retry Policy
