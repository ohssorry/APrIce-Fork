import json

from aprice_advisor import load_jsonl
from aprice_advisor.models import Source


def row(**overrides):
    base = {
        "schema_version": 1,
        "timestamp": "2026-08-20T12:00:00Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-1",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
    }
    base.update(overrides)
    return base


def write(tmp_path, *rows):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


# -- happy path --


def test_loads_a_valid_minimal_row(tmp_path):
    result = load_jsonl(write(tmp_path, row()))
    assert result.errors == []
    [event] = result.events
    assert event.request_id == "req-1"
    assert event.input_tokens == 100
    assert event.output_tokens == 50


def test_loads_every_optional_field(tmp_path):
    result = load_jsonl(
        write(
            tmp_path,
            row(
                trace_id="trace-1",
                request_fingerprint="opaque-abc",
                cache_eligible=True,
                cache_status="hit",
                callsite_id="site-1",
                source={"file": "src\\app.py", "line": 16},
            ),
        )
    )
    assert result.errors == []
    [event] = result.events
    assert event.trace_id == "trace-1"
    assert event.cache_eligible is True
    assert event.cache_status == "hit"
    # Normalized to forward slashes, same rule as ApiCall.location.
    assert event.source == Source(file="src/app.py", line=16)


def test_cache_status_absent_is_unknown_not_guessed(tmp_path):
    result = load_jsonl(write(tmp_path, row()))
    assert result.events[0].cache_status is None


def test_blank_lines_are_skipped_silently(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(f"\n{json.dumps(row())}\n\n")
    result = load_jsonl(path)
    assert result.errors == []
    assert len(result.events) == 1


def test_an_unknown_field_is_ignored_not_used(tmp_path):
    result = load_jsonl(write(tmp_path, row(prompt="ignore me, not part of the schema")))
    assert result.errors == []
    assert not hasattr(result.events[0], "prompt")


# -- required-field validation --


def test_invalid_json_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(f"not json\n{json.dumps(row(request_id='req-2'))}\n")
    result = load_jsonl(path)
    assert len(result.errors) == 1
    assert result.errors[0].line == 1
    assert len(result.events) == 1


def test_a_json_array_row_is_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("[1, 2, 3]\n")
    result = load_jsonl(path)
    assert result.events == []
    assert "object" in result.errors[0].reason


def test_wrong_schema_version_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(schema_version=2)))
    assert result.events == []
    assert "schema_version" in result.errors[0].reason


def test_missing_required_string_field_is_rejected(tmp_path):
    r = row()
    del r["provider"]
    result = load_jsonl(write(tmp_path, r))
    assert result.events == []
    assert "provider" in result.errors[0].reason


def test_empty_required_string_field_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(project_id="")))
    assert result.events == []


def test_timestamp_without_a_timezone_offset_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(timestamp="2026-08-20T12:00:00")))
    assert result.events == []
    assert "timestamp" in result.errors[0].reason


def test_timestamp_with_explicit_offset_is_accepted(tmp_path):
    result = load_jsonl(write(tmp_path, row(timestamp="2026-08-20T21:00:00+09:00")))
    assert result.errors == []


def test_negative_token_count_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(input_tokens=-1)))
    assert result.events == []


def test_boolean_token_count_is_rejected(tmp_path):
    # bool is a subclass of int in Python -- must be rejected explicitly.
    result = load_jsonl(write(tmp_path, row(output_tokens=True)))
    assert result.events == []


def test_non_integer_token_count_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(input_tokens=1.5)))
    assert result.events == []


def test_unknown_status_value_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(status="pending")))
    assert result.events == []


# -- request_id uniqueness and retry_of integrity --


def test_duplicate_request_id_keeps_the_first_and_drops_the_rest(tmp_path):
    result = load_jsonl(write(tmp_path, row(input_tokens=100), row(input_tokens=999)))
    assert len(result.events) == 1
    assert result.events[0].input_tokens == 100
    assert len(result.errors) == 1
    assert "duplicate" in result.errors[0].reason


def test_retry_of_referencing_an_unknown_request_id_drops_only_that_row(tmp_path):
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-1"),
            row(request_id="req-2", retry_of="does-not-exist"),
        )
    )
    assert [e.request_id for e in result.events] == ["req-1"]
    assert "retry_of" in result.errors[0].reason


def test_retry_of_pointing_forward_in_the_file_is_valid(tmp_path):
    # retry_of may reference a request_id defined later in the file --
    # resolution happens after the whole file is read.
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-2", retry_of="req-1"),
            row(request_id="req-1"),
        )
    )
    assert {e.request_id for e in result.events} == {"req-1", "req-2"}
    assert result.errors == []


def test_a_two_node_retry_of_cycle_drops_both(tmp_path):
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-a", retry_of="req-b"),
            row(request_id="req-b", retry_of="req-a"),
        )
    )
    assert result.events == []
    assert len(result.errors) == 2
    assert all("cycle" in e.reason for e in result.errors)


def test_a_self_referencing_retry_of_is_a_cycle(tmp_path):
    result = load_jsonl(write(tmp_path, row(request_id="req-a", retry_of="req-a")))
    assert result.events == []
    assert "cycle" in result.errors[0].reason


def test_a_retry_chain_that_terminates_is_not_a_cycle(tmp_path):
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-1"),
            row(request_id="req-2", retry_of="req-1"),
            row(request_id="req-3", retry_of="req-2"),
        )
    )
    assert {e.request_id for e in result.events} == {"req-1", "req-2", "req-3"}
    assert result.errors == []


def test_an_event_pointing_into_a_cycle_is_dropped_not_left_dangling(tmp_path):
    # A -> B, and B <-> C is a cycle. B and C drop as cycle members; A must
    # also drop, since its retry_of would otherwise dangle -- found in
    # review on #55 (eunji719).
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-a", retry_of="req-b"),
            row(request_id="req-b", retry_of="req-c"),
            row(request_id="req-c", retry_of="req-b"),
        )
    )
    assert result.events == []
    reasons = {e.reason for e in result.errors}
    assert any("cycle" in r for r in reasons)
    assert any("retry_of references unknown request_id" in r for r in reasons)


def test_a_longer_chain_into_a_cycle_is_fully_unwound(tmp_path):
    # req-1 -> req-2 -> req-3 <-> req-4 (cycle). Every node in the chain
    # must eventually drop, not just the immediate neighbor of the cycle.
    result = load_jsonl(
        write(
            tmp_path,
            row(request_id="req-1", retry_of="req-2"),
            row(request_id="req-2", retry_of="req-3"),
            row(request_id="req-3", retry_of="req-4"),
            row(request_id="req-4", retry_of="req-3"),
        )
    )
    assert result.events == []


# -- optional field type validation --


def test_cache_status_rejects_a_value_outside_the_enum(tmp_path):
    result = load_jsonl(write(tmp_path, row(cache_status="stale")))
    assert result.events == []


def test_source_missing_line_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(source={"file": "app.py"})))
    assert result.events == []


def test_source_with_a_non_positive_line_is_rejected(tmp_path):
    result = load_jsonl(write(tmp_path, row(source={"file": "app.py", "line": 0})))
    assert result.events == []


def test_request_fingerprint_must_be_a_string(tmp_path):
    result = load_jsonl(write(tmp_path, row(request_fingerprint=12345)))
    assert result.events == []
