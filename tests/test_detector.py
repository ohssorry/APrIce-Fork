from pathlib import Path

from aprice import detector, rules

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app.py"


def calls():
    return detector.scan_path(FIXTURE)


def test_finds_every_call_site():
    assert len(calls()) == 5


def test_reads_provider_from_attribute_path():
    providers = {c.provider for c in calls()}
    assert providers == {"anthropic", "openai"}


def test_reads_literal_model_and_max_tokens():
    summarize = next(c for c in calls() if c.model == "claude-sonnet-5")
    assert summarize.max_tokens == 1024
    assert summarize.loop_depth == 0
    assert summarize.line > 0


def test_detects_loop_nesting_depth():
    depths = sorted(c.loop_depth for c in calls())
    assert depths == [0, 0, 0, 1, 2]


def test_model_from_variable_is_detected_but_not_resolved():
    dynamic = next(c for c in calls() if c.model is None and c.max_tokens == 512)
    assert dynamic.provider == "anthropic"


def test_missing_max_tokens_is_none_not_zero():
    no_ceiling = next(c for c in calls() if c.model == "gpt-4o-mini")
    assert no_ceiling.max_tokens is None


def test_responses_reads_max_output_tokens_without_missing_limit_finding():
    source = """
client.responses.create(
    model="gpt-4o",
    max_output_tokens=9999,
)
"""

    call = detector.scan_source(source, "responses.py")[0]

    assert call.max_tokens == 9999
    assert all(finding.rule != "no-max-tokens" for finding in rules.check(call))


def test_chat_completions_reads_max_completion_tokens():
    source = """
client.chat.completions.create(
    model="gpt-4o",
    max_completion_tokens=2048,
)
"""

    call = detector.scan_source(source, "chat.py")[0]

    assert call.max_tokens == 2048


def test_dynamic_provider_specific_output_limit_remains_unknown():
    source = """
client.responses.create(
    model="gpt-4o",
    max_output_tokens=limit,
)
"""

    call = detector.scan_source(source, "dynamic.py")[0]

    assert call.max_tokens is None
    assert any(finding.rule == "no-max-tokens" for finding in rules.check(call))


def test_unparseable_source_is_skipped_not_fatal():
    assert detector.scan_source("def broken(:\n", "broken.py") == []


def test_call_in_loop_raises_a_warning():
    looped = next(c for c in calls() if c.loop_depth == 1)
    assert any(f.rule == "call-in-loop" for f in rules.check(looped))


def test_nested_loop_raises_the_nested_rule():
    nested = next(c for c in calls() if c.loop_depth == 2)
    assert any(f.rule == "call-in-nested-loop" for f in rules.check(nested))


def test_plain_call_raises_no_warnings():
    plain = next(c for c in calls() if c.model == "claude-sonnet-5")
    assert [f for f in rules.check(plain) if f.severity == "warn"] == []
