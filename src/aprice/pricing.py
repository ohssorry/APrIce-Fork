"""Community-maintained price database and the cost estimator.

Prices live in ``prices/*.yaml`` -- one file per provider -- so that keeping
them current is a documentation-sized pull request, not a code change. Every
entry carries ``verified_on`` so a stale price is visible rather than silently
wrong.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

import yaml

from .models import ApiCall, CostEstimate, Price

PRICES_DIR = Path(__file__).parent / "prices"

# Fields every entry in prices/*.yaml must define. verified_on is required so
# a stale or missing price is visible rather than silently treated as free.
REQUIRED_FIELDS = ("id", "input_per_mtok", "output_per_mtok", "verified_on")


class PriceFileError(ValueError):
    """Raised when a prices/*.yaml file contains malformed data.

    Carries the file name and offending entry so a bad PR is easy to
    pinpoint instead of failing with a bare KeyError deep in yaml parsing.
    """


# Fraction of `max_tokens` we assume a typical response actually uses. The
# upper bound of every estimate is the full `max_tokens`, which the API does
# enforce; the lower bound is this fraction of it.
TYPICAL_OUTPUT_RATIO = 0.3

# Input tokens cannot be derived from source when the prompt is assembled at
# runtime, so the caller supplies an assumption. This is the default used when
# no `aprice.yaml` is present.
DEFAULT_INPUT_TOKENS = 1_000


def _validate_entry(entry: dict, provider: str, path: Path, seen_ids: set[str]) -> Price:
    """Validate one model entry from a prices/*.yaml file and build its Price.

    Raises PriceFileError naming ``path`` and the entry so a bad price table
    fails CI with a message a reviewer can act on, instead of an obscure
    KeyError/ValueError from deep inside yaml parsing.
    """
    label = entry.get("id", "<no id>") if isinstance(entry, dict) else "<malformed entry>"
    if not isinstance(entry, dict):
        raise PriceFileError(f"{path.name}: entry {entry!r} is not a mapping")

    missing = [field for field in REQUIRED_FIELDS if entry.get(field) is None]
    if missing:
        raise PriceFileError(
            f"{path.name}: '{label}' is missing required field(s): {', '.join(missing)}"
        )

    model_id = entry["id"]
    if model_id in seen_ids:
        raise PriceFileError(f"{path.name}: duplicate model id '{model_id}'")
    seen_ids.add(model_id)

    try:
        input_price = float(entry["input_per_mtok"])
        output_price = float(entry["output_per_mtok"])
    except (TypeError, ValueError) as exc:
        raise PriceFileError(f"{path.name}: '{model_id}' has a non-numeric price") from exc

    if input_price < 0 or output_price < 0:
        raise PriceFileError(f"{path.name}: '{model_id}' has a negative price")
    if input_price == 0.0 or output_price == 0.0:
        raise PriceFileError(
            f"{path.name}: '{model_id}' has a 0.00 placeholder price "
            "-- confirm the real price or drop the entry instead"
        )

    verified_on = entry["verified_on"]
    if isinstance(verified_on, dt.datetime):
        verified_date = verified_on.date()
    elif isinstance(verified_on, dt.date):
        verified_date = verified_on
    elif isinstance(verified_on, str):
        try:
            verified_date = dt.date.fromisoformat(verified_on)
        except ValueError as exc:
            raise PriceFileError(
                f"{path.name}: '{model_id}' has verified_on '{verified_on}' -- expected YYYY-MM-DD"
            ) from exc
    else:
        raise PriceFileError(
            f"{path.name}: '{model_id}' has verified_on '{verified_on}' -- expected YYYY-MM-DD"
        )

    # Compare against UTC "today". No forward grace: issue #12's acceptance
    # criteria is catching future dates, and a date one day out is still a
    # future date from CI's perspective, not a legitimate "just verified".
    utc_today = dt.datetime.now(dt.timezone.utc).date()
    if verified_date > utc_today:
        raise PriceFileError(
            f"{path.name}: '{model_id}' has a future verified_on date '{verified_date}'"
        )

    return Price(
        provider=provider,
        model=model_id,
        input_per_mtok=input_price,
        output_per_mtok=output_price,
        unit=entry.get("unit", "token"),
        note=entry.get("note", ""),
    )


@lru_cache(maxsize=1)
def load_prices() -> dict[tuple[str, str], Price]:
    """Load every provider YAML into a ``(provider, model) -> Price`` table."""
    table: dict[tuple[str, str], Price] = {}
    for path in sorted(PRICES_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PriceFileError(f"{path.name}: invalid YAML -- {exc}") from exc
        doc = doc or {}
        if not isinstance(doc, dict):
            raise PriceFileError(
                f"{path.name}: top-level document must be a mapping, got {type(doc).__name__}"
            )
        provider = doc.get("provider") or path.stem
        seen_ids: set[str] = set()
        models = doc.get("models", [])
        if not isinstance(models, list):
            raise PriceFileError(
                f"{path.name}: 'models' must be a list, got {type(models).__name__}"
            )
        for entry in models:
            price = _validate_entry(entry, provider, path, seen_ids)
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
