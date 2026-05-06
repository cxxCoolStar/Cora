from __future__ import annotations

import math
from dataclasses import dataclass

from core.schemas.message import Message
from core.schemas.tool import ToolSpec


def estimate_text_tokens(text: str) -> int:
    compact = text or ""
    # Rough heuristic similar to common chat budgeting: ~4 chars per token,
    # with a small per-message floor so short messages are not free.
    return max(1, (len(compact) + 3) // 4)


def estimate_message_tokens(message: Message) -> int:
    tokens = estimate_text_tokens(message.content)
    tokens += 8  # role / envelope overhead
    if message.name:
        tokens += estimate_text_tokens(message.name)
    if message.tool_calls:
        tokens += 16
    return tokens


def estimate_tool_spec_tokens(tool: ToolSpec) -> int:
    schema_text = str(tool.input_schema or {})
    return (
        estimate_text_tokens(tool.name)
        + estimate_text_tokens(tool.description)
        + estimate_text_tokens(schema_text)
        + 24
    )


@dataclass(slots=True)
class ContextBudgetDecision:
    recent_start_index: int
    recent_token_estimate: int
    needs_summary: bool
    total_history_tokens: int
    tail_budget_tokens: int


class ContextBudgetManager:
    SCALE_EWMA_ALPHA = 0.20
    MIN_PROMPT_SCALE = 0.50
    MAX_PROMPT_SCALE = 3.00

    def __init__(
        self,
        *,
        context_length: int = 128_000,
        compression_threshold: float = 0.50,
        summary_target_ratio: float = 0.20,
        protect_last_n_min: int = 8,
    ) -> None:
        self.context_length = max(8_192, int(context_length))
        self.compression_threshold = max(0.10, min(float(compression_threshold), 0.95))
        self.summary_target_ratio = max(0.05, min(float(summary_target_ratio), 0.80))
        self.protect_last_n_min = max(2, int(protect_last_n_min))
        self._prompt_token_scale = 1.0
        self._last_estimated_prompt_tokens = 0
        self._last_actual_prompt_tokens = 0

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_length * self.compression_threshold)

    @property
    def tail_budget_tokens(self) -> int:
        return max(512, int(self.threshold_tokens * self.summary_target_ratio))

    @property
    def prompt_token_scale(self) -> float:
        return self._prompt_token_scale

    @property
    def last_estimated_prompt_tokens(self) -> int:
        return self._last_estimated_prompt_tokens

    @property
    def last_actual_prompt_tokens(self) -> int:
        return self._last_actual_prompt_tokens

    def estimate_messages_tokens(self, *, messages: list[Message], calibrated: bool = True) -> int:
        raw_total = sum(estimate_message_tokens(message) for message in messages)
        if not calibrated:
            return raw_total
        return self._apply_scale(raw_total)

    def estimate_prompt_tokens(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        calibrated: bool = True,
    ) -> int:
        raw_total = self.estimate_messages_tokens(messages=messages, calibrated=False)
        raw_total += sum(estimate_tool_spec_tokens(tool) for tool in tools)
        if not calibrated:
            return raw_total
        return self._apply_scale(raw_total)

    def observe_prompt_usage(self, *, estimated_prompt_tokens: int, actual_prompt_tokens: int) -> None:
        estimated = max(1, int(estimated_prompt_tokens))
        actual = max(1, int(actual_prompt_tokens))
        observed_scale = max(
            self.MIN_PROMPT_SCALE,
            min(self.MAX_PROMPT_SCALE, actual / estimated),
        )
        self._prompt_token_scale = (
            (1.0 - self.SCALE_EWMA_ALPHA) * self._prompt_token_scale
            + self.SCALE_EWMA_ALPHA * observed_scale
        )
        self._last_estimated_prompt_tokens = estimated
        self._last_actual_prompt_tokens = actual

    def choose_recent_slice(self, *, messages: list[Message]) -> ContextBudgetDecision:
        if not messages:
            return ContextBudgetDecision(
                recent_start_index=0,
                recent_token_estimate=0,
                needs_summary=False,
                total_history_tokens=0,
                tail_budget_tokens=self.tail_budget_tokens,
            )

        total_history_tokens = self.estimate_messages_tokens(messages=messages, calibrated=True)
        tail_budget_tokens = max(256, int(self.tail_budget_tokens / max(self._prompt_token_scale, 0.01)))

        recent_tokens = 0
        recent_start = len(messages)
        kept_count = 0
        for index in range(len(messages) - 1, -1, -1):
            message_tokens = self._apply_scale(estimate_message_tokens(messages[index]))
            would_exceed = recent_tokens + message_tokens > tail_budget_tokens
            if kept_count >= self.protect_last_n_min and would_exceed:
                break
            recent_tokens += message_tokens
            recent_start = index
            kept_count += 1

        needs_summary = recent_start > 0
        return ContextBudgetDecision(
            recent_start_index=recent_start,
            recent_token_estimate=recent_tokens,
            needs_summary=needs_summary,
            total_history_tokens=total_history_tokens,
            tail_budget_tokens=tail_budget_tokens,
        )

    def _apply_scale(self, raw_tokens: int) -> int:
        return max(1, int(math.ceil(max(0, raw_tokens) * self._prompt_token_scale)))
