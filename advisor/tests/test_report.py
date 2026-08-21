import json

from aprice_advisor.aggregate import AggregateResult, UsageGroup, UsageGroupKey
from aprice_advisor.duplicates import DuplicateAnalysis
from aprice_advisor.models import Source
from aprice_advisor.recommendations import Recommendation
from aprice_advisor.report import AnalysisResult, render_json, render_markdown
from aprice_advisor.retry import RetrySummary


def empty_aggregate() -> AggregateResult:
    return AggregateResult(
        groups=[],
        total_request_count=0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_standard_cost_usd=0.0,
        unpriced_request_count=0,
    )


def empty_duplicates(window_seconds: float = 300.0) -> DuplicateAnalysis:
    return DuplicateAnalysis(duplicate_window_seconds=window_seconds, groups=[])


def empty_result(input_file: str = "events.jsonl") -> AnalysisResult:
    return AnalysisResult(
        input_file=input_file,
        duplicate_window_seconds=300.0,
        load_errors=[],
        aggregate=empty_aggregate(),
        retry=[],
        duplicates=empty_duplicates(),
        cache_misses=[],
        recommendations=[],
    )


def result_with_a_recommendation() -> AnalysisResult:
    key = UsageGroupKey(
        project_id="proj-a",
        provider="anthropic",
        model="claude-sonnet-5",
        operation="messages.create",
    )
    priced_group = UsageGroup(
        key=key, request_count=1, input_tokens=100, output_tokens=50, standard_cost_usd=0.00105
    )
    unpriced_group = UsageGroup(
        key=UsageGroupKey(
            project_id="proj-a",
            provider="anthropic",
            model="unknown-model",
            operation="messages.create",
        ),
        request_count=1,
        input_tokens=100,
        output_tokens=50,
        standard_cost_usd=None,
    )
    aggregate = AggregateResult(
        groups=[priced_group, unpriced_group],
        total_request_count=2,
        total_input_tokens=200,
        total_output_tokens=100,
        total_standard_cost_usd=0.00105,
        unpriced_request_count=1,
    )
    retry_summary = RetrySummary(
        project_id="proj-a",
        callsite_id="site-a",
        provider="anthropic",
        model="claude-sonnet-5",
        request_count=1,
        input_tokens=100,
        output_tokens=50,
        standard_cost_usd=0.00105,
    )
    recommendation = Recommendation(
        rule="retry",
        evidence=retry_summary,
        source=Source(file="src/app.py", line=12),
        message="Review retry policy and maximum attempts for this call.",
    )
    return AnalysisResult(
        input_file="events.jsonl",
        duplicate_window_seconds=300.0,
        load_errors=[],
        aggregate=aggregate,
        retry=[retry_summary],
        duplicates=empty_duplicates(),
        cache_misses=[],
        recommendations=[recommendation],
    )


# -- json --


def test_render_json_is_valid_and_versioned():
    payload = json.loads(render_json(empty_result()))
    assert payload["schema_version"] == 1
    assert payload["input_file"] == "events.jsonl"
    assert payload["duplicate_window_seconds"] == 300.0


def test_render_json_on_an_empty_log_is_still_valid():
    payload = json.loads(render_json(empty_result()))
    assert payload["aggregate"]["groups"] == []
    assert payload["retry"] == []
    assert payload["duplicates"] == []
    assert payload["cache_misses"] == []
    assert payload["recommendations"] == []
    assert payload["unpriced_models"] == []


def test_render_json_includes_unpriced_models_with_no_guessed_cost():
    payload = json.loads(render_json(result_with_a_recommendation()))
    assert payload["unpriced_models"] == [{"provider": "anthropic", "model": "unknown-model"}]


def test_render_json_recommendation_carries_rule_source_and_evidence():
    payload = json.loads(render_json(result_with_a_recommendation()))
    [rec] = payload["recommendations"]
    assert rec["rule"] == "retry"
    assert rec["source"] == {"file": "src/app.py", "line": 12}
    assert rec["evidence"]["request_count"] == 1


def test_render_json_load_errors_are_reported_not_dropped():
    from aprice_advisor.loader import LoadError

    result = empty_result()
    result_with_errors = AnalysisResult(
        input_file=result.input_file,
        duplicate_window_seconds=result.duplicate_window_seconds,
        load_errors=[LoadError(line=3, reason="invalid JSON")],
        aggregate=result.aggregate,
        retry=result.retry,
        duplicates=result.duplicates,
        cache_misses=result.cache_misses,
        recommendations=result.recommendations,
    )
    payload = json.loads(render_json(result_with_errors))
    assert payload["load_errors"] == [{"line": 3, "reason": "invalid JSON"}]


# -- markdown --


def test_render_markdown_states_standard_cost_meaning_and_window():
    md = render_markdown(empty_result())
    assert "not an invoice" in md
    assert "300-second" in md


def test_render_markdown_never_claims_an_actual_bill():
    md = render_markdown(result_with_a_recommendation())
    assert "actual" in md.lower()  # "not an invoice amount" / "real billing"
    assert "standard cost" in md.lower()


def test_render_markdown_on_an_empty_log_says_no_patterns_found():
    md = render_markdown(empty_result())
    assert "No waste patterns detected" in md


def test_render_markdown_lists_recommendations_with_location():
    md = render_markdown(result_with_a_recommendation())
    assert "[retry]" in md
    assert "src/app.py:12" in md


def test_render_markdown_shows_no_location_when_source_is_absent():
    result = result_with_a_recommendation()
    no_source_rec = Recommendation(
        rule=result.recommendations[0].rule,
        evidence=result.recommendations[0].evidence,
        source=None,
        message=result.recommendations[0].message,
    )
    result_without_source = AnalysisResult(
        input_file=result.input_file,
        duplicate_window_seconds=result.duplicate_window_seconds,
        load_errors=result.load_errors,
        aggregate=result.aggregate,
        retry=result.retry,
        duplicates=result.duplicates,
        cache_misses=result.cache_misses,
        recommendations=[no_source_rec],
    )
    md = render_markdown(result_without_source)
    assert "(no location)" in md


def test_render_markdown_lists_unpriced_models():
    md = render_markdown(result_with_a_recommendation())
    assert "anthropic/unknown-model" in md


def test_render_markdown_output_is_ascii_only():
    md = render_markdown(result_with_a_recommendation())
    md.encode("ascii")  # raises UnicodeEncodeError if anything isn't ASCII


def test_render_markdown_reports_skipped_load_errors():
    from aprice_advisor.loader import LoadError

    result = empty_result()
    result_with_errors = AnalysisResult(
        input_file=result.input_file,
        duplicate_window_seconds=result.duplicate_window_seconds,
        load_errors=[LoadError(line=3, reason="invalid JSON")],
        aggregate=result.aggregate,
        retry=result.retry,
        duplicates=result.duplicates,
        cache_misses=result.cache_misses,
        recommendations=result.recommendations,
    )
    md = render_markdown(result_with_errors)
    assert "1 log row(s) failed validation" in md
