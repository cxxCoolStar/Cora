# Phase 5b：Mutating Tool 幂等键（PR-5b）

> 状态：✅ 已完成  
> 前置：Phase 5a（checkpoint resume）

## 1. 目标

在 **plan resume** 时，防止已部分执行的 task 重复执行写操作。

### 问题场景

```
Task-2: 修改配置文件
├─ 1. read_file("config.py") ✅
├─ 2. write_file("config.py", new_content) ✅
├─ 3. call_api_to_validate() ❌ 超时失败
└─ Task 被标记为 failed

用户执行 /execute resume
└─ Task-2 完整重新执行
    └─ write_file 再次执行 ⚠️ 可能重复写入
```

### 解决方案

为每个 **mutating operation** 生成唯一的 **idempotency key**，resume 时检查该 key 是否已执行过。

---

## 2. 核心概念

### 2.1 Mutating Tool

**Mutating 操作** = 会改变系统状态的操作（写文件、发消息、删除数据等）

**重要：只有 mutating 操作才需要 idempotency key！**

| 工具类型 | 是否 mutating | 幂等性 | 需要 idempotency key |
|----------|---------------|--------|----------------------|
| `read_file` | ❌ 否 | ✅ 天然幂等 | ❌ 不需要（读操作无副作用） |
| `search_files` | ❌ 否 | ✅ 天然幂等 | ❌ 不需要（读操作无副作用） |
| `write_file` | ✅ 是 | ✅ 幂等（覆盖写） | ✅ 需要（避免重复写入） |
| `append_to_file` | ✅ 是 | ❌ 不幂等（追加） | ✅ 需要（防止重复追加） |
| `delete_item` | ✅ 是 | ✅ 幂等（删除不存在的项不报错） | ✅ 需要（避免重复删除尝试） |
| `send_message` | ✅ 是 | ❌ 不幂等（会发送多条） | ✅ 需要（防止重复发送） |
| `run_terminal_command` | ✅ 是 | ⚠️ 取决于命令 | ✅ 需要（命令可能有副作用） |

**为什么 read 不需要 idempotency key？**
- Read 操作不改变系统状态，重复执行是安全的
- Resume 时重新 read 可以获取最新状态（可能包含之前 write 的结果）
- 给 read 加 key 会增加大量无意义的记录

### 2.2 Idempotency Key

**格式：** `{run_id}:{task_id}:{tool_name}:{semantic_target}`

**示例：**
```
run-abc123:task-2:write_file:config.py
run-abc123:task-2:delete_item:item-456
run-abc123:task-3:send_message:user-789
```

**语义目标（semantic_target）：**
- `write_file` → 文件路径
- `delete_item` → item_id
- `send_message` → user_id
- `run_terminal_command` → 命令的前 50 字符 hash

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
    
    # 新增字段
    completed_operations: list[str] = field(default_factory=list)
    """已完成的 mutating 操作的 idempotency keys"""
```

### 3.2 StoredPlanExecution 扩展

```python
@dataclass
class StoredPlanExecution:
    session_id: str
    plan: PlanSpec
    planner_run_id: str
    source_message_id: str
    task_index: int
    task_results: list[TaskResultSpec]
    pending_hitl_id: str
    pause_reason: str
    checkpoint_id: str
    run_metadata: dict[str, Any]
    
    # 新增字段
    completed_operations_cache: dict[str, str] = field(default_factory=dict)
    """全局已完成操作缓存：{idempotency_key: result_summary}"""
```

---

## 4. 实现路径

### 4.1 工具元数据扩展

在 `ToolSpec` 或 tool registry 里标记 mutating 工具：

```python
# src/core/tools/registry.py
MUTATING_TOOLS = {
    "write_file": {
        "is_mutating": True,
        "is_idempotent": True,
        "semantic_target_extractor": lambda args: args.get("path"),
    },
    "append_to_file": {
        "is_mutating": True,
        "is_idempotent": False,
        "semantic_target_extractor": lambda args: args.get("path"),
    },
    "delete_item": {
        "is_mutating": True,
        "is_idempotent": True,
        "semantic_target_extractor": lambda args: args.get("item_id"),
    },
    "send_message": {
        "is_mutating": True,
        "is_idempotent": False,
        "semantic_target_extractor": lambda args: args.get("user_id"),
    },
}
```

### 4.2 Idempotency Key 生成

```python
# src/core/agent/idempotency.py

def generate_idempotency_key(
    *,
    run_id: str,
    task_id: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
) -> str | None:
    """为 mutating 工具生成幂等键"""
    tool_meta = MUTATING_TOOLS.get(tool_name)
    if tool_meta is None:
        return None  # 非 mutating 工具
    
    extractor = tool_meta["semantic_target_extractor"]
    target = extractor(tool_arguments)
    if not target:
        return None
    
    return f"{run_id}:{task_id}:{tool_name}:{target}"
```

### 4.3 Worker 执行前检查

在 `PlanExecutor._run_worker_turn` 里注入已完成操作列表：

```python
async def _run_worker_turn(
    self,
    *,
    session_id: str,
    plan: PlanSpec,
    task: TaskSpec,
    planner_run_id: str,
    source_message_id: str,
    context_snapshot: RuntimeContextSnapshot,
    metadata_base: dict[str, Any],
) -> tuple[TaskResultSpec, list[dict[str, Any]]]:
    plan_resume = bool(metadata_base.get("plan_resume"))
    
    # 新增：注入已完成操作列表
    completed_ops = []
    if plan_resume:
        checkpoint = self._get_checkpoint(session_id)
        if checkpoint:
            completed_ops = checkpoint.completed_operations_cache.keys()
    
    task_metadata = {
        **metadata_base,
        "agent_role": WORKER_AGENT_ROLE,
        "task_id": task.task_id,
        "parent_run_id": planner_run_id,
        "completed_operations": list(completed_ops),  # 新增
    }
    
    # ... 其余逻辑
```

### 4.4 Tool Executor 检查与记录

在 `RuntimeToolExecutor.execute` 里：

```python
async def execute(
    self,
    *,
    session_id: str,
    source_message_id: str,
    plan: ToolPlan,
    text: str,
    upload: UploadFile | None,
    context: dict[str, Any],
) -> ToolExecutionResult:
    # 生成 idempotency key
    run_id = context.get("agent_run_id", "")
    task_id = context.get("task_id", "")
    idempotency_key = generate_idempotency_key(
        run_id=run_id,
        task_id=task_id,
        tool_name=plan.tool,
        tool_arguments=plan.arguments or {},
    )
    
    # 检查是否已执行
    if idempotency_key:
        completed_ops = context.get("completed_operations", [])
        if idempotency_key in completed_ops:
            return ToolExecutionResult(
                reply=f"Operation {plan.tool} already completed (idempotency key: {idempotency_key})",
                action="tool_skipped",
                status="completed",
                disposition="respond",
                metadata={"idempotency_key": idempotency_key, "skipped": True},
            )
    
    # 执行工具
    result = await self._execute_tool(plan, text, upload, context)
    
    # 记录已完成操作
    if idempotency_key and result.status == "completed":
        result.metadata["idempotency_key"] = idempotency_key
    
    return result
```

### 4.5 Checkpoint 持久化

在 `_persist_plan_execution_state` 里收集所有已完成操作：

```python
def _persist_plan_execution_state(
    self,
    *,
    session_id: str,
    stored: StoredValidatedPlan,
    execution: PlanExecutionResult,
    source_message_id: str,
    run_metadata: dict[str, Any],
) -> None:
    # 收集所有已完成的 idempotency keys
    completed_ops_cache = {}
    for task_result in execution.plan_run.task_results:
        for op_key in task_result.completed_operations:
            completed_ops_cache[op_key] = task_result.summary
    
    # 保存 checkpoint
    if execution.status == "failed" and execution.paused_task_index is not None:
        self.plan_store.save_execution(
            execution=StoredPlanExecution(
                session_id=session_id,
                plan=stored.plan,
                planner_run_id=stored.planner_run_id,
                source_message_id=source_message_id,
                task_index=execution.paused_task_index,
                task_results=list(execution.plan_run.task_results),
                pending_hitl_id="",
                pause_reason="failed",
                checkpoint_id=new_checkpoint_id(),
                run_metadata=run_metadata,
                completed_operations_cache=completed_ops_cache,  # 新增
            )
        )
```

---

## 5. Eval 设计

### 5.1 Eval Case: `plan_checkpoint_idempotency_prevents_duplicate_write`

```json
{
  "id": "plan_checkpoint_idempotency_prevents_duplicate_write",
  "type": "harness",
  "description": "Resume should skip already-completed mutating operations using idempotency keys.",
  "tags": ["harness", "planner", "worker", "checkpoint", "idempotency"],
  "setup": {
    "planner_stub_mode": "two_step",
    "workspace_files": {
      "data.txt": "initial content"
    }
  },
  "steps": [
    {
      "label": "planner creates two-step plan with write operations",
      "input": {
        "agent_role": "planner",
        "text": "Modify data.txt in stages"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["/execute", "task-2"]
      }
    },
    {
      "label": "execute fails after task-1 writes file",
      "input": {
        "agent_role": "execute",
        "text": "/execute"
      },
      "expect": {
        "status": "failed",
        "disposition": "respond",
        "reply_contains_all": ["execution failed", "task-1", "/execute resume"],
        "state": {
          "workspace_file_content": {
            "data.txt": "modified by task-1"
          }
        }
      }
    },
    {
      "label": "resume skips task-1 write operation via idempotency key",
      "input": {
        "agent_role": "execute",
        "text": "/execute resume"
      },
      "expect": {
        "status": "completed",
        "disposition": "respond",
        "reply_contains_all": ["execution completed successfully"],
        "state": {
          "workspace_file_content": {
            "data.txt": "modified by task-1"
          },
          "latest_agent_run_trace_contains_all": [
            "tool_skipped",
            "idempotency_key"
          ]
        }
      }
    }
  ]
}
```

---

## 6. 实现切片（小步 PR）

### PR-5b-1: 数据模型与工具元数据 ✅

- [x] `TaskResultSpec.completed_operations` 字段
- [x] `StoredPlanExecution.completed_operations_cache` 字段
- [x] `MUTATING_TOOLS` 元数据定义
- [x] 单元测试：`test_task_result_spec_serialization`

### PR-5b-2: Worker 注入与 Tool Executor 检查 ✅

- [x] `src/core/agent/idempotency.py` 模块
- [x] `generate_idempotency_key()` 函数
- [x] `PlanExecutor._run_worker_turn` 注入 `completed_operations`
- [x] `PlanExecutor._extract_completed_operations()` 提取 idempotency keys
- [x] `RuntimeToolExecutor._dispatch_invocation` 检查与跳过逻辑
- [x] `ToolExecutionResult.metadata["idempotency_key"]` 记录
- [x] 单元测试：13 个测试全部通过

### PR-5b-3: Checkpoint 持久化与 Resume ✅

- [x] `_persist_plan_execution_state` 收集 `completed_operations_cache`
- [x] `_resume_plan_from_checkpoint` 传递 `completed_operations`
- [x] 单元测试：`test_checkpoint_persists_and_resumes_with_completed_operations`

### PR-5b-4: Eval 与集成测试 ✅

- [x] `plan_checkpoint_idempotency_prevents_duplicate_write.json`
- [x] 运行 `.\scripts\run_harness_evals.cmd` 确保全绿（36/36 通过）

---

## 7. 边界情况

### 7.1 非幂等工具的处理

对于 `append_to_file`、`send_message` 等非幂等工具：

**选项 A（保守）：** Resume 时拒绝执行，要求用户确认
```python
if not tool_meta["is_idempotent"] and idempotency_key in completed_ops:
    return ToolExecutionResult(
        reply=f"Non-idempotent operation {plan.tool} was partially completed. Manual review required.",
        action="tool_blocked",
        status="failed",
        disposition="clarify",
    )
```

**选项 B（激进）：** 信任 idempotency key，跳过执行
```python
# 与幂等工具相同处理
```

**推荐：选项 A**（Phase 5b 初版），后续可通过配置切换。

### 7.2 Semantic Target 提取失败

如果无法提取语义目标（如 `run_terminal_command` 的命令过于复杂）：

```python
if not target:
    # 降级：使用参数的 hash 作为 target
    import hashlib
    args_json = json.dumps(tool_arguments, sort_keys=True)
    target = hashlib.sha256(args_json.encode()).hexdigest()[:16]
```

### 7.3 跨 Session 的幂等性

当前设计仅在**同一 session 的 plan resume** 内生效。跨 session 不共享 idempotency key。

---

## 8. 验收标准

- [x] `TaskResultSpec` 和 `StoredPlanExecution` 包含幂等性字段
- [x] Mutating 工具有明确的元数据定义
- [x] Resume 时能正确跳过已完成的写操作
- [x] Eval `plan_checkpoint_idempotency_prevents_duplicate_write` 通过
- [x] 现有 36 个 harness eval 仍然全绿
- [x] 单元测试覆盖 idempotency key 生成、检查、持久化逻辑（14 个测试全部通过）

---

## 9. 未来扩展（Phase 5 其它项）

- **PR-5c：** Retry backoff（可重试错误自动退避重试）
- **PR-5d：** MCP 工具挂载（外部工具也受幂等性约束）
- **PR-5e：** Run replay（回放时展示跳过的操作）

---

## 10. 参考

- [cora-phase5-checkpoint-design.md](./cora-phase5-checkpoint-design.md) — Phase 5a checkpoint 基础
- [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) — 第 13.2 节 Idempotency
