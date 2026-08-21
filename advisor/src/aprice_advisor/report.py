"""Renders an analysis run as machine-readable JSON or human Markdown.

Both renderers are pure functions -- they return a string, they never print.
Mirrors the same rule the root ``aprice.report`` module follows, for the
same reason: it is what makes them easy to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .aggregate import AggregateResult
from .cache import CacheMissSummary
from .duplicates import DuplicateAnalysis, PotentialDuplicateSummary
from .loader import LoadError
from .models import Source
from .recommendations import Recommendation
from .retry import RetrySummary

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AnalysisResult:
    input_file: str
    duplicate_window_seconds: float
    load_errors: list[LoadError]
    aggregate: AggregateResult
    retry: list[RetrySummary]
    duplicates: DuplicateAnalysis
    cache_misses: list[CacheMissSummary]
    recommendations: list[Recommendation]


def _source_dict(source: Source | None) -> dict | None:
    if source is None:
        return None
    return {"file": source.file, "line": source.line}


def _retry_dict(item: RetrySummary) -> dict:
    return {
        "project_id": item.project_id,
        "callsite_id": item.callsite_id,
        "provider": item.provider,
        "model": item.model,
        "request_count": item.request_count,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "standard_cost_usd": item.standard_cost_usd,
    }


def _duplicate_dict(item: PotentialDuplicateSummary) -> dict:
    return {
        "project_id": item.project_id,
        "callsite_id": item.callsite_id,
        "provider": item.provider,
        "model": item.model,
        "request_fingerprint": item.request_fingerprint,
        "duplicate_request_count": item.duplicate_request_count,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "potential_standard_cost_usd": item.potential_standard_cost_usd,
    }


def _cache_dict(item: CacheMissSummary) -> dict:
    return {
        "project_id": item.project_id,
        "callsite_id": item.callsite_id,
        "model": item.model,
        "request_count": item.request_count,
        "input_tokens": item.input_tokens,
    }


_EVIDENCE_DICT = {
    "retry": _retry_dict,
    "potential-duplicate": _duplicate_dict,
    "cache-miss": _cache_dict,
}


def _recommendation_dict(item: Recommendation) -> dict:
    return {
        "rule": item.rule,
        "message": item.message,
        "source": _source_dict(item.source),
        "evidence": _EVIDENCE_DICT[item.rule](item.evidence),
    }


def render_json(result: AnalysisResult) -> str:
    """Render an AnalysisResult as JSON.

    Standard-price cost is always what the verified price table says actual
    logged tokens would cost -- never a monthly forecast, never call volume.
    See docs on the root ``aprice`` package for why that boundary exists;
    Advisor inherits it rather than relaxing it just because real usage
    numbers are available here.
    """
    aggregate = result.aggregate
    payload = {
        "schema_version": SCHEMA_VERSION,
        "input_file": result.input_file,
        "duplicate_window_seconds": result.duplicate_window_seconds,
        "load_errors": [{"line": e.line, "reason": e.reason} for e in result.load_errors],
        "aggregate": {
            "groups": [
                {
                    "project_id": g.key.project_id,
                    "provider": g.key.provider,
                    "model": g.key.model,
                    "operation": g.key.operation,
                    "request_count": g.request_count,
                    "input_tokens": g.input_tokens,
                    "output_tokens": g.output_tokens,
                    "standard_cost_usd": g.standard_cost_usd,
                }
                for g in aggregate.groups
            ],
            "total_request_count": aggregate.total_request_count,
            "total_input_tokens": aggregate.total_input_tokens,
            "total_output_tokens": aggregate.total_output_tokens,
            "total_standard_cost_usd": aggregate.total_standard_cost_usd,
            "unpriced_request_count": aggregate.unpriced_request_count,
        },
        "retry": [_retry_dict(item) for item in result.retry],
        "duplicates": [_duplicate_dict(item) for item in result.duplicates.groups],
        "cache_misses": [_cache_dict(item) for item in result.cache_misses],
        "recommendations": [_recommendation_dict(item) for item in result.recommendations],
        "unpriced_models": [
            {"provider": g.key.provider, "model": g.key.model}
            for g in aggregate.groups
            if g.standard_cost_usd is None
        ],
    }
    return json.dumps(payload, indent=2)


def _usd(amount: float) -> str:
    if amount < 1:
        return f"${amount:.5f}"
    return f"${amount:,.2f}"


def render_markdown(result: AnalysisResult) -> str:
    """Render a human-readable summary of one analysis run."""
    lines = [f"## APrIce Advisor report: `{result.input_file}`", ""]
    lines.append(
        "<sub>Standard-price cost applies the verified price table to actual "
        "logged tokens -- it is not an invoice amount. Real billing may include "
        "caching, batch, or committed-use discounts this table doesn't model. "
        f"Duplicate detection uses a {result.duplicate_window_seconds:.0f}-second "
        "observation window.</sub>"
    )
    lines.append("")

    if result.load_errors:
        lines.append(f"{len(result.load_errors)} log row(s) failed validation and were skipped.")
        lines.append("")

    aggregate = result.aggregate
    lines.append(
        f"**Total usage:** {aggregate.total_request_count} request(s), "
        f"standard cost {_usd(aggregate.total_standard_cost_usd)} "
        f"({aggregate.unpriced_request_count} unpriced request(s) excluded)."
    )
    lines.append("")

    if result.recommendations:
        lines.append(f"### Recommendations ({len(result.recommendations)})")
        for rec in result.recommendations:
            location = f"`{rec.source.file}:{rec.source.line}`" if rec.source else "(no location)"
            lines.append(f"- **[{rec.rule}]** {location} -- {rec.message}")
        lines.append("")
    else:
        lines.append("No waste patterns detected in this log.")
        lines.append("")

    unpriced_models = sorted(
        {(g.key.provider, g.key.model) for g in aggregate.groups if g.standard_cost_usd is None}
    )
    if unpriced_models:
        lines.append("### Unpriced models")
        for provider, model in unpriced_models:
            lines.append(f"- `{provider}/{model}` -- usage observed, no verified price")
        lines.append("")

    return "\n".join(lines)
