"""
Pricing resolution.

Model families overlap as substrings, so first-match-wins over a dict bills the
wrong rate. These tests pin the longest-match rule for the cases where the
overlap actually costs money.
"""

from __future__ import annotations

import pytest

from agent_lens.integrations import _pricing
from agent_lens.integrations.anthropic import _ANTHROPIC_PRICING
from agent_lens.integrations.anthropic import _estimate_cost as anthropic_cost
from agent_lens.integrations.openai import _OPENAI_PRICING
from agent_lens.integrations.openai import _estimate_cost as openai_cost


class TestResolve:
    def test_exact_family_resolves(self):
        assert _pricing.resolve(_OPENAI_PRICING, "gpt-4o") == _OPENAI_PRICING["gpt-4o"]

    def test_longest_match_wins_over_contained_family(self):
        # "gpt-4o" is a substring of "gpt-4o-mini"; the more specific key must win.
        assert (
            _pricing.resolve(_OPENAI_PRICING, "gpt-4o-mini")
            == _OPENAI_PRICING["gpt-4o-mini"]
        )

    def test_dated_snapshot_suffix_still_resolves(self):
        assert (
            _pricing.resolve(_OPENAI_PRICING, "gpt-4o-mini-2024-07-18")
            == _OPENAI_PRICING["gpt-4o-mini"]
        )
        assert (
            _pricing.resolve(_ANTHROPIC_PRICING, "claude-3-5-sonnet-20241022")
            == _ANTHROPIC_PRICING["claude-3-5-sonnet"]
        )

    def test_turbo_beats_bare_family(self):
        assert (
            _pricing.resolve(_OPENAI_PRICING, "gpt-4-turbo-preview")
            == _OPENAI_PRICING["gpt-4-turbo"]
        )

    @pytest.mark.parametrize("model", ["", "some-model-we-never-heard-of"])
    def test_unknown_model_resolves_to_none(self, model):
        assert _pricing.resolve(_OPENAI_PRICING, model) is None


class TestEstimateCost:
    def test_gpt_4o_mini_is_not_billed_at_gpt_4o_rates(self):
        """
        The bug this guards: a first-match scan hit "gpt-4o" before
        "gpt-4o-mini" and over-reported a mini call by ~33x.
        """
        mini = openai_cost("gpt-4o-mini", 1_000, 1_000)
        full = openai_cost("gpt-4o", 1_000, 1_000)

        assert mini == pytest.approx(0.00015 + 0.0006)
        assert mini < full
        assert full / mini == pytest.approx(26.666, rel=1e-3)

    def test_openai_table_is_priced_per_thousand_tokens(self):
        assert openai_cost("gpt-4o", 1_000, 0) == pytest.approx(0.005)

    def test_anthropic_table_is_priced_per_million_tokens(self):
        assert anthropic_cost("claude-3-5-sonnet", 1_000_000, 0) == pytest.approx(3.0)

    def test_unknown_model_costs_zero_rather_than_raising(self):
        assert openai_cost("mystery-model", 1_000, 1_000) == 0.0
        assert anthropic_cost("mystery-model", 1_000, 1_000) == 0.0

    def test_zero_tokens_costs_zero(self):
        assert openai_cost("gpt-4o", 0, 0) == 0.0
