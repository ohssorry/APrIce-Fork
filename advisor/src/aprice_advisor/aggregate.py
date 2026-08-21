"""Aggregates validated usage-log events into per-project cost summaries.

Uses ``aprice.pricing.lookup()`` only -- never ``aprice.pricing.estimate()``,
which assumes an input-token count and a 30% output-token floor for static
analysis where the real token counts aren't knowable. Every ``Event`` here
already carries the real ``input_tokens``/``output_tokens`` from an actual
request, so no assumption is needed. See issue #34 for the reuse contract.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from aprice import pricing

from .models import Event


@dataclass(frozen=True)
class UsageGroupKey:
    project_id: str
    provider: str
    model: str
    operation: str


@dataclass(frozen=True)
class UsageGroup:
    """Summed usage for one (project, provider, model, operation) group.

    ``standard_cost_usd`` is what this usage would cost at the price DB's
    verified per-token rate -- not what was actually billed. Real invoices
    may include caching, batch, or committed-use discounts the price DB
    doesn't model yet (see issue #34), so this is a standard-rate estimate,
    never mixed with or presented as an actual charge.

    None when the model has no verified price, or its price entry isn't
    token-based -- the usage is still counted, the cost just isn't guessed.
    """

    key: UsageGroupKey
    request_count: int
    input_tokens: int
    output_tokens: int
    standard_cost_usd: float | None


@dataclass(frozen=True)
class AggregateResult:
    groups: list[UsageGroup]
    total_request_count: int
    total_input_tokens: int
    total_output_tokens: int
    # Sum over priced groups only. Unpriced usage contributes 0 here, not a
    # guessed cost -- see unpriced_request_count for what was excluded.
    total_standard_cost_usd: float
    unpriced_request_count: int


def aggregate_usage(events: list[Event]) -> AggregateResult:
    """Group events by (project, provider, model, operation) and price them.

    Only projects/groups with at least one observed event appear in the
    result -- no entry is manufactured for usage that was never logged.
    """
    buckets: dict[UsageGroupKey, list[Event]] = defaultdict(list)
    for event in events:
        key = UsageGroupKey(
            project_id=event.project_id,
            provider=event.provider,
            model=event.model,
            operation=event.operation,
        )
        buckets[key].append(event)

    groups: list[UsageGroup] = []
    total_cost = 0.0
    total_requests = 0
    total_input = 0
    total_output = 0
    unpriced_requests = 0

    for key in sorted(buckets, key=lambda k: (k.project_id, k.provider, k.model, k.operation)):
        group_events = buckets[key]
        input_tokens = sum(e.input_tokens for e in group_events)
        output_tokens = sum(e.output_tokens for e in group_events)
        request_count = len(group_events)

        cost = _standard_cost(key.provider, key.model, input_tokens, output_tokens)
        if cost is None:
            unpriced_requests += request_count
        else:
            total_cost += cost

        groups.append(
            UsageGroup(
                key=key,
                request_count=request_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                standard_cost_usd=cost,
            )
        )

        total_requests += request_count
        total_input += input_tokens
        total_output += output_tokens

    return AggregateResult(
        groups=groups,
        total_request_count=total_requests,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_standard_cost_usd=total_cost,
        unpriced_request_count=unpriced_requests,
    )


def _standard_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float | None:
    price = pricing.lookup(provider, model)
    # unit != "token" means the price table's per-mtok rate doesn't apply to
    # this operation (e.g. a future per-image or per-request entry) -- the
    # #34 contract says to leave that unpriced rather than misapply a rate.
    if price is None or price.unit != "token":
        return None
    return (
        input_tokens / 1_000_000 * price.input_per_mtok
        + output_tokens / 1_000_000 * price.output_per_mtok
    )
