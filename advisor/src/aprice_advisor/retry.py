"""Aggregate observed retry usage without inferring retry relationships."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from aprice import pricing

from .models import Event


@dataclass(frozen=True)
class RetrySummary:
    """Observed retry usage for one project, callsite, provider, and model.

    ``standard_cost_usd`` applies the verified price table to actual logged
    tokens. It is not an invoice amount and remains None when no applicable
    token price exists.
    """

    project_id: str
    callsite_id: str | None
    provider: str
    model: str
    request_count: int
    input_tokens: int
    output_tokens: int
    standard_cost_usd: float | None


def find_retry_usage(events: Iterable[Event]) -> list[RetrySummary]:
    """Return usage for events whose ``retry_of`` relationship is explicit.

    Events are expected to come from ``load_jsonl()``, which removes missing
    and cyclic retry references. Names, timestamps, and failure status are not
    used as evidence because none of them proves that a request was a retry.
    """
    buckets: dict[tuple[str, str | None, str, str], list[Event]] = {}

    for event in events:
        if event.retry_of is None:
            continue
        key = (event.project_id, event.callsite_id, event.provider, event.model)
        buckets.setdefault(key, []).append(event)

    summaries = []
    for (project_id, callsite_id, provider, model), group in buckets.items():
        input_tokens = sum(event.input_tokens for event in group)
        output_tokens = sum(event.output_tokens for event in group)
        summaries.append(
            RetrySummary(
                project_id=project_id,
                callsite_id=callsite_id,
                provider=provider,
                model=model,
                request_count=len(group),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                standard_cost_usd=_standard_cost(provider, model, input_tokens, output_tokens),
            )
        )

    return sorted(
        summaries,
        key=lambda item: (
            item.project_id,
            item.callsite_id is not None,
            item.callsite_id or "",
            item.provider,
            item.model,
        ),
    )


def _standard_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float | None:
    price = pricing.lookup(provider, model)
    if price is None or price.unit != "token":
        return None
    return (
        input_tokens / 1_000_000 * price.input_per_mtok
        + output_tokens / 1_000_000 * price.output_per_mtok
    )
