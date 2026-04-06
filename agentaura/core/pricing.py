"""Model pricing and cost computation.

Prices are per million tokens. Cost computed from token counts in assistant message usage.
These are approximate — Claude's docs note cost metrics are approximate.
Validate against session_costs.txt where available for coarse sanity checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentaura.core.events import TokenUsage


@dataclass(frozen=True)
class ModelPricing:
    input_per_m: float
    output_per_m: float
    cache_write_per_m: float
    cache_read_per_m: float


# Pricing as of early 2026. Update as needed.
MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-6": ModelPricing(
        input_per_m=15.0,
        output_per_m=75.0,
        cache_write_per_m=18.75,
        cache_read_per_m=1.50,
    ),
    "claude-sonnet-4-5-20250929": ModelPricing(
        input_per_m=3.0,
        output_per_m=15.0,
        cache_write_per_m=3.75,
        cache_read_per_m=0.30,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input_per_m=0.80,
        output_per_m=4.0,
        cache_write_per_m=1.0,
        cache_read_per_m=0.08,
    ),
}

# Fallback for unknown models (use Sonnet pricing as reasonable middle ground)
_FALLBACK_PRICING = ModelPricing(
    input_per_m=3.0, output_per_m=15.0, cache_write_per_m=3.75, cache_read_per_m=0.30
)


def get_pricing(model: str) -> ModelPricing:
    """Get pricing for a model, falling back to Sonnet pricing for unknown models."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Try prefix match (e.g., "claude-opus-4-6" matches "claude-opus-4-6[1m]")
    for key, pricing in MODEL_PRICING.items():
        if model.startswith(key) or key.startswith(model):
            return pricing
    return _FALLBACK_PRICING


def compute_cost(usage: TokenUsage, model: str) -> float:
    """Compute cost in USD from token usage and model.

    Cost = (input * input_rate + output * output_rate
            + cache_creation * write_rate + cache_read * read_rate) / 1_000_000
    """
    pricing = get_pricing(model)
    cost = (
        usage.input_tokens * pricing.input_per_m
        + usage.output_tokens * pricing.output_per_m
        + usage.cache_creation_input_tokens * pricing.cache_write_per_m
        + usage.cache_read_input_tokens * pricing.cache_read_per_m
    ) / 1_000_000
    return cost


def load_session_costs(claude_dir_path: str | None = None) -> dict[str, float]:
    """Load session_costs.txt for validation. Returns {session_id: cost_usd}."""
    from pathlib import Path

    claude_dir = Path(claude_dir_path) if claude_dir_path else Path.home() / ".claude"
    costs_file = claude_dir / "session_costs.txt"
    if not costs_file.exists():
        return {}

    costs: dict[str, float] = {}
    for line in costs_file.read_text().splitlines():
        line = line.strip()
        if ":" in line:
            sid, cost_str = line.rsplit(":", 1)
            try:
                costs[sid] = float(cost_str)
            except ValueError:
                pass
    return costs
