from __future__ import annotations

from semantic_review.config import SemanticReviewConfig
from semantic_review.cost import conservative_call_cost_usd
from semantic_review.errors import SemanticReviewBudgetError


class LiveBudget:
    """Per-run conservative hard cap on live API attempts and USD.

    Scope is one CLI/benchmark/service run, not a persistent account budget.
    Each outbound live provider invocation must call assert_can_call() first
    and consume_attempt() afterward. Retries are additional API calls.
    """

    def __init__(self, config: SemanticReviewConfig) -> None:
        self._config = config
        self.calls = 0
        self.spent_usd = 0.0
        self.reserved_usd = conservative_call_cost_usd(
            config.model,
            config.pricing,
            max_input_tokens=config.max_input_tokens,
            max_output_tokens=config.max_output_tokens,
        )

    def remaining_calls(self) -> int:
        return max(0, self._config.max_live_calls - self.calls)

    def assert_can_call(self) -> None:
        if self.calls >= self._config.max_live_calls:
            raise SemanticReviewBudgetError(
                f"Live semantic-review call cap reached ({self._config.max_live_calls}). "
                "No further provider request is sent."
            )
        if self.spent_usd + self.reserved_usd > self._config.max_live_usd:
            raise SemanticReviewBudgetError(
                f"Live semantic-review USD cap would be exceeded "
                f"(spent={self.spent_usd:.8f}, reserve={self.reserved_usd:.8f}, "
                f"max={self._config.max_live_usd:.8f}). No further provider request is sent."
            )

    def consume_attempt(self, actual_cost_usd: float | None) -> None:
        """Record one outbound live attempt.

        Known usage replaces the reservation with actual cost. Unknown usage
        (failures, missing usage) charges the conservative reservation so the
        attempt is never treated as free.
        """
        self.calls += 1
        if actual_cost_usd is not None:
            self.spent_usd = round(self.spent_usd + actual_cost_usd, 8)
        else:
            self.spent_usd = round(self.spent_usd + self.reserved_usd, 8)
