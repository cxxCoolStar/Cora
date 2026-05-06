from __future__ import annotations

from dataclasses import dataclass

from core.schemas.message import Message


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


@dataclass(slots=True)
class ContextBudgetDecision:
    recent_start_index: int
    recent_token_estimate: int
    needs_summary: bool
    total_history_tokens: int
    tail_budget_tokens: int


class ContextBudgetManager:
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

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_length * self.compression_threshold)

    @property
    def tail_budget_tokens(self) -> int:
        return max(512, int(self.threshold_tokens * self.summary_target_ratio))

    def choose_recent_slice(self, *, messages: list[Message]) -> ContextBudgetDecision:
        if not messages:
            return ContextBudgetDecision(
                recent_start_index=0,
                recent_token_estimate=0,
                needs_summary=False,
                total_history_tokens=0,
                tail_budget_tokens=self.tail_budget_tokens,
            )

        total_history_tokens = sum(estimate_message_tokens(message) for message in messages)
        tail_budget_tokens = self.tail_budget_tokens

        recent_tokens = 0
        recent_start = len(messages)
        kept_count = 0
        for index in range(len(messages) - 1, -1, -1):
            message_tokens = estimate_message_tokens(messages[index])
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
