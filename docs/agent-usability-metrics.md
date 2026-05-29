# Agent 可用性指标（Cora）

> 把「更好用」拆成可回归的 harness 任务，而不是只看模型 benchmark。

## 三层度量

| 层 | 作用 | Cora 入口 |
|----|------|-----------|
| 组件 / Trace | policy、沙箱、幂等是否正确 | `evals/cases/harness/*` trace 断言 |
| 任务成功率（TSR） | 用户任务是否一次完成 | 本文 **T1–T5** 对应用例 |
| 真人 / 产品 | 主观满意度、留存 | 发版前抽检（未自动化） |

## 任务清单 ↔ Harness

| ID | 用户任务 | Harness case | 关键断言 |
|----|----------|--------------|----------|
| T1 | 保存文本笔记 | `archive_save_then_search` step 1 | `item_count=1`, `archive_run`, `capture` |
| T2 | 按语义检索 | `archive_save_then_search` step 2 | 回复含保存内容 |
| T3 | 微信保存 + 进度 | `wechat_progress_on_save` | 进度含「记入资料库」「正在归档」 |
| T4 | 微信检索 ACK | `wechat_find_progress_ack` | 进度含「资料库里查找」 |
| T5 | Plan 执行成功 | `plan_execute_task_success` | `execution completed successfully` |
| — | 工具后整理回复进度 | `wechat_archive_save_llm_compose_progress` | 进度含「整理回复」 |

运行（stub，默认 CI / 日常）：

```powershell
.\scripts\run_harness_evals.cmd
```

运行（**真实 LLM**，仅 `live` 标签用例，需 `.env` 中 `CORA_OPENAI_API_KEY`）：

```powershell
.\scripts\run_live_harness_evals.cmd
```

| Live case | 测什么 |
|-----------|--------|
| `plan_llm_parallel_search_planner` | Planner 产出 `parallel_subagents` |
| `live_archive_save_nl` | 中文自然语言保存 → `archive_run` + `item_count=1` |
| `live_archive_search_nl` | 中文保存后自然语言检索 |
| `live_read_file_nl` | 自然语言读文件 → `read_file` |
| `live_plan_execute_nl` | 真实 planner + `/execute` worker |

## 与 openai/evals 的关系

- [openai/evals](https://github.com/openai/evals)：偏 **模型输出质量** 与通用 benchmark。
- Cora harness：偏 **产品 agent**（微信、归档、policy、plan、进度）。

两者互补：换模型时可加一小套 openai/evals 作回归；**产品是否更好用** 以本页 T1–T5 为准。

## 扩展指引

新增任务时：

1. 在 `evals/cases/harness/` 增加 JSON（优先 `/tool` 或 `channel: wechat` 可重复路径）。
2. 在本表登记 T 编号与断言摘要。
3. 跑全量 harness，确保 全绿。
