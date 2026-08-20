"""Loads and validates a JSONL usage log against schema v1 (see APrIce #34).

A malformed row is skipped and recorded as a LoadError rather than failing
the whole file -- the same choice aprice.detector makes for a syntactically
broken source file: one bad line shouldn't discard everything else that
parsed fine. See the C review comment on #34 for the reasoning, including
why a duplicate request_id or a broken retry_of reference drops only the
offending row rather than the whole file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import Event, LoadError, LoadResult, Source

SCHEMA_VERSION = 1

_STATUS_VALUES = {"success", "error"}
_CACHE_STATUS_VALUES = {"hit", "miss"}
_REQUIRED_STRING_FIELDS = (
    "timestamp",
    "project_id",
    "provider",
    "model",
    "operation",
    "request_id",
)


def load_jsonl(path: Path) -> LoadResult:
    accepted: list[tuple[int, Event]] = []
    errors: list[LoadError] = []
    seen_ids: dict[str, int] = {}

    with Path(path).open(encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            parsed = _parse_row(raw_line, line_no)
            if isinstance(parsed, LoadError):
                errors.append(parsed)
                continue

            if parsed.request_id in seen_ids:
                errors.append(LoadError(line_no, f"duplicate request_id: {parsed.request_id!r}"))
                continue
            seen_ids[parsed.request_id] = line_no
            accepted.append((line_no, parsed))

    kept = _drop_broken_retry_references(accepted, errors)
    kept = _drop_retry_cycles(kept, errors)

    errors.sort(key=lambda e: e.line)
    return LoadResult(events=[event for _, event in kept], errors=errors)


def _drop_broken_retry_references(
    accepted: list[tuple[int, Event]], errors: list[LoadError]
) -> list[tuple[int, Event]]:
    valid_ids = {event.request_id for _, event in accepted}
    kept: list[tuple[int, Event]] = []
    for line_no, event in accepted:
        if event.retry_of is not None and event.retry_of not in valid_ids:
            errors.append(
                LoadError(line_no, f"retry_of references unknown request_id: {event.retry_of!r}")
            )
            continue
        kept.append((line_no, event))
    return kept


def _drop_retry_cycles(
    kept: list[tuple[int, Event]], errors: list[LoadError]
) -> list[tuple[int, Event]]:
    graph = {event.request_id: event.retry_of for _, event in kept if event.retry_of is not None}
    cyclic_ids = _find_cyclic(graph)
    if not cyclic_ids:
        return kept

    final: list[tuple[int, Event]] = []
    for line_no, event in kept:
        if event.request_id in cyclic_ids:
            errors.append(LoadError(line_no, "retry_of forms a cycle"))
        else:
            final.append((line_no, event))
    return final


def _find_cyclic(graph: dict[str, str]) -> set[str]:
    """Return every request_id that sits on a retry_of cycle.

    Walks each chain once, tracking the current path so a node re-appearing
    on it identifies the cycle; walking stops on hitting anything already
    resolved by an earlier chain, so no edge is re-walked.
    """
    cyclic: set[str] = set()
    resolved: set[str] = set()

    for start in graph:
        if start in resolved:
            continue
        path: list[str] = []
        index_in_path: dict[str, int] = {}
        node: str | None = start
        while node is not None and node in graph and node not in resolved:
            if node in index_in_path:
                cyclic.update(path[index_in_path[node] :])
                break
            index_in_path[node] = len(path)
            path.append(node)
            node = graph.get(node)
        resolved.update(path)

    return cyclic


def _parse_row(raw: str, line_no: int) -> Event | LoadError:
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LoadError(line_no, f"invalid JSON: {exc}")

    if not isinstance(doc, dict):
        return LoadError(line_no, "row is not a JSON object")

    if doc.get("schema_version") != SCHEMA_VERSION:
        return LoadError(line_no, f"unsupported schema_version: {doc.get('schema_version')!r}")

    for field in _REQUIRED_STRING_FIELDS:
        value = doc.get(field)
        if not isinstance(value, str) or not value:
            return LoadError(line_no, f"'{field}' must be a non-empty string")

    if not _is_rfc3339_with_offset(doc["timestamp"]):
        return LoadError(line_no, "'timestamp' must be RFC 3339 with a timezone offset")

    for field in ("input_tokens", "output_tokens"):
        value = doc.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return LoadError(line_no, f"'{field}' must be a non-negative integer")

    status = doc.get("status")
    if status not in _STATUS_VALUES:
        return LoadError(line_no, f"'status' must be one of {sorted(_STATUS_VALUES)}")

    for field in ("trace_id", "retry_of", "request_fingerprint", "callsite_id"):
        value = doc.get(field)
        if value is not None and not isinstance(value, str):
            return LoadError(line_no, f"'{field}' must be a string")

    cache_eligible = doc.get("cache_eligible")
    if cache_eligible is not None and not isinstance(cache_eligible, bool):
        return LoadError(line_no, "'cache_eligible' must be a boolean")

    cache_status = doc.get("cache_status")
    if cache_status is not None and cache_status not in _CACHE_STATUS_VALUES:
        return LoadError(
            line_no, f"'cache_status' must be one of {sorted(_CACHE_STATUS_VALUES)} or absent"
        )

    source = _parse_source(doc.get("source"), line_no)
    if isinstance(source, LoadError):
        return source

    return Event(
        schema_version=SCHEMA_VERSION,
        timestamp=doc["timestamp"],
        project_id=doc["project_id"],
        provider=doc["provider"],
        model=doc["model"],
        operation=doc["operation"],
        request_id=doc["request_id"],
        input_tokens=doc["input_tokens"],
        output_tokens=doc["output_tokens"],
        status=status,
        trace_id=doc.get("trace_id"),
        retry_of=doc.get("retry_of"),
        request_fingerprint=doc.get("request_fingerprint"),
        cache_eligible=cache_eligible,
        cache_status=cache_status,
        callsite_id=doc.get("callsite_id"),
        source=source,
    )


def _parse_source(raw_source: object, line_no: int) -> Source | None | LoadError:
    if raw_source is None:
        return None
    if not isinstance(raw_source, dict):
        return LoadError(line_no, "'source' must be an object")

    file = raw_source.get("file")
    if not isinstance(file, str) or not file:
        return LoadError(line_no, "'source.file' must be a non-empty string")

    line = raw_source.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return LoadError(line_no, "'source.line' must be an integer >= 1")

    return Source(file=file.replace("\\", "/"), line=line)


def _is_rfc3339_with_offset(value: str) -> bool:
    # datetime.fromisoformat() only grew broad RFC 3339 support in Python
    # 3.11; this package targets 3.10+, so the 'Z' shorthand is normalized
    # by hand first. Formats fromisoformat still can't parse on 3.10 (e.g.
    # some fractional-second widths) are rejected here even if they are
    # technically valid RFC 3339 -- narrower than the spec, not wider.
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None
