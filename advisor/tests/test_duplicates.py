from datetime import timedelta

import pytest

from aprice_advisor.duplicates import find_potential_duplicates
from aprice_advisor.models import Event


def event(request_id, minute, **overrides):
    values = {
        "schema_version": 1,
        "timestamp": f"2026-08-21T12:{minute:02d}:00Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": request_id,
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "request_fingerprint": "opaque-fingerprint",
    }
    values.update(overrides)
    return Event(**values)


def test_default_window_counts_only_requests_after_the_first():
    result = find_potential_duplicates(
        [
            event("req-1", 0, input_tokens=1_000_000, output_tokens=1_000_000),
            event("req-2", 2, input_tokens=200, output_tokens=80),
            event("req-3", 5, input_tokens=300, output_tokens=120),
        ]
    )

    assert result.duplicate_window_seconds == 300.0
    [summary] = result.groups
    assert summary.duplicate_request_count == 2
    assert summary.input_tokens == 500
    assert summary.output_tokens == 200


def test_a_request_outside_the_window_starts_a_new_window():
    result = find_potential_duplicates(
        [
            event("req-1", 0),
            event("req-2", 4),
            event("req-3", 8),
        ]
    )

    [summary] = result.groups
    assert summary.duplicate_request_count == 1
    assert summary.input_tokens == 100


def test_custom_window_is_applied_and_reported():
    result = find_potential_duplicates(
        [event("req-1", 0), event("req-2", 2)],
        duplicate_window=timedelta(minutes=1),
    )

    assert result.duplicate_window_seconds == 60.0
    assert result.groups == []


def test_missing_or_empty_fingerprint_is_not_guessed():
    result = find_potential_duplicates(
        [
            event("req-1", 0, request_fingerprint=None),
            event("req-2", 1, request_fingerprint=None),
            event("req-3", 2, request_fingerprint=""),
            event("req-4", 3, request_fingerprint=""),
        ]
    )

    assert result.groups == []


def test_same_fingerprint_does_not_mix_projects_callsites_or_models():
    result = find_potential_duplicates(
        [
            event("a-1", 0, project_id="proj-a", callsite_id="site-a"),
            event("b-1", 1, project_id="proj-b", callsite_id="site-a"),
            event("a-2", 2, project_id="proj-a", callsite_id="site-b"),
            event("a-3", 3, project_id="proj-a", callsite_id="site-a", model="claude-opus-5"),
        ]
    )

    assert result.groups == []


def test_potential_standard_cost_uses_only_duplicate_tokens():
    result = find_potential_duplicates(
        [
            event("req-1", 0, input_tokens=1_000_000, output_tokens=1_000_000),
            event("req-2", 1, input_tokens=1_000_000, output_tokens=1_000_000),
        ]
    )

    [summary] = result.groups
    assert summary.potential_standard_cost_usd == pytest.approx(3.00 + 15.00)


def test_unpriced_duplicates_are_retained_without_a_guessed_cost():
    result = find_potential_duplicates(
        [
            event("req-1", 0, model="unknown-model"),
            event("req-2", 1, model="unknown-model"),
        ]
    )

    [summary] = result.groups
    assert summary.duplicate_request_count == 1
    assert summary.potential_standard_cost_usd is None


@pytest.mark.parametrize("seconds", [0, -1])
def test_non_positive_window_is_rejected(seconds):
    with pytest.raises(ValueError, match="greater than zero"):
        find_potential_duplicates([], duplicate_window=timedelta(seconds=seconds))
