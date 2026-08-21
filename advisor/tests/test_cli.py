import json

import pytest

from aprice_advisor import cli

EVENT_TEMPLATE = {
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
}


def write_jsonl(tmp_path, *rows):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n" if rows else "")
    return path


def event(**overrides):
    row = dict(EVENT_TEMPLATE)
    row.update(overrides)
    return row


def test_format_choices_are_json_and_markdown():
    parser = cli.build_parser()
    command = next(a for a in parser._actions if a.dest == "command")
    analyze_parser = command.choices["analyze"]
    format_action = next(a for a in analyze_parser._actions if a.dest == "format")
    assert set(format_action.choices) == {"json", "markdown"}


def test_analyze_missing_file_exits_2(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.jsonl"
    code = cli.main(["analyze", str(missing)])
    err = capsys.readouterr().err
    assert code == 2
    assert "no such file" in err


def test_analyze_markdown_runs_clean(tmp_path, capsys):
    path = write_jsonl(tmp_path, event())
    code = cli.main(["analyze", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "APrIce Advisor report" in out


def test_analyze_json_is_parseable_and_versioned(tmp_path, capsys):
    path = write_jsonl(tmp_path, event())
    code = cli.main(["analyze", str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema_version"] == 1
    assert payload["input_file"] == str(path)


def test_analyze_on_an_empty_log_is_still_valid(tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    code = cli.main(["analyze", str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["aggregate"]["groups"] == []


def test_analyze_reports_an_unregistered_model_as_unpriced(tmp_path, capsys):
    path = write_jsonl(tmp_path, event(model="some-unreleased-model"))
    code = cli.main(["analyze", str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["unpriced_models"] == [
        {"provider": "anthropic", "model": "some-unreleased-model"}
    ]


def test_analyze_with_a_waste_pattern_but_no_location_still_succeeds(tmp_path, capsys):
    path = write_jsonl(
        tmp_path,
        event(request_id="req-1"),
        event(request_id="req-2", retry_of="req-1"),
    )
    code = cli.main(["analyze", str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    [rec] = payload["recommendations"]
    assert rec["rule"] == "retry"
    assert rec["source"] is None


def test_duplicate_window_flag_is_parsed_and_reported(tmp_path, capsys):
    path = write_jsonl(tmp_path, event())
    code = cli.main(["analyze", str(path), "--duplicate-window", "1m", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["duplicate_window_seconds"] == 60.0


def test_invalid_duplicate_window_format_exits_2(tmp_path, capsys):
    # A bad --duplicate-window is caught by argparse's own type= validation,
    # which exits the process directly (SystemExit) rather than returning --
    # this is what an actual invalid `aprice-advisor` invocation does too.
    path = write_jsonl(tmp_path, event())
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["analyze", str(path), "--duplicate-window", "not-a-duration"])
    capsys.readouterr()
    assert exc_info.value.code == 2


def test_zero_duplicate_window_is_rejected(tmp_path, capsys):
    path = write_jsonl(tmp_path, event())
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["analyze", str(path), "--duplicate-window", "0s"])
    capsys.readouterr()
    assert exc_info.value.code == 2


def test_callsites_flag_resolves_a_recommendation_location(tmp_path, capsys):
    events_path = write_jsonl(
        tmp_path,
        event(request_id="req-1", callsite_id="site-a"),
        event(request_id="req-2", retry_of="req-1", callsite_id="site-a"),
    )
    callsites_path = tmp_path / "callsites.json"
    callsites_path.write_text(json.dumps({"site-a": {"file": "src/worker.py", "line": 27}}))

    code = cli.main(
        [
            "analyze",
            str(events_path),
            "--callsites",
            str(callsites_path),
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    [rec] = payload["recommendations"]
    assert rec["source"] == {"file": "src/worker.py", "line": 27}


def test_invalid_callsites_file_exits_2(tmp_path, capsys):
    events_path = write_jsonl(tmp_path, event())
    callsites_path = tmp_path / "callsites.json"
    callsites_path.write_text("not json")

    code = cli.main(["analyze", str(events_path), "--callsites", str(callsites_path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "invalid callsite map" in err


def test_load_errors_from_broken_rows_are_reported_not_silently_dropped(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text("not json\n" + json.dumps(event()) + "\n")

    code = cli.main(["analyze", str(path), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(payload["load_errors"]) == 1
    assert payload["aggregate"]["total_request_count"] == 1
