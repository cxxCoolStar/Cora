# 微信渠道 /replay 命令测试指南

> 如何通过微信测试 Plan 执行回放功能

## 前提条件

1. ✅ Cora 服务已启动并连接到微信
2. ✅ 你有一个可以发送消息的微信账号
3. ✅ Phase 5 的所有功能已实现（checkpoint, idempotency, retry, replay）

## 测试场景 1：查看失败执行的 Replay

### 步骤 1：创建一个会失败的 Plan

发送给微信文件传输助手：
```
/plan 帮我搜索 src 目录下的文件，然后写入一个新文件
```

**预期响应**：
- Cora 会返回一个 3 步计划
- 提示你使用 `/execute` 执行

### 步骤 2：执行 Plan（会在某个 task 失败）

发送：
```
/execute
```

**预期响应**：
- Task-1 可能成功（search_files）
- Task-2 可能失败（write_file 被 policy 拒绝）
- 返回失败消息，提示可以使用 `/execute resume` 或 `/execute restart`

### 步骤 3：查看 Replay 报告（Markdown 格式）

发送：
```
/replay
```

**预期响应**：
```markdown
# Plan Execution Report

**Plan ID**: plan-abc123
**Status**: failed
**Total Time**: 5.2s
**Total Retries**: 0

## task-1: Search workspace files
- **Status**: completed
- **Retries**: 0
- **Time**: 2.1s

### Operations:
[000.0] `search_files(path="src")` → completed (1.2s)

## task-2: Write results to file
- **Status**: failed
- **Retries**: 0
- **Time**: 1.5s
- **Error Category**: permission_denied
- **Error**: Tool policy denied: write_file is not allowed

### Operations:
[002.1] `write_file(path="results.txt")` → failed
  - Error: Tool policy denied: write_file is not allowed
```

### 步骤 4：查看 Replay 报告（JSON 格式）

发送：
```
/replay json
```

**预期响应**：
```json
{
  "plan_id": "plan-abc123",
  "status": "failed",
  "total_time_seconds": 5.2,
  "total_retries": 0,
  "tasks": [
    {
      "task_id": "task-1",
      "title": "Search workspace files",
      "status": "completed",
      "retry_count": 0,
      "start_time": 0.0,
      "end_time": 2.1,
      "duration_seconds": 2.1,
      "error_category": null,
      "last_error": null,
      "operations": [...]
    },
    {
      "task_id": "task-2",
      "title": "Write results to file",
      "status": "failed",
      "retry_count": 0,
      "start_time": 2.1,
      "end_time": 3.6,
      "duration_seconds": 1.5,
      "error_category": "permission_denied",
      "last_error": "Tool policy denied: write_file is not allowed",
      "operations": [...]
    }
  ]
}
```

---

## 测试场景 2：查看带重试的 Replay

### 步骤 1：创建一个可能需要重试的 Plan

发送：
```
/plan 搜索 tests 目录，然后读取找到的文件
```

### 步骤 2：执行 Plan

发送：
```
/execute
```

**说明**：
- 如果某个 task 遇到临时错误（如 timeout），会自动重试
- 最多重试 3 次，使用指数退避策略

### 步骤 3：查看 Replay（包含重试信息）

发送：
```
/replay
```

**预期响应**：
```markdown
# Plan Execution Report

**Plan ID**: plan-def456
**Status**: completed
**Total Time**: 15.3s
**Total Retries**: 2

## task-1: Search tests directory
- **Status**: completed
- **Retries**: 0
- **Time**: 2.0s

## task-2: Read found files
- **Status**: completed
- **Retries**: 2
- **Time**: 10.5s
- **Error Category**: timeout (最后一次重试前的错误)

### Operations:
[002.0] `read_file(path="test.py")` → failed (timeout)
  - Retry after 1.2s
[003.7] `read_file(path="test.py")` → failed (timeout)
  - Retry after 2.5s
[006.7] `read_file(path="test.py")` → completed (3.8s)
```

---

## 测试场景 3：查看带 Idempotency 的 Replay

### 步骤 1：创建一个包含写操作的 Plan

发送：
```
/plan 创建一个配置文件，然后读取它
```

### 步骤 2：执行 Plan（假设在 task-2 失败）

发送：
```
/execute
```

**预期**：
- Task-1 成功写入文件
- Task-2 失败
- Checkpoint 保存了 task-1 的 completed_operations

### 步骤 3：查看失败后的 Replay

发送：
```
/replay
```

**预期响应**：
```markdown
# Plan Execution Report

**Plan ID**: plan-ghi789
**Status**: failed
**Total Time**: 3.5s
**Total Retries**: 0

## task-1: Create config file
- **Status**: completed
- **Retries**: 0
- **Time**: 1.8s

### Operations:
[000.0] `write_file(path="config.py")` → completed (1.8s)

## task-2: Read config file
- **Status**: failed
- **Retries**: 0
- **Time**: 0.5s
```

### 步骤 4：Resume 执行

发送：
```
/execute resume
```

**说明**：
- Task-1 的 write_file 操作会被跳过（idempotency）
- 直接从 task-2 重试

### 步骤 5：Resume 后查看 Replay

**注意**：Resume 成功后，checkpoint 会被清除，所以要在 resume 之前查看 replay！

如果想看到 idempotency 跳过的操作，应该在 resume 之前发送：
```
/replay
```

---

## 常见问题

### Q1: 为什么 `/replay` 返回 "No execution history found"？

**原因**：
- Plan 执行成功完成后，checkpoint 会被清除
- Replay 只能查看失败或暂停的执行历史

**解决方案**：
- 在执行失败后立即调用 `/replay`
- 或者在 resume 之前调用 `/replay`

### Q2: 如何查看成功完成的 Plan 的 Replay？

**当前限制**：
- 成功完成的 plan 的 checkpoint 会被清除
- 无法查看已完成 plan 的 replay

**未来改进**：
- 可以考虑在 Phase 6 中添加执行历史持久化
- 将 replay 数据保存到单独的存储中

### Q3: Replay 报告中的时间戳是什么意思？

**时间戳格式**：
- `[000.0]` 表示相对于 plan 开始的秒数
- 例如 `[002.1]` 表示 plan 开始后 2.1 秒

### Q4: 如何在微信中查看 JSON 格式的 Replay？

发送：
```
/replay json
```

**注意**：
- JSON 格式可能很长，微信可能会截断
- 建议用于程序化处理或调试

---

## 命令速查表

| 命令 | 说明 | 使用场景 |
|------|------|----------|
| `/plan <任务描述>` | 创建执行计划 | 开始一个新的多步任务 |
| `/execute` | 执行计划 | 首次执行或失败后自动 resume |
| `/execute resume` | 显式 resume | 从失败的 task 继续执行 |
| `/execute restart` | 重新开始 | 清除 checkpoint，从头执行 |
| `/replay` | 查看 Markdown 格式的执行报告 | 了解执行历史、重试、跳过的操作 |
| `/replay json` | 查看 JSON 格式的执行报告 | 程序化处理或详细调试 |

---

## 测试检查清单

- [ ] 能够创建 plan（`/plan`）
- [ ] 能够执行 plan（`/execute`）
- [ ] 执行失败后能查看 replay（`/replay`）
- [ ] Replay 显示正确的 task 状态
- [ ] Replay 显示重试次数（如果有重试）
- [ ] Replay 显示错误信息（如果失败）
- [ ] JSON 格式的 replay 正常工作（`/replay json`）
- [ ] Resume 后能继续执行（`/execute resume`）
- [ ] Restart 能清除 checkpoint（`/execute restart`）

---

## 调试技巧

### 1. 查看日志

如果 replay 命令不工作，检查服务器日志：
```bash
# 查找 replay 相关日志
grep "plan_replay" logs/cora.log

# 查找 execute_plan_outcome 日志
grep "execute_plan_outcome" logs/cora.log
```

### 2. 检查 Checkpoint

确认 checkpoint 是否存在：
```python
# 在 Python REPL 中
from core.agent.plan_store import InMemoryPlanStore
store = InMemoryPlanStore()
execution = store.get_execution(session_id="your-session-id")
print(execution)
```

### 3. 测试 Replay 命令解析

```python
from core.agent.plan_execute import parse_replay_command

# 测试命令解析
cmd = parse_replay_command("/replay")
print(cmd)  # ReplayCommand(format='markdown')

cmd = parse_replay_command("/replay json")
print(cmd)  # ReplayCommand(format='json')
```

---

## 下一步

完成测试后，你可以：
1. 在实际场景中使用 replay 功能调试 plan 执行问题
2. 基于 replay 数据分析性能瓶颈
3. 考虑实现 Phase 6 的可观测性增强功能
