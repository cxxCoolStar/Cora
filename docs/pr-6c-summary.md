# PR-6c: MCP Tool Policy 与 HITL 集成 - 完成总结

> 状态：✅ 已完成  
> 日期：2026-05-26  
> 前置：PR-6a（MCP Client）、PR-6b（Tool Registry 集成）

## 实现内容

### 1. Policy Engine 扩展

**文件**：`src/core/agent/tool_policy_engine.py`

- `is_mcp_tool()` / `matches_any_pattern()`：识别 MCP 工具与 fnmatch 通配符
- `profile_has_mcp_policy()` / `mcp_tool_uses_profile_policy()`：判断 profile 是否治理 MCP
- `evaluate()` 在 builtin `allowed_tool_names` 检查之后、HITL 之前插入 MCP 策略分支：
  - denied patterns → `mcp_pattern_denied`
  - ask patterns → `mcp_pattern_requires_confirmation`
  - allowed patterns + default policy → allow / ask / deny
- `should_expose_tool()`：MCP 工具不再被 readonly profile 的 builtin allow list 误过滤

### 2. Policy Profile 配置

**文件**：`src/core/agent/policy_profiles.py`

- `HarnessPolicyProfile` 新增 `mcp_default_policy`、`mcp_*_patterns` 字段
- `wechat_safe`：默认 ask，拒绝 `mcp_*_shell|exec|delete`
- `background_readonly` / `planner_readonly`：仅允许 read/query/list/search 类 MCP 工具
- `coding_full`：`mcp_default_policy=allow`

### 3. 运行时接线

**文件**：`src/core/mcp/runtime.py`、`src/core/clawbot/dependencies.py`、`src/core/cli/main.py`

- `CORA_MCP_ENABLED=true` 时，gateway 启动调用 `connect_mcp_if_enabled()`
- 连接成功后重建 `ToolManager(mcp_manager=...)` 并 `refresh_tool_specs()`
- gateway 退出时 `disconnect_mcp()`

**配置**（`src/core/config.py`）：

```env
CORA_MCP_ENABLED=true
CORA_MCP_CONFIG_PATH=config/mcp_servers.json
```

### 4. 测试与 Eval

| 类型 | 文件 | 结果 |
|------|------|------|
| 单元测试 | `tests/test_mcp_tool_policy.py` | 通过 |
| 集成测试 | `tests/test_mcp_tool_policy_integration.py` | 4/4 通过 |
| Harness eval | `evals/cases/harness/mcp_tool_respects_policy.json` | 4 steps 通过 |
| 全量 harness | `.\scripts\run_harness_evals.cmd` | **40/40** 通过 |

## 验收标准对照

- ✅ MCP 工具受 policy 约束（与 builtin 共用 `ToolPolicyEngine` + harness guard）
- ✅ 支持 allow/deny/ask 通配符与 default policy
- ✅ `wechat_safe` 下 MCP 工具触发 HITL ask（`mcp_default_policy_ask`）
- ✅ 无回归：原有 39 个 harness case 仍通过

## 下一步（PR-6d / 6e）

- **6d**：MCP mutating 工具的 idempotency key 与 retry 错误分类
- **6e**：用户向 MCP 集成指南、示例 server、`mcp_tool_discovery_and_execution` eval

## 相关文档

- [cora-phase6-mcp-integration-design.md](./cora-phase6-mcp-integration-design.md)
- [pr-6b-summary.md](./pr-6b-summary.md)
