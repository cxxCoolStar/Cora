# WeChat 高风险工具确认（HITL）

Cora 在微信通道上对**需要确认**的工具会暂停执行，等待用户明确同意后再继续。本文说明用户如何操作，以及开发者如何排查。

## 用户怎么用

当 Agent 拟执行高风险操作时，你会收到类似下面的中文确认卡：

```text
需要你确认后我才会继续：

· 操作：定时提醒（scheduled_tasks）
· 说明：查看当前提醒列表

请直接回复：
  确认 — 执行
  拒绝 — 取消

（约 10 分钟内有效；超时请重新发起请求）
```

| 你的回复 | 结果 |
|----------|------|
| `确认` / `同意` / `执行` | 批准并执行该操作 |
| `拒绝` / `取消` / `不要` | 取消，不执行 |
| 其它文字或再发文件 | 若仍有待确认项，会提示你先确认或拒绝 |

**超时**：待确认项默认 **10 分钟** 有效。超时后回复 `确认` 会提示已过期，需要重新发起原请求。

## 哪些工具可能触发确认

在 `wechat_safe` 策略下（微信默认）：

| 类型 | 示例 | 说明 |
|------|------|------|
| 始终拒绝 | `shell_exec` | 微信通道不允许 shell |
| 需确认（中/高风险） | `scheduled_tasks` 等带 `requires_confirmation` 的工具 | 非 CLI 平台会 `ask` |
| 沙箱执行 | `write_file` 等可变工具 | 在 `.cora/sandboxes/{run_id}/workspace/` 隔离执行 |

CLI / API 本地调试时，高风险 tool 可能自动放行（`platform=cli`），与微信行为不同。

## 处理中的进度提示

长任务（归档、主题分类、生成最终回复）可能耗时 1–2 分钟。Gateway 会在处理期间向微信发送**多条**短消息，避免看起来像「卡住」：

| 时机 | 示例文案 |
|------|----------|
| 开始处理 | `收到，正在处理你的请求…` |
| 工具执行（`skill_run` / `archive_run`） | `正在归档处理…` |
| 入库完成、仍在生成回复 | `已写入资料库，正在整理回复…` |
| 超过心跳间隔仍在跑 | `还在处理中，请稍等…`（默认每 90s） |

环境变量（前缀 `CORA_`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `WECHAT_PROGRESS_ENABLED` | `true` | 关闭后不再发进度消息 |
| `WECHAT_PROGRESS_HEARTBEAT_SECONDS` | `90` | 长任务心跳间隔（秒）；`0` 关闭心跳 |
| `WECHAT_PROGRESS_TOOL_UPDATES` | `true` | 是否在工具开始/归档完成时推送 |
| `WECHAT_PROGRESS_MIN_INTERVAL_SECONDS` | `12` | 进度消息最小间隔，避免刷屏 |

实现：`src/core/channels/wechat/progress.py`（Poller 包裹整次 `handle_inbound_event`；工具层通过 context var 挂钩）。

不会为 `确认`/`拒绝`、仅 `/new` 等极短指令发 ACK。

## 开发者接口

HTTP（会话维度）：

```http
GET  /sessions/{session_id}/hitl/{hitl_id}
POST /sessions/{session_id}/runs/{run_id}/hitl/{hitl_id}/approve
POST /sessions/{session_id}/runs/{run_id}/hitl/{hitl_id}/reject
```

服务层：

- `ClawBotService.approve_hitl_and_resume`
- `ClawBotService.reject_hitl`
- `ClawBotService.get_latest_pending_hitl`

持久化表：`clawbot_hitl_requests`（含 `expires_at`）。

## 相关 Trace 事件

| 事件 | 含义 |
|------|------|
| `tool.requested` | 已创建待确认，未执行 |
| `tool.hitl.approved` | 用户已批准，开始执行 |
| `tool.completed` | 工具执行结束 |
| `tool.sandbox.applied` | 在沙箱工作区执行 |

## Harness 回归

```powershell
.\scripts\run_harness_evals.cmd
```

相关 case：`wechat_hitl_confirm_command`、`high_risk_tool_requests_confirmation`、`hitl_approve_and_resume`。

更完整的 harness 设计见 [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md)。
