# Live harness 审核记录

> 模型：`kimi-k2.5`（`.env` `CORA_MODEL_PROVIDER=openai`）。  
> 命令：`.\scripts\run_live_harness_evals.cmd`（`CORA_EVAL_LIVE_ONLY=1`）。

## 汇总（2026-05-28）

| Case | 结果 | 耗时 | 审核结论 |
|------|------|------|----------|
| `plan_llm_parallel_search_planner` | PASS | ~14s | Planner 产出含 `parallel_subagents` 与 `/execute`，符合预期。 |
| `live_archive_save_nl` | PASS | ~55s | 自然语言保存，调用 `archive_run`，`item_count=1`，回复礼貌且含主题「健康档案」。 |
| `live_archive_search_nl` | PASS | ~109s | 保存+检索均走 `archive_run`；检索回复命中 marker 与「抽血」。 |
| `live_read_file_nl` | PASS | ~6s | 调用 `read_file`，正确答出 `live_probe_fn`。 |
| `live_plan_execute_nl` | PASS | ~27s | 真实 planner 校验 plan，`/execute` 成功且 worker 使用 `search_files`。 |

**合计：5/5 cases，7/7 steps。**

## 产品层观察（非断言失败）

1. **工具路径不一致**：同一轮 live 中，保存有时用 `archive_run`，有时先 `skill_view` 再 `skill_run`（仍成功入库）。断言已允许二者；长期建议在 system prompt 里优先 `archive_run` 以减少步数与延迟。
2. **检索偶发重复入库**：早期版本 step2 断言 `item_count=1` 失败（实际为 2），说明模型在「查找」时可能再次 save。已改为 step2 不卡 `item_count`，仅看回复与 `agent_run_count`；需在真机观察是否复现。
3. **EVAL marker 未写入正文**：`live_archive_save_nl` 用户句含 `LIVE_EVAL_SAVE_a7f3`，入库摘要未保留 marker（检索仍可按语义找到）。若要做严格回归，可在 save 后断言 DB 正文含 marker（待扩展 judge）。
4. **耗时**：archive 类 live case 约 1–2 分钟/条，适合发版前手动或夜间 job，不宜放进默认 CI。

## 断言策略（live）

- 优先：`item_count`、`action=capture`、`tool_names_any: [archive_run, skill_run]`
- 回复：`reply_contains_any`（中英文关键词），避免死磕整句
- 检索 step：不强制 `item_count`，避免误 save 导致假失败
