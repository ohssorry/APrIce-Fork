"""Data contracts for one parsed row of a JSONL usage log.

Shape is fixed by the schema agreed in APrIce issue #34. Like the root
`aprice` package's own models.py, this file is a shared contract across
Advisor's A/B/C-owned modules (cache/duplicates/retries, aggregate, and
loader/CLI/report respectively) -- do not add or rename a field without
that same three-way agreement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """Where in the caller's source this request was made, if reported."""

    file: str
    line: int


@dataclass(frozen=True)
class Event:
    """One validated row of the JSONL log -- real, observed usage.

    Unlike aprice.models.ApiCall, every number here comes from an actual
    request rather than a static-analysis assumption. See #34: raw prompt
    and response text are deliberately not part of this schema, and a field
    this loader doesn't recognize is never used as analysis evidence.
    """

    schema_version: int
    timestamp: str
    project_id: str
    provider: str
    model: str
    operation: str
    request_id: str
    input_tokens: int
    output_tokens: int
    status: str  # "success" | "error"
    trace_id: str | None = None
    retry_of: str | None = None
    request_fingerprint: str | None = None
    cache_eligible: bool | None = None
    cache_status: str | None = None  # "hit" | "miss" | None (unknown -- not guessed)
    callsite_id: str | None = None
    source: Source | None = None


@dataclass(frozen=True)
class LoadError:
    """One row that failed validation. The row is skipped, not fatal to
    the rest of the file -- see the loader module docstring."""

    line: int
    reason: str


@dataclass(frozen=True)
class LoadResult:
    events: list[Event]
    errors: list[LoadError]
