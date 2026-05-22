# Cora 项目目标（长期）

> 维护者：个人练手项目，用于系统学习 agent / harness 开发。  
> 最后更新：2026-05-22

## 长期目标

**成为 agent 开发工程师**（能独立设计与实现：工具治理、多步执行、通道集成、可观测与 eval）。

Cora 不仅是「微信文件助手」产品，更是**刻意练习的代码库**：每一阶段对应 agent 系统里的一块真实能力，而不是堆功能。

## 产品目标（次要但真实）

- 解决微信文件传输助手场景：存档、检索、回传文件（见 [README.md](../README.md)）。
- 产品约束反过来训练 agent 设计（例如微信 HITL、沙箱、只读 Planner）。

## 学习路径 ↔ 代码阶段（对照表）

| 阶段 | 在练什么 | 主要代码入口 | 状态 |
|------|----------|--------------|------|
| Phase 1 | Harness 生命周期、run 记录、trace、eval 冒烟 | `DefaultAgentHarness`、`evals/cases/harness/` | ✅ |
| Phase 2 | Tool policy：allow/deny/ask/sandbox；HITL；微信确认 | `ToolPolicyEngine`、`hitl_store`、`channels/wechat/` | ✅ |
| Phase 3a | 结构化计划 schema + 校验（先形状、后模型） | `schemas/plan.py`、`plan_validator.py` | ✅ |
| Phase 3b | Planner harness（只读角色产出 PlanSpec） | 待实现 | ⏳ |
| Phase 3c | 顺序 Worker 执行计划 | 待实现 | ⏳ |
| Phase 4+ | Subagent、checkpoint、MCP 等 | 见 [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) | 未开始 |

## 建议的自学方式（在本仓库内）

1. **跟请求链路**：`WechatGatewayService` → `ClawBotService` → `DefaultAgentHarness._tool_policy_guard`。
2. **用 eval 当规格**：改行为前先加/改 `evals/cases/harness/*.json`，再跑 `.\scripts\run_harness_evals.cmd`。
3. **小步 PR**：与 `docs/cora-phase3-planning-design.md` 中的 PR-3a/3b/3c 切片一致，每步可解释、可测。
4. **文档即笔记**：本文件 + `wechat-hitl.md` + harness 实现 doc；遇到「为什么这样设计」就补一段。

## 对 AI 协作者的期望

- 解释改动时：**先讲 agent 概念，再指文件**，帮助维护者建立心智模型。
- 默认 **不跳过 Phase 2 治理**（policy/HITL/sandbox）去做多 agent 花活。
- 复杂功能优先 **eval + 小 PR**，便于复盘学习。

## 相关文档

- [cora-multi-agent-harness-implementation.md](./cora-multi-agent-harness-implementation.md) — 架构路线图
- [cora-phase3-planning-design.md](./cora-phase3-planning-design.md) — Phase 3 设计
- [wechat-hitl.md](./wechat-hitl.md) — 微信人工确认
