import json

from aprice import diff, report
from aprice.models import ApiCall, CostEstimate, Finding, Price, ScanResult


def call(model="claude-sonnet-5", max_tokens=1000, loop_depth=0, line=16):
    return ApiCall(
        provider="anthropic",
        file="src/app.py",
        line=line,
        model=model,
        max_tokens=max_tokens,
        loop_depth=loop_depth,
    )


def price(model="claude-sonnet-5"):
    return Price(provider="anthropic", model=model, input_per_mtok=3.0, output_per_mtok=15.0)


def result_with_calls():
    priced = call(loop_depth=1)
    unpriced = call(model=None, max_tokens=None, line=31)
    estimate = CostEstimate(call=priced, price=price(), low_usd=0.0078, high_usd=0.018)
    finding = Finding(
        call=priced,
        rule="call-in-loop",
        severity="warn",
        message="API call inside a loop.",
    )
    return ScanResult(
        calls=[priced, unpriced],
        estimates=[estimate],
        findings=[finding],
        unpriced=[unpriced],
    )


def empty_result():
    return ScanResult()


# -- text --


def test_render_terminal_lists_cost_and_findings():
    text = report.render_terminal(result_with_calls())
    assert "src/app.py:16" in text
    assert "call-in-loop" in text
    assert "$0.00780 - $0.01800" in text


def test_render_terminal_on_empty_result():
    assert report.render_terminal(empty_result()) == "APrIce: no paid API calls found."


# -- markdown --


def test_render_markdown_includes_cost_table_and_risks():
    md = report.render_markdown(result_with_calls())
    assert "| Location | Model | Cost per request |" in md
    assert "src/app.py:16" in md
    assert "API call inside a loop." in md


def test_render_markdown_on_empty_result():
    md = report.render_markdown(empty_result())
    assert "No paid API calls found" in md


# -- json --


def test_render_json_is_valid_json_with_schema_version():
    payload = json.loads(report.render_json(result_with_calls()))
    assert payload["schema_version"] == 1


def test_render_json_includes_call_fields_and_cost_range():
    payload = json.loads(report.render_json(result_with_calls()))
    [entry] = payload["estimates"]
    assert entry["location"] == "src/app.py:16"
    assert entry["provider"] == "anthropic"
    assert entry["model"] == "claude-sonnet-5"
    assert entry["max_tokens"] == 1000
    assert entry["loop_depth"] == 1
    # A range, not a single collapsed number.
    assert entry["cost_usd"] == {"low": 0.0078, "high": 0.018}


def test_render_json_includes_findings_and_unpriced():
    payload = json.loads(report.render_json(result_with_calls()))
    assert payload["unpriced"][0]["location"] == "src/app.py:31"
    assert payload["findings"][0]["rule"] == "call-in-loop"
    assert payload["findings"][0]["severity"] == "warn"


def test_render_json_on_empty_result_is_still_valid_json():
    payload = json.loads(report.render_json(empty_result()))
    assert payload["estimates"] == []
    assert payload["unpriced"] == []
    assert payload["findings"] == []
    assert payload["total_cost_usd"] == {"low": 0.0, "high": 0.0}


# -- diff --


def diff_result_with_changes():
    added = call(model="claude-opus-5", max_tokens=2000, line=9)
    removed = call(model="claude-haiku-4-5", max_tokens=200, line=8)
    changed_base = call(model="claude-sonnet-5", max_tokens=1000, line=5)
    changed_head = call(model="claude-sonnet-5", max_tokens=4000, line=5)
    entries = [
        diff.CallDiff(
            key=("src/app.py", "anthropic", "claude-haiku-4-5"),
            status="removed",
            base=removed,
            head=None,
            base_cost=(0.0013, 0.002),
            head_cost=None,
        ),
        diff.CallDiff(
            key=("src/app.py", "anthropic", "claude-opus-5"),
            status="added",
            base=None,
            head=added,
            base_cost=None,
            head_cost=(0.02, 0.055),
        ),
        diff.CallDiff(
            key=("src/app.py", "anthropic", "claude-sonnet-5"),
            status="changed",
            base=changed_base,
            head=changed_head,
            base_cost=(0.0075, 0.018),
            head_cost=(0.021, 0.063),
        ),
    ]
    return diff.DiffResult(
        base_ref="base",
        head_ref="HEAD",
        entries=entries,
        base_low=0.0088,
        base_high=0.02,
        head_low=0.0285,
        head_high=0.081,
    )


def empty_diff_result():
    return diff.DiffResult(
        base_ref="base",
        head_ref="HEAD",
        entries=[],
        base_low=0.0,
        base_high=0.0,
        head_low=0.0,
        head_high=0.0,
    )


def test_render_diff_text_marks_added_removed_and_changed():
    text = report.render_diff_text(diff_result_with_changes())
    assert "+ src/app.py:9" in text
    assert "- src/app.py:8" in text
    assert "~ src/app.py:5" in text


def test_render_diff_text_shows_low_and_high_delta_separately():
    text = report.render_diff_text(diff_result_with_changes())
    # base total 0.0088-0.02, head total 0.0285-0.081 -> +0.0197 low, +0.061 high
    assert "low +$0.01970" in text
    assert "high +$0.06100" in text


def test_render_diff_text_on_no_changes():
    text = report.render_diff_text(empty_diff_result())
    assert "No API call changes detected." in text


def test_render_diff_markdown_includes_table_and_net_change():
    md = report.render_diff_markdown(diff_result_with_changes())
    assert "| | Location | Model | Cost change per request |" in md
    assert "Net change per request" in md


def test_render_diff_json_is_valid_and_keeps_range_not_a_point():
    payload = json.loads(report.render_diff_json(diff_result_with_changes()))
    assert payload["schema_version"] == 1
    assert payload["base_ref"] == "base"
    assert payload["head_ref"] == "HEAD"
    statuses = {e["status"] for e in payload["entries"]}
    assert statuses == {"added", "removed", "changed"}
    assert set(payload["total_delta_usd"]) == {"low", "high"}


def test_render_diff_json_keeps_unpriced_entries_not_dropped():
    unpriced_added = ApiCall(provider="anthropic", file="src/x.py", line=1, model=None)
    entries = [
        diff.CallDiff(
            key=("src/x.py", "anthropic", None),
            status="added",
            base=None,
            head=unpriced_added,
            base_cost=None,
            head_cost=None,
        )
    ]
    result = diff.DiffResult(
        base_ref="base",
        head_ref="HEAD",
        entries=entries,
        base_low=0.0,
        base_high=0.0,
        head_low=0.0,
        head_high=0.0,
    )
    payload = json.loads(report.render_diff_json(result))
    [entry] = payload["entries"]
    assert entry["status"] == "added"
    assert entry["head_cost_usd"] is None


def test_render_diff_json_on_empty_result_is_still_valid_json():
    payload = json.loads(report.render_diff_json(empty_diff_result()))
    assert payload["entries"] == []
    assert payload["total_delta_usd"] == {"low": 0.0, "high": 0.0}
