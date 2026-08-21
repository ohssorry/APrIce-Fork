from dataclasses import asdict

from aprice_advisor.cache import CacheMissSummary, find_cache_misses
from aprice_advisor.models import Event


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


def test_only_an_explicitly_eligible_miss_is_reported():
    result = find_cache_misses(
        [
            event("eligible-miss", cache_eligible=True, cache_status="miss"),
            event("cache-hit", cache_eligible=True, cache_status="hit"),
            event("not-eligible", cache_eligible=False, cache_status="miss"),
            event("eligibility-unknown", cache_status="miss"),
            event("status-unknown", cache_eligible=True),
        ]
    )

    assert result == [
        CacheMissSummary(
            project_id="proj-a",
            callsite_id=None,
            model="claude-sonnet-5",
            request_count=1,
            input_tokens=100,
        )
    ]


def test_misses_are_grouped_by_project_callsite_and_model():
    result = find_cache_misses(
        [
            event(
                "req-1",
                callsite_id="site-a",
                cache_eligible=True,
                cache_status="miss",
                input_tokens=100,
            ),
            event(
                "req-2",
                callsite_id="site-a",
                cache_eligible=True,
                cache_status="miss",
                input_tokens=250,
            ),
            event(
                "req-3",
                project_id="proj-b",
                callsite_id="site-a",
                cache_eligible=True,
                cache_status="miss",
                input_tokens=400,
            ),
            event(
                "req-4",
                callsite_id="site-b",
                model="claude-opus-5",
                cache_eligible=True,
                cache_status="miss",
                input_tokens=800,
            ),
        ]
    )

    assert result == [
        CacheMissSummary("proj-a", "site-a", "claude-sonnet-5", 2, 350),
        CacheMissSummary("proj-a", "site-b", "claude-opus-5", 1, 800),
        CacheMissSummary("proj-b", "site-a", "claude-sonnet-5", 1, 400),
    ]


def test_missing_callsite_is_kept_as_unknown_instead_of_guessed():
    result = find_cache_misses(
        [event("req-1", cache_eligible=True, cache_status="miss", callsite_id=None)]
    )

    assert result[0].callsite_id is None


def test_result_does_not_invent_a_dollar_saving():
    [summary] = find_cache_misses([event("req-1", cache_eligible=True, cache_status="miss")])

    assert set(asdict(summary)) == {
        "project_id",
        "callsite_id",
        "model",
        "request_count",
        "input_tokens",
    }


def test_empty_input_returns_no_candidates():
    assert find_cache_misses([]) == []
