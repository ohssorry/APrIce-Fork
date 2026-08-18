import json

from aprice import report
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
