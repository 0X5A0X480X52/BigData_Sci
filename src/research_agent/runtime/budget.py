"""Budget guardrails for tool execution and agent iterations."""

from __future__ import annotations

from dataclasses import dataclass, field

from research_agent.core.models import RunConfig


class BudgetExceededError(RuntimeError):
    """Raised when a budget limit is exceeded."""


@dataclass
class BudgetTracker:
    config: RunConfig
    tool_calls: int = 0
    retries: int = 0
    iterations: int = 0

    # ── Token / cost stubs (populated when LLM is wired in later phases) ──
    estimated_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def consume_tool_call(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.config.max_tool_calls:
            raise BudgetExceededError(
                f"Tool-call budget exceeded: {self.tool_calls}>{self.config.max_tool_calls}"
            )

    def consume_retry(self) -> None:
        self.retries += 1
        if self.retries > self.config.max_retries:
            raise BudgetExceededError(
                f"Retry budget exceeded: {self.retries}>{self.config.max_retries}"
            )

    def consume_iteration(self) -> None:
        """Call once per ReAct loop iteration."""
        self.iterations += 1
        if self.iterations > self.config.max_iterations:
            raise BudgetExceededError(
                f"Iteration budget exceeded: {self.iterations}>{self.config.max_iterations}"
            )

    @property
    def remaining_iterations(self) -> int:
        return max(0, self.config.max_iterations - self.iterations)

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.config.max_tool_calls - self.tool_calls)
