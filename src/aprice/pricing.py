"""Community-maintained price database and the cost estimator.

Prices live in ``prices/*.yaml`` -- one file per provider -- so that keeping
them current is a documentation-sized pull request, not a code change. Every
entry carries ``verified_on`` so a stale price is visible rather than silently
wrong.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .models import ApiCall, CostEstimate, Price

PRICES_DIR = Path(__file__).parent / "prices"

# Fraction of `max_tokens` we assume a typical response actually uses. The
# upper bound of every estimate is the full `max_tokens`, which the API does
# enforce; the lower bound is this fraction of it.
TYPICAL_OUTPUT_RATIO = 0.3

# Input tokens cannot be derived from source when the prompt is assembled at
# runtime, so the caller supplies an assumption. This is the default used when
# no `aprice.yaml` is present.
DEFAULT_INPUT_TOKENS = 1_000


@lru_cache(maxsize=1)
def load_prices() -> dict[tuple[str, str], Price]:
    """Load every provider YAML into a ``(provider, model) -> Price`` table."""
    table: dict[tuple[str, str], Price] = {}
    for path in sorted(PRICES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        provider = doc.get("provider") or path.stem
        for entry in doc.get("models", []):
            price = Price(
                provider=provider,
                model=entry["id"],
                input_per_mtok=float(entry["input_per_mtok"]),
                output_per_mtok=float(entry["output_per_mtok"]),
                unit=entry.get("unit", "token"),
                note=entry.get("note", ""),
            )
            table[(provider, price.model)] = price
    return table


def lookup(provider: str, model: str | None) -> Price | None:
    if model is None:
        return None
    return load_prices().get((provider, model))


def estimate(call: ApiCall, input_tokens: int = DEFAULT_INPUT_TOKENS) -> CostEstimate | None:
    """Estimate the USD cost of a single call, as a range.

    Returns None when the model is unknown -- an unpriced call is reported
    separately rather than guessed at.
    """
    price = lookup(call.provider, call.model)
    if price is None:
        return None

    max_out = call.max_tokens or 1024
    input_cost = input_tokens / 1_000_000 * price.input_per_mtok

    high = input_cost + (max_out / 1_000_000 * price.output_per_mtok)
    low = input_cost + (max_out * TYPICAL_OUTPUT_RATIO / 1_000_000 * price.output_per_mtok)
    return CostEstimate(call=call, price=price, low_usd=low, high_usd=high)
