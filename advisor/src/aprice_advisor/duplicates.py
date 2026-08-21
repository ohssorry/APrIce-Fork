"""Find potential duplicate requests from explicit opaque fingerprints."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aprice import pricing

from .models import Event

DEFAULT_DUPLICATE_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class PotentialDuplicateSummary:
    """Repeated observed usage after the first request in a time window."""

    project_id: str
    callsite_id: str | None
    provider: str
    model: str
    request_fingerprint: str
    duplicate_request_count: int
    input_tokens: int
    output_tokens: int
    potential_standard_cost_usd: float | None


@dataclass(frozen=True)
class DuplicateAnalysis:
    duplicate_window_seconds: float
    groups: list[PotentialDuplicateSummary]


def find_potential_duplicates(
    events: Iterable[Event],
    duplicate_window: timedelta = DEFAULT_DUPLICATE_WINDOW,
) -> DuplicateAnalysis:
    """Find repeated fingerprints without reading or deriving raw prompts.

    Each project/callsite/provider/model/fingerprint group is sorted by its
    observed timestamp. The first request anchors a window; later requests in
    that window are potential duplicates, while the first request outside it
    starts a new window. This avoids chaining nearby requests into an
    unbounded interval.
    """
    window_seconds = duplicate_window.total_seconds()
    if window_seconds <= 0:
        raise ValueError("duplicate_window must be greater than zero")

    buckets: dict[tuple[str, str | None, str, str, str], list[Event]] = {}
    for event in events:
        if not event.request_fingerprint:
            continue
        key = (
            event.project_id,
            event.callsite_id,
            event.provider,
            event.model,
            event.request_fingerprint,
        )
        buckets.setdefault(key, []).append(event)

    summaries: list[PotentialDuplicateSummary] = []
    for key, group in buckets.items():
        duplicate_events = _duplicates_in_windows(group, duplicate_window)
        if not duplicate_events:
            continue

        project_id, callsite_id, provider, model, fingerprint = key
        input_tokens = sum(event.input_tokens for event in duplicate_events)
        output_tokens = sum(event.output_tokens for event in duplicate_events)
        summaries.append(
            PotentialDuplicateSummary(
                project_id=project_id,
                callsite_id=callsite_id,
                provider=provider,
                model=model,
                request_fingerprint=fingerprint,
                duplicate_request_count=len(duplicate_events),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                potential_standard_cost_usd=_standard_cost(
                    provider, model, input_tokens, output_tokens
                ),
            )
        )

    summaries.sort(
        key=lambda item: (
            item.project_id,
            item.callsite_id is not None,
            item.callsite_id or "",
            item.provider,
            item.model,
            item.request_fingerprint,
        )
    )
    return DuplicateAnalysis(duplicate_window_seconds=window_seconds, groups=summaries)


def _duplicates_in_windows(events: list[Event], window: timedelta) -> list[Event]:
    ordered = sorted(events, key=lambda event: _timestamp(event.timestamp))
    if not ordered:
        return []

    duplicates: list[Event] = []
    window_start = _timestamp(ordered[0].timestamp)
    for event in ordered[1:]:
        observed_at = _timestamp(event.timestamp)
        if observed_at - window_start <= window:
            duplicates.append(event)
        else:
            window_start = observed_at
    return duplicates


def _timestamp(value: str) -> datetime:
    # load_jsonl() has already validated RFC 3339 and timezone presence.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
