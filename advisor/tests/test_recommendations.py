import json

import pytest

from aprice_advisor.models import Event, Source
from aprice_advisor.recommendations import (
    CallsiteMapError,
    build_recommendations,
    load_callsite_map,
)


def event(request_id, minute=0, **overrides):
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
    }
    values.update(overrides)
    return Event(**values)


def test_explicit_event_source_is_attached_to_the_recommendation():
    source = Source(file="src/app.py", line=12)
    result = build_recommendations(
        [
            event(
                "req-1",
                cache_eligible=True,
                cache_status="miss",
                source=source,
            )
        ]
    )

    [recommendation] = result
    assert recommendation.rule == "cache-miss"
    assert recommendation.source == source


def test_callsite_mapping_is_used_only_when_event_source_is_absent(tmp_path):
    path = tmp_path / "callsites.json"
    path.write_text(json.dumps({"site-a": {"file": "src\\worker.py", "line": 27}}))
    callsites = load_callsite_map(path)

    result = build_recommendations(
        [
            event("req-1", callsite_id="site-a"),
            event("req-2", callsite_id="site-a", retry_of="req-1"),
        ],
        callsites=callsites,
    )

    [recommendation] = result
    assert recommendation.rule == "retry"
    assert recommendation.source == Source(file="src/worker.py", line=27)


def test_location_is_none_when_log_and_mapping_have_no_evidence():
    result = build_recommendations(
        [
            event("req-1", request_fingerprint="opaque"),
            event("req-2", minute=1, request_fingerprint="opaque"),
        ],
        callsites={"unrelated": Source(file="not/this/call.py", line=1)},
    )

    [recommendation] = result
    assert recommendation.rule == "potential-duplicate"
    assert recommendation.source is None


def test_every_recommendation_keeps_its_rule_observation_and_direction():
    result = build_recommendations(
        [
            event("cache", cache_eligible=True, cache_status="miss"),
            event("dup-original", request_fingerprint="same"),
            event("dup-repeat", minute=1, request_fingerprint="same"),
            event("retry-original"),
            event("retry-repeat", retry_of="retry-original"),
        ]
    )

    by_rule = {item.rule: item for item in result}
    assert set(by_rule) == {"cache-miss", "potential-duplicate", "retry"}
    assert by_rule["cache-miss"].evidence.request_count == 1
    assert by_rule["potential-duplicate"].evidence.duplicate_request_count == 1
    assert by_rule["retry"].evidence.request_count == 1
    assert "cache" in by_rule["cache-miss"].message
    assert "deduplication" in by_rule["potential-duplicate"].message
    assert "maximum attempts" in by_rule["retry"].message


def test_direct_source_takes_precedence_over_callsite_mapping():
    direct = Source(file="src/direct.py", line=8)
    result = build_recommendations(
        [
            event(
                "req-1",
                callsite_id="site-a",
                cache_eligible=True,
                cache_status="miss",
                source=direct,
            )
        ],
        callsites={"site-a": Source(file="src/mapped.py", line=99)},
    )

    assert result[0].source == direct


def test_empty_events_produce_no_recommendations():
    assert build_recommendations([]) == []


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "JSON object"),
        ({"site-a": "src/app.py:1"}, "must map to an object"),
        ({"site-a": {"line": 1}}, ".file"),
        ({"site-a": {"file": "src/app.py", "line": 0}}, ".line"),
        ({"site-a": {"file": "src/app.py", "line": True}}, ".line"),
    ],
)
def test_invalid_callsite_map_shape_is_rejected(tmp_path, document, message):
    path = tmp_path / "callsites.json"
    path.write_text(json.dumps(document))

    with pytest.raises(CallsiteMapError, match=message):
        load_callsite_map(path)


def test_invalid_callsite_map_json_is_rejected(tmp_path):
    path = tmp_path / "callsites.json"
    path.write_text("not json")

    with pytest.raises(CallsiteMapError, match="invalid callsite map"):
        load_callsite_map(path)
