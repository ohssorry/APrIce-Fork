"""End-to-end verification of the whole Advisor pipeline together.

Issue #42: every module already has its own unit tests, but nothing had
exercised loader -> aggregate -> retry/duplicates/cache -> recommendations
-> report as one realistic log. This fixture deliberately packs in every
verification scenario from that issue at once:

- a broken JSON row and a duplicate request_id (both must be skipped, not
  fatal)
- negative tokens (rejected by the loader)
- an explicit retry (retry_of) vs. an ordinary repeated call with no
  retry_of (must NOT be treated as a retry)
- a fingerprint duplicate inside the window, one outside it, and a call
  with no fingerprint at all (must NOT be treated as a duplicate)
- an explicit cache-eligible miss vs. cache_eligible=False (must NOT be
  flagged)
- a location from the log's own `source` vs. one resolved only through a
  user-supplied callsite map
- a model with no verified price

If any single module regresses in a way that breaks the combination (not
just its own isolated unit tests), this is the test that should catch it.
"""

import json

from aprice_advisor import cli

ROWS = [
    "not valid json at all",
    {
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:00Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-1",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "source": {"file": "src/summarize.py", "line": 16},
    },
    {  # duplicate request_id -- must be skipped, first occurrence kept
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:01Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-1",
        "input_tokens": 999,
        "output_tokens": 999,
        "status": "success",
    },
    {  # explicit retry of req-1
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:02Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-2",
        "retry_of": "req-1",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "error",
        "source": {"file": "src/summarize.py", "line": 16},
    },
    {  # ordinary repeated call, no retry_of -- not a retry
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:03Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-3",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
    },
    {  # negative tokens -- rejected by the loader
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:04Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-4",
        "input_tokens": -50,
        "output_tokens": 50,
        "status": "success",
    },
    {  # explicit cache-eligible miss, only a callsite_id (no direct source)
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:05Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-5",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "cache_eligible": True,
        "cache_status": "miss",
        "callsite_id": "site-cache",
    },
    {  # cache_eligible False -- not evidence of a cache miss
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:06Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-6",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "cache_eligible": False,
    },
    {  # fingerprint duplicate, within the default 5-minute window
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:07Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-7",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "request_fingerprint": "fp-1",
    },
    {
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:08Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "request_fingerprint": "fp-1",
    },
    {  # same fingerprint, 10 minutes later -- outside the default window
        "schema_version": 1,
        "timestamp": "2026-08-21T12:10:00Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-9",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
        "request_fingerprint": "fp-1",
    },
    {  # unpriced model
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:10Z",
        "project_id": "proj-a",
        "provider": "anthropic",
        "model": "not-a-real-model",
        "operation": "messages.create",
        "request_id": "req-10",
        "input_tokens": 100,
        "output_tokens": 50,
        "status": "success",
    },
    {  # a second project -- must stay a separate aggregate group, not merged
        # into proj-a's
        "schema_version": 1,
        "timestamp": "2026-08-21T12:00:11Z",
        "project_id": "proj-b",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "operation": "messages.create",
        "request_id": "req-11",
        "input_tokens": 200,
        "output_tokens": 100,
        "status": "success",
    },
]

# claude-sonnet-5 is $3.00 / $15.00 per Mtok in src/aprice/prices/anthropic.yaml.
_SONNET_INPUT_RATE = 3.00
_SONNET_OUTPUT_RATE = 15.00


def _sonnet_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * _SONNET_INPUT_RATE
        + output_tokens / 1_000_000 * _SONNET_OUTPUT_RATE
    )


def _write_fixture(tmp_path):
    events_path = tmp_path / "events.jsonl"
    lines = [row if isinstance(row, str) else json.dumps(row) for row in ROWS]
    events_path.write_text("\n".join(lines) + "\n")

    callsites_path = tmp_path / "callsites.json"
    callsites_path.write_text(json.dumps({"site-cache": {"file": "src/embed.py", "line": 42}}))
    return events_path, callsites_path


def _run(tmp_path, capsys, *extra_args):
    events_path, callsites_path = _write_fixture(tmp_path)
    code = cli.main(
        ["analyze", str(events_path), "--callsites", str(callsites_path), "--format", "json"]
        + list(extra_args)
    )
    payload = json.loads(capsys.readouterr().out)
    return code, payload


def test_broken_and_duplicate_rows_are_skipped_not_fatal(tmp_path, capsys):
    code, payload = _run(tmp_path, capsys)
    assert code == 0
    # "not valid json", the duplicate request_id, and the negative-token row.
    assert len(payload["load_errors"]) == 3


def test_valid_rows_survive_alongside_the_broken_ones(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    # 12 data rows minus the duplicate request_id (req-1 twice) and the
    # negative-token row (req-4), both rejected -- see the load_errors count.
    assert payload["aggregate"]["total_request_count"] == 10


def test_project_level_groups_stay_separate_with_exact_totals(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    groups_by_project = {
        g["project_id"]: g
        for g in payload["aggregate"]["groups"]
        if g["provider"] == "anthropic" and g["model"] == "claude-sonnet-5"
    }

    # proj-a: req-1, req-2, req-3, req-5, req-6, req-7, req-8, req-9 -- 8
    # claude-sonnet-5 requests at 100/50 tokens each (req-10 is a different
    # model and gets its own group; the duplicate req-1 and req-4 never
    # made it into the log at all).
    proj_a = groups_by_project["proj-a"]
    assert proj_a["request_count"] == 8
    assert proj_a["input_tokens"] == 800
    assert proj_a["output_tokens"] == 400
    assert proj_a["standard_cost_usd"] == _sonnet_cost(800, 400)

    # proj-b: req-11 only, 200/100 tokens -- must not be merged into proj-a.
    proj_b = groups_by_project["proj-b"]
    assert proj_b["request_count"] == 1
    assert proj_b["input_tokens"] == 200
    assert proj_b["output_tokens"] == 100
    assert proj_b["standard_cost_usd"] == _sonnet_cost(200, 100)

    # The grand total is exactly the two priced groups summed -- the
    # unpriced not-a-real-model request (req-10) must not contribute.
    expected_total = _sonnet_cost(800, 400) + _sonnet_cost(200, 100)
    assert payload["aggregate"]["total_standard_cost_usd"] == expected_total


def test_only_the_explicit_retry_is_counted_not_the_ordinary_repeat(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    assert len(payload["retry"]) == 1
    [retry] = payload["retry"]
    assert retry["request_count"] == 1  # req-2 only; req-1 and req-3 are not retries


def test_fingerprint_window_and_no_fingerprint_calls_are_distinguished(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    assert len(payload["duplicates"]) == 1
    [dup] = payload["duplicates"]
    assert dup["duplicate_request_count"] == 1  # req-8 only; req-9 is outside the window


def test_cache_eligible_false_is_not_flagged_as_a_miss(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    assert len(payload["cache_misses"]) == 1
    [miss] = payload["cache_misses"]
    assert miss["request_count"] == 1  # req-5 only; req-6 (cache_eligible=False) is excluded


def test_source_and_callsite_locations_both_resolve(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    by_rule = {rec["rule"]: rec for rec in payload["recommendations"]}
    assert by_rule["retry"]["source"] == {"file": "src/summarize.py", "line": 16}
    assert by_rule["cache-miss"]["source"] == {"file": "src/embed.py", "line": 42}


def test_duplicate_recommendation_has_no_location_when_the_log_has_none(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    by_rule = {rec["rule"]: rec for rec in payload["recommendations"]}
    assert by_rule["potential-duplicate"]["source"] is None


def test_unpriced_model_is_reported_and_excluded_from_the_cost_total(tmp_path, capsys):
    _, payload = _run(tmp_path, capsys)
    assert payload["unpriced_models"] == [{"provider": "anthropic", "model": "not-a-real-model"}]
    assert payload["aggregate"]["unpriced_request_count"] == 1


def test_standard_cost_is_never_labeled_as_an_actual_bill(tmp_path, capsys):
    events_path, callsites_path = _write_fixture(tmp_path)
    code = cli.main(
        ["analyze", str(events_path), "--callsites", str(callsites_path), "--format", "markdown"]
    )
    md = capsys.readouterr().out
    assert code == 0
    assert "not an invoice amount" in md
    assert "actual bill" not in md.lower()


def test_markdown_report_is_ascii_only(tmp_path, capsys):
    events_path, callsites_path = _write_fixture(tmp_path)
    cli.main(["analyze", str(events_path), "--callsites", str(callsites_path)])
    md = capsys.readouterr().out
    md.encode("ascii")


def test_an_entirely_empty_log_still_produces_a_valid_report(tmp_path, capsys):
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("")

    code = cli.main(["analyze", str(empty_path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["aggregate"]["total_request_count"] == 0
    assert payload["recommendations"] == []
    assert payload["unpriced_models"] == []
