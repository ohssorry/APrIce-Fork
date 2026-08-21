"""Find cache misses only when the usage log provides explicit evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import Event


@dataclass(frozen=True)
class CacheMissSummary:
    """Observed cache misses for one project, callsite, and model."""

    project_id: str
    callsite_id: str | None
    model: str
    request_count: int
    input_tokens: int


def find_cache_misses(events: Iterable[Event]) -> list[CacheMissSummary]:
    """Aggregate events explicitly marked as cache-eligible misses.

    A model name or repeated input is not evidence that caching was possible,
    so both cache fields must opt an event into this result. The price database
    has no verified cached-input rate yet; consequently this function reports
    observed requests and tokens without inventing a dollar saving.
    """
    totals: dict[tuple[str, str | None, str], tuple[int, int]] = {}

    for event in events:
        if event.cache_eligible is not True or event.cache_status != "miss":
            continue

        key = (event.project_id, event.callsite_id, event.model)
        request_count, input_tokens = totals.get(key, (0, 0))
        totals[key] = (request_count + 1, input_tokens + event.input_tokens)

    summaries = [
        CacheMissSummary(
            project_id=project_id,
            callsite_id=callsite_id,
            model=model,
            request_count=request_count,
            input_tokens=input_tokens,
        )
        for (project_id, callsite_id, model), (request_count, input_tokens) in totals.items()
    ]
    return sorted(
        summaries,
        key=lambda item: (
            item.project_id,
            item.callsite_id is not None,
            item.callsite_id or "",
            item.model,
        ),
    )
