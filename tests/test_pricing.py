"""Tests for pricing and cost computation."""

from pathlib import Path

from agentaura.core.events import TokenUsage
from agentaura.core.pricing import (
    MODEL_PRICING,
    compute_cost,
    compute_cost_from_counts,
    get_pricing,
    load_session_costs,
)


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


# --- compute_cost_from_counts ---


def test_cost_from_counts_matches_compute_cost():
    usage = TokenUsage(
        input_tokens=1000,
        output_tokens=500,
        cache_creation_input_tokens=200,
        cache_read_input_tokens=100,
    )
    cost1 = compute_cost(usage, "claude-opus-4-6")
    cost2 = compute_cost_from_counts(
        model="claude-opus-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_write_tokens=200,
        cache_read_tokens=100,
    )
    assert abs(cost1 - cost2) < 0.0000001


def test_cost_from_counts_gpt54():
    cost = compute_cost_from_counts(
        model="gpt-5.4",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    # 1M * 2.5/1M + 100K * 15/1M = 2.5 + 1.5 = 4.0
    assert abs(cost - 4.0) < 0.001


# --- get_pricing edge cases ---


def test_prefix_match_with_suffix():
    """Model name with version suffix should match base model."""
    p = get_pricing("claude-opus-4-6[1m]")
    assert p.input_per_m == 5.0


def test_openai_models_have_pricing():
    assert "gpt-5.4" in MODEL_PRICING
    assert "gpt-5.4-nano" in MODEL_PRICING
    assert "gpt-5-codex" in MODEL_PRICING


# --- load_session_costs ---


def test_load_session_costs_valid(tmp_path: Path):
    costs_file = tmp_path / "session_costs.txt"
    costs_file.write_text("session-1:5.50\nsession-2:3.25\n")
    costs = load_session_costs(str(tmp_path))
    assert costs["session-1"] == 5.50
    assert costs["session-2"] == 3.25


def test_load_session_costs_missing_file(tmp_path: Path):
    costs = load_session_costs(str(tmp_path))
    assert costs == {}


def test_load_session_costs_malformed_lines(tmp_path: Path):
    costs_file = tmp_path / "session_costs.txt"
    costs_file.write_text("valid:1.0\ninvalid\nalso:bad:format\nok:2.5\n")
    costs = load_session_costs(str(tmp_path))
    assert costs["valid"] == 1.0
    assert costs["ok"] == 2.5
    # "invalid" has no colon, skipped
    # "also:bad:format" → rsplit(":", 1) → ("also:bad", "format") → ValueError, skipped
    assert len(costs) == 2
