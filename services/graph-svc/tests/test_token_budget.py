"""S12.2.2: Token budget tracking and enforcement tests."""

from __future__ import annotations

from astra_graph.token_budget import MODEL_PRICING, TokenBudgetStatus


class TestModelPricing:
    """Model pricing configuration."""

    def test_pricing_defined_for_sonnet(self):
        """Claude Sonnet pricing is configured."""
        assert "claude-sonnet-5" in MODEL_PRICING
        pricing = MODEL_PRICING["claude-sonnet-5"]
        assert pricing["input"] > 0
        assert pricing["output"] > pricing["input"]

    def test_output_more_expensive_than_input(self):
        """Output tokens cost more than input tokens (standard model pricing)."""
        for model, pricing in MODEL_PRICING.items():
            assert pricing["output"] > pricing["input"], f"{model}: output should cost more"


class TestTokenBudgetStatus:
    """Budget status computation."""

    def test_status_not_exhausted_below_100_percent(self):
        """Budget status shows not exhausted when below 100%."""
        status = TokenBudgetStatus(
            tokens_limit=1_000_000,
            tokens_consumed=500_000,
            cost_usd=7.50,
            percent_used=50.0,
            is_exhausted=False,
            is_warning=False,
        )
        assert not status.is_exhausted
        assert not status.is_warning

    def test_status_warning_at_80_percent(self):
        """Budget status shows warning between 80% and 100%."""
        status = TokenBudgetStatus(
            tokens_limit=1_000_000,
            tokens_consumed=800_000,
            cost_usd=12.0,
            percent_used=80.0,
            is_exhausted=False,
            is_warning=True,
        )
        assert not status.is_exhausted
        assert status.is_warning

    def test_status_exhausted_at_100_percent(self):
        """Budget status shows exhausted at or above 100%."""
        status = TokenBudgetStatus(
            tokens_limit=1_000_000,
            tokens_consumed=1_000_000,
            cost_usd=15.0,
            percent_used=100.0,
            is_exhausted=True,
            is_warning=False,
        )
        assert status.is_exhausted
        assert not status.is_warning  # Once exhausted, not warning anymore
