"""Connect observed waste evidence to explicit source locations and advice."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .cache import CacheMissSummary, find_cache_misses
from .duplicates import (
    DEFAULT_DUPLICATE_WINDOW,
    PotentialDuplicateSummary,
    find_potential_duplicates,
)
from .models import Event, Source
from .retry import RetrySummary, find_retry_usage

WasteEvidence = CacheMissSummary | PotentialDuplicateSummary | RetrySummary

_MESSAGES = {
    "cache-miss": "Review provider cache configuration for this call.",
    "potential-duplicate": "Review deduplication, idempotency, or caching for this call.",
    "retry": "Review retry policy and maximum attempts for this call.",
}


class CallsiteMapError(ValueError):
    """Raised when a user-provided callsite mapping is invalid."""


@dataclass(frozen=True)
class Recommendation:
    """One improvement direction tied to its observed evidence."""

    rule: str
    evidence: WasteEvidence
    source: Source | None
    message: str


def load_callsite_map(path: Path) -> dict[str, Source]:
    """Load ``{callsite_id: {file, line}}`` without inventing locations."""
    mapping_path = Path(path)
    try:
        document = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CallsiteMapError(f"{mapping_path}: invalid callsite map -- {exc}") from exc

    if not isinstance(document, dict):
        raise CallsiteMapError(f"{mapping_path}: callsite map must be a JSON object")

    result: dict[str, Source] = {}
    for callsite_id, raw_source in document.items():
        if not isinstance(callsite_id, str) or not callsite_id:
            raise CallsiteMapError(f"{mapping_path}: callsite id must be a non-empty string")
        if not isinstance(raw_source, dict):
            raise CallsiteMapError(f"{mapping_path}: {callsite_id!r} must map to an object")

        file = raw_source.get("file")
        if not isinstance(file, str) or not file:
            raise CallsiteMapError(
                f"{mapping_path}: {callsite_id!r}.file must be a non-empty string"
            )

        line = raw_source.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise CallsiteMapError(f"{mapping_path}: {callsite_id!r}.line must be an integer >= 1")

        result[callsite_id] = Source(file=file.replace("\\", "/"), line=line)

    return result


def build_recommendations(
    events: Iterable[Event],
    *,
    callsites: Mapping[str, Source] | None = None,
    duplicate_window: timedelta = DEFAULT_DUPLICATE_WINDOW,
) -> list[Recommendation]:
    """Build evidence-backed advice for cache, duplicate, and retry waste."""
    observed = list(events)
    explicit_callsites = callsites or {}
    recommendations: list[Recommendation] = []

    for evidence in find_cache_misses(observed):
        recommendations.append(_recommend("cache-miss", evidence, observed, explicit_callsites))

    duplicate_analysis = find_potential_duplicates(observed, duplicate_window=duplicate_window)
    for evidence in duplicate_analysis.groups:
        recommendations.append(
            _recommend("potential-duplicate", evidence, observed, explicit_callsites)
        )

    for evidence in find_retry_usage(observed):
        recommendations.append(_recommend("retry", evidence, observed, explicit_callsites))

    return sorted(recommendations, key=_recommendation_key)


def _recommend(
    rule: str,
    evidence: WasteEvidence,
    events: list[Event],
    callsites: Mapping[str, Source],
) -> Recommendation:
    return Recommendation(
        rule=rule,
        evidence=evidence,
        source=_resolve_source(rule, evidence, events, callsites),
        message=_MESSAGES[rule],
    )


def _resolve_source(
    rule: str,
    evidence: WasteEvidence,
    events: list[Event],
    callsites: Mapping[str, Source],
) -> Source | None:
    matching_sources = {
        event.source
        for event in events
        if event.source is not None and _matches(rule, evidence, event)
    }
    if len(matching_sources) == 1:
        return next(iter(matching_sources))
    if matching_sources:
        # Conflicting locations in the observed log are stronger evidence of
        # ambiguity than a separate mapping is of either location being right.
        return None

    if evidence.callsite_id is not None:
        return callsites.get(evidence.callsite_id)
    return None


def _matches(rule: str, evidence: WasteEvidence, event: Event) -> bool:
    if (
        event.project_id != evidence.project_id
        or event.callsite_id != evidence.callsite_id
        or event.model != evidence.model
    ):
        return False

    provider = getattr(evidence, "provider", None)
    if provider is not None and event.provider != provider:
        return False

    if rule == "cache-miss":
        return event.cache_eligible is True and event.cache_status == "miss"
    if rule == "potential-duplicate":
        return event.request_fingerprint == evidence.request_fingerprint
    if rule == "retry":
        return event.retry_of is not None
    return False


def _recommendation_key(item: Recommendation) -> tuple[str, bool, str, str, str]:
    return (
        item.evidence.project_id,
        item.evidence.callsite_id is not None,
        item.evidence.callsite_id or "",
        item.evidence.model,
        item.rule,
    )
