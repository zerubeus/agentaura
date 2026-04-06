"""Tests for pricing and cost computation."""

from agentaura.core.events import TokenUsage
from agentaura.core.pricing import MODEL_PRICING, compute_cost, get_pricing


def test_known_models_have_pricing():
    assert "claude-opus-4-6" in MODEL_PRICING
    assert "claude-sonnet-4-5-20250929" in MODEL_PRICING
    assert "claude-haiku-4-5-20251001" in MODEL_PRICING


def test_opus_pricing():
    p = get_pricing("claude-opus-4-6")
    assert p.input_per_m == 5.0
    assert p.output_per_m == 25.0


def test_fallback_pricing_for_unknown():
    p = get_pricing("unknown-model-xyz")
    # Should return fallback (Sonnet pricing)
    assert p.input_per_m == 3.0


def test_compute_cost_simple():
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    cost = compute_cost(usage, "claude-opus-4-6")
    # 1M * 5/1M + 100K * 25/1M = 5 + 2.5 = 7.5
    assert abs(cost - 7.5) < 0.001


def test_compute_cost_with_cache():
    usage = TokenUsage(
        input_tokens=500,
        output_tokens=50,
        cache_creation_input_tokens=1000,
        cache_read_input_tokens=200,
    )
    cost = compute_cost(usage, "claude-opus-4-6")
    expected = (500 * 5.0 + 50 * 25.0 + 1000 * 6.25 + 200 * 0.50) / 1_000_000
    assert abs(cost - expected) < 0.0001


def test_compute_cost_zero_tokens():
    usage = TokenUsage()
    cost = compute_cost(usage, "claude-opus-4-6")
    assert cost == 0.0
