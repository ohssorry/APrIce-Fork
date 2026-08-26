import json

import pytest

from aprice_advisor import load_jsonl
from aprice_advisor.models import Event
from aprice_advisor.retry import RetrySummary, find_retry_usage


def event(request_id, **overrides):
    values = {
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:00Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": request_id,
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
    }
    values.update(overrides)
    return Event(**values)


def test_only_events_with_an_explicit_retry_relationship_are_counted():
    result = find_retry_usage(
        [
            event("req-1"),
            event("req-2", retry_of="req-1", input_tokens=200, output_tokens=80),
            event("req-3", status="error"),
            event("req-4"),
        ]
    )

    [summary] = result
    assert summary.request_count == 1
    assert summary.input_tokens == 200
    assert summary.output_tokens == 80


def test_a_retry_chain_counts_each_retry_but_not_the_original_request():
    result = find_retry_usage(
        [
            event("req-1", input_tokens=1_000_000, output_tokens=1_000_000),
            event(
                "req-2",
                retry_of="req-1",
                input_tokens=100,
                output_tokens=40,
            ),
            event(
                "req-3",
                retry_of="req-2",
                input_tokens=250,
                output_tokens=60,
            ),
        ]
    )

    [summary] = result
    assert summary.request_count == 2
    assert summary.input_tokens == 350
    assert summary.output_tokens == 100


def test_retries_are_grouped_by_project_callsite_provider_and_model():
    result = find_retry_usage(
        [
            event("a-root"),
            event("a-retry", retry_of="a-root", callsite_id="site-a"),
            event("b-root", project_id="proj-b"),
            event(
                "b-retry",
                project_id="proj-b",
                retry_of="b-root",
                callsite_id="site-b",
                model="claude-opus-5",
            ),
        ]
    )

    assert [(item.project_id, item.callsite_id, item.model) for item in result] == [
        ("proj-a", "site-a", "claude-sonnet-5"),
        ("proj-b", "site-b", "claude-opus-5"),
    ]


def test_verified_standard_rate_is_applied_to_actual_retry_tokens():
    result = find_retry_usage(
        [
            event("req-1"),
            event(
                "req-2",
                retry_of="req-1",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
        ]
    )

    [summary] = result
    assert summary.standard_cost_usd == pytest.approx(3.00 + 15.00)


def test_unpriced_retry_usage_is_kept_without_a_guessed_cost():
    result = find_retry_usage(
        [
            event("req-1", model="unknown-model"),
            event("req-2", retry_of="req-1", model="unknown-model"),
        ]
    )

    assert result == [
        RetrySummary(
            project_id="proj-a",
            callsite_id=None,
            provider="anthropic",
            model="unknown-model",
            request_count=1,
            input_tokens=100,
            output_tokens=50,
            standard_cost_usd=None,
        )
    ]


def test_missing_and_cyclic_retry_references_never_reach_analysis(tmp_path):
    def row(request_id, retry_of=None):
        value = {
            "schema_version": 1,
            "timestamp": "2026-08-21T12:00:00Z",
            "project_id": "proj-a",
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "operation": "messages.create",
            "request_id": request_id,
            "input_tokens": 100,
            "output_tokens": 50,
            "status": "success",
        }
        if retry_of is not None:
            value["retry_of"] = retry_of
        return value

    path = tmp_path / "events.jsonl"
    rows = [
        row("missing", "not-present"),
        row("cycle-a", "cycle-b"),
        row("cycle-b", "cycle-a"),
    ]
    path.write_text("\n".join(json.dumps(value) for value in rows) + "\n")

    loaded = load_jsonl(path)
    assert loaded.events == []
    assert len(loaded.errors) == 3
    assert find_retry_usage(loaded.events) == []


def test_empty_input_returns_no_retry_usage():
    assert find_retry_usage([]) == []
