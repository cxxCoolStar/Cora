# Cora 生产级 Multi-Agent Harness 实施蓝图

## 1. 背景与目标
根据 `cora-general-agent-direction.md` 的规划，Cora 正从“文件归档助手”向“通用生产级智能 Agent”转型。结合《从零设计生产级 Multi-Agent Harness》的核心理念，仅仅依靠 Prompt 拼接和简单的工具调用无法满足生产级 Agent 对稳定性、安全性和可追溯性的要求。

本文档详细规划了 Cora 在 **架构编排、工具治理、状态与记忆、评估体系、成本控制及 MCP 接入** 五大核心模块的演进与实施路径。

---

## 2. 核心模块实施方案

### 2.1 架构编排：引入全局 Harness (Orchestrator)
**当前痛点**：核心调度逻辑耦合在业务循环中，Agent 自主决定流程，容易失控或陷入死循环。
**设计改造**：
* **提取纯粹的 Orchestrator**：将 `src/core/agent/turn_runner.py` 升级为核心调度器，接管所有决策权（任务生命周期、执行计划裁决、失败处理）。
* **引入声明式计划 (Declarative Planning)**：如果引入 Planner Agent，其输出必须是结构化的执行意图（如 JSON 格式的步骤和依赖），由 Orchestrator 负责解释和分配给 Worker Agents。
* **设立四道硬终止网关**：强制配置 `max_steps`（最大步数）、`max_tokens`（最大消耗）、`max_duration`（最大耗时）、`max_tool_calls`（最大工具调用频次），防止资源黑洞。

### 2.2 工具治理：建立 Tool Registry 安全边界
**当前痛点**：工具调用相对自由，缺乏统一的安全校验、限流和审计拦截。
**设计改造**：
* **丰富 Tool Registry 元数据**：扩展现有的 `src/core/tools/registry.py`。每个工具除了基础的 Schema 和 Description，必须增加：
  - **RBAC（权限管理）**：指定允许调用的子 Agent 列表。
  - **风险分级**：标记高/中/低风险。
  - **超时与速率限制 (Rate Limit)**。
* **加入 Human-in-the-Loop (HITL)**：对于高风险操作（如 `shell_exec` 终端执行、`write_file` 危险写入），拦截调用并请求人工授权，避免“脱缰运行”。

### 2.3 状态与记忆：状态与记忆分层解耦
**当前痛点**：上下文容易膨胀，缺乏分层管理，有用信息与临时状态混合。
**设计改造**：
* **状态 (State)** 负责临时运行数据：
  - **Working State**：当前步骤的临时上下文，任务结束即丢弃。
  - **Session State**：一次会话内多个 Agent 共享的信息（如 Redis TTL 缓存）。
* **记忆 (Memory)** 负责长期复用知识：
  - **分离 Episodic Memory (事件/偏好) 与 Semantic Memory (业务规则/领域概念)**。
  - **混合检索策略**：不直接塞满 Prompt。采用“前置注入核心高分记忆 + 提供 `memory_search` 工具供 Agent 按需检索”。
  - **定期遗忘与修剪**：基于访问频次、时间、重要性计算保留分数（低分删除、中分压缩摘要、高分保留原文）。

### 2.4 评估体系：落地四层 Eval Pipeline
**当前痛点**：缺乏丰富的自动化回归和能力测试，尤其是非 Archive 场景的测试用例和中间轨迹审查。
**设计改造**：在 `src/core/evals` 基础上完善四个层级：
1. **Component Eval (组件评估)**：单 Agent 是否选对了工具、参数是否合规。
2. **Trajectory Eval (轨迹评估)**：步骤是否必要、是否重复调用、有无陷入死循环（重点）。
3. **Task Completion Eval (任务完成度)**：是否满足最终业务目标，是否存在幻觉。
4. **End-to-End Eval (端到端业务效果)**：单位任务的 Token 成本和延迟是否在指标内。
* **引入 Fixtures 机制**：建立标准化的测试沙箱，隔离数据库和缓存环境。

### 2.5 成本控制：引入 Token Budget 调度
**当前痛点**：缺乏实时成本意识，容易出现由于长历史或者重试导致的 Token 爆炸。
**设计改造**：
* **Model Routing (模型路由)**：低难度任务（摘要、意图分类）强制路由给低成本小模型，复杂推理分配给主力大模型。
* **Budget 分级降级 (熔断保护)**：
  - **黄区 (20%-50% 预算)**：触发 Context Compression（上下文压缩），折叠早期历史对话。
  - **红区 (<20% 预算)**：切断思维链 (CoT)，仅允许核心工具调用。
  - **熔断区 (<5% 预算)**：直接中止，返回 partial result（部分结果）给用户。

### 2.6 MCP (Model Context Protocol) 接入安全
* **规范接入**：第三方 MCP Server 绝不直接暴露给 Agent，必须挂载在 Cora 的 Tool Registry 之下。
* **白名单与配额**：为每个 MCP Server 设置独立的限流配额。对 MCP 暴露的工具实行白名单机制，并保持对敏感 API 的 HITL 拦截机制。

---

## 3. 实施路径 (Roadmap)

### Phase 1: MVP 闭环基础重构 (1-2周)
* **目标**：跑通安全的端到端业务闭环。
* **行动**：
  1. 重构 `turn_runner.py` 为标准化的 Orchestrator。
  2. 升级 `Tool Registry`，对现有的 `file`, `web`, `terminal` 等工具打上权限和风险标签。
  3. 完善基础 Trace 与运行日志收集，为 Eval 铺垫。

### Phase 2: 安全加固与预算控制 (2-3周)
* **目标**：把 Demo 变成可控、可靠的系统。
* **行动**：
  1. 上线 Token Budget 控制模块和上下文压缩策略。
  2. 落地高风险工具的 Human-in-the-Loop 审核机制。
  3. 建设 Eval 体系的 Fixtures 沙箱，完善 Component 和 Trajectory 的回归测试。

### Phase 3: 规模化扩展与通用融合 (长期)
* **目标**：真正实现复杂 Multi-Agent 编排与外部生态对接。
* **行动**：
  1. 接入外部标准 MCP Server（如 Git, 数据库等）。
  2. 上线动态记忆分层与检索修剪机制。
  3. 引入多路路由和并行分布式规划机制（基于 Declarative Planning）。
