"""
agent_lens.integrations._pricing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared model-name → pricing resolution for the provider integrations.

Model names are versioned and dated (``gpt-4o-mini``, ``claude-3-5-sonnet-20241022``),
so a pricing table is keyed by family prefix and matched as a substring. The match
must be the *longest* key that fits: ``gpt-4o`` is a substring of ``gpt-4o-mini``, so
a first-match-wins scan bills a mini call at full ``gpt-4o`` rates.
"""

from __future__ import annotations

Pricing = dict[str, float]
PricingTable = dict[str, Pricing]


def resolve(table: PricingTable, model: str) -> Pricing | None:
    """
    Return the pricing entry for ``model``, or None when no family matches.

    The longest matching key wins, so a more specific family always beats the
    less specific one it contains.
    """
    if not model:
        return None

    best_key: str | None = None
    for key in table:
        if key in model and (best_key is None or len(key) > len(best_key)):
            best_key = key

    return table[best_key] if best_key is not None else None


def estimate_cost(
    table: PricingTable,
    model: str,
    input_tokens: int,
    output_tokens: int,
    per_tokens: int,
) -> float:
    """
    Estimate USD cost for a call.

    ``per_tokens`` is the unit the table is quoted in — 1_000 for tables priced
    per 1K tokens, 1_000_000 for tables priced per 1M tokens.
    """
    pricing = resolve(table, model)
    if pricing is None:
        return 0.0

    return (
        input_tokens * pricing["input"] / per_tokens
        + output_tokens * pricing["output"] / per_tokens
    )
