import warnings
from pathlib import Path

import pytest

from aprice import detector, rules

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app.py"


def calls():
    return detector.scan_path(FIXTURE)


def test_finds_every_call_site():
    assert len(calls()) == 5


def test_reads_provider_from_attribute_path():
    providers = {c.provider for c in calls()}
    assert providers == {"anthropic", "openai"}


@pytest.mark.parametrize(
    ("expression", "provider"),
    [
        ("client.embeddings.create(model='text-embedding-3-small')", "openai"),
        ("client.images.generate(model='gpt-image-1')", "openai"),
        ("client.images.edit(model='gpt-image-1')", "openai"),
        ("client.images.create_variation(model='dall-e-2')", "openai"),
        ("client.audio.speech.create(model='gpt-4o-mini-tts')", "openai"),
        ("client.audio.transcriptions.create(model='whisper-1')", "openai"),
        ("client.audio.translations.create(model='whisper-1')", "openai"),
        ("client.completions.create(model='gpt-3.5-turbo-instruct')", "openai"),
        ("client.messages.batches.create(requests=[])", "anthropic"),
        ("client.models.embed_content(model='gemini-embedding-001')", "google"),
    ],
)
def test_detects_supported_paid_sdk_calls(expression, provider):
    detected = detector.scan_source(expression, "supported_calls.py")

    assert len(detected) == 1
    assert detected[0].provider == provider


def test_reads_literal_model_and_max_tokens():
    summarize = next(c for c in calls() if c.model == "claude-sonnet-5")
    assert summarize.max_tokens == 1024
    assert summarize.loop_depth == 0
    assert summarize.line > 0


def test_detects_loop_nesting_depth():
    depths = sorted(c.loop_depth for c in calls())
    assert depths == [0, 0, 0, 1, 2]


@pytest.mark.parametrize(
    ("range_args", "expected_bound"),
    [("5", 5), ("2, 8, 2", 3), ("5, 0, -1", 5), ("-3", 0)],
)
def test_literal_range_records_exact_iteration_bound(range_args, expected_bound):
    source = f"""
for item in range({range_args}):
    client.responses.create(model="gpt-4o", max_output_tokens=10)
"""

    call = detector.scan_source(source, "literal_range.py")[0]
    loop_findings = [
        finding for finding in rules.check(call) if finding.rule.startswith("call-in-")
    ]

    assert call.loop_depth == 1
    assert call.loop_bounds == (expected_bound,)
    assert len(loop_findings) == 1
    assert f"upper bound of {expected_bound} iterations" in loop_findings[0].message


@pytest.mark.parametrize("loop_header", ["for item in range(limit):", "while enabled:"])
def test_unknown_loop_bound_is_not_guessed(loop_header):
    source = f"""
{loop_header}
    client.responses.create(model="gpt-4o", max_output_tokens=10)
"""

    call = detector.scan_source(source, "unknown_bound.py")[0]
    loop_finding = next(finding for finding in rules.check(call) if finding.rule == "call-in-loop")

    assert call.loop_bounds == (None,)
    assert "static upper bound" not in loop_finding.message
    assert "which this tool cannot see" in loop_finding.message


def test_nested_loop_bounds_preserve_outer_to_inner_order_without_multiplying():
    source = """
for outer in range(2):
    for inner in range(limit):
        client.responses.create(model="gpt-4o", max_output_tokens=10)
"""

    call = detector.scan_source(source, "nested_bounds.py")[0]
    loop_findings = [
        finding for finding in rules.check(call) if finding.rule.startswith("call-in-")
    ]

    assert call.loop_bounds == (2, None)
    assert len(loop_findings) == 1
    assert "iteration bounds [2, unknown]" in loop_findings[0].message


def test_comprehension_records_literal_range_bound():
    source = """
[
    client.responses.create(model="gpt-4o", max_output_tokens=10)
    for item in range(4)
]
"""

    call = detector.scan_source(source, "bounded_comprehension.py")[0]

    assert call.loop_depth == 1
    assert call.loop_bounds == (4,)


def test_comprehension_iterable_is_evaluated_before_loop():
    source = """
[
    client.responses.create(model="body", max_tokens=10)
    for item in client.responses.create(model="iterable", max_tokens=10)
    if client.responses.create(model="condition", max_tokens=10)
]
"""

    detected = {call.model: call for call in detector.scan_source(source, "comprehension.py")}

    assert detected["iterable"].loop_depth == 0
    assert detected["condition"].loop_depth == 1
    assert detected["body"].loop_depth == 1
    assert not any(f.rule == "call-in-loop" for f in rules.check(detected["iterable"]))


def test_comprehension_generators_increase_depth_in_evaluation_order():
    source = """
[
    client.responses.create(model="body", max_tokens=10)
    for first in client.responses.create(model="outer-iterable", max_tokens=10)
    if client.responses.create(model="outer-condition", max_tokens=10)
    for second in client.responses.create(model="inner-iterable", max_tokens=10)
    if client.responses.create(model="inner-condition", max_tokens=10)
]
"""

    detected = {call.model: call for call in detector.scan_source(source, "nested_comp.py")}

    assert detected["outer-iterable"].loop_depth == 0
    assert detected["outer-condition"].loop_depth == 1
    assert detected["inner-iterable"].loop_depth == 1
    assert detected["inner-condition"].loop_depth == 2
    assert detected["body"].loop_depth == 2


def test_all_comprehension_result_kinds_run_inside_the_loop():
    sources = (
        "[client.responses.create(model='list', max_tokens=10) for x in items]",
        "{client.responses.create(model='set', max_tokens=10) for x in items}",
        "{x: client.responses.create(model='dict', max_tokens=10) for x in items}",
        "(client.responses.create(model='generator', max_tokens=10) for x in items)",
    )

    for source in sources:
        call = detector.scan_source(source, "comprehension_kinds.py")[0]
        assert call.loop_depth == 1


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


def test_comments_and_strings_are_not_detected_as_calls():
    source = '''
# client.messages.create(model="comment", max_tokens=10)
example = "client.responses.create(model='string', max_tokens=10)"
"""client.models.generate_content(model='docstring')"""
'''

    assert detector.scan_source(source, "non_code.py") == []


def test_bare_functions_with_endpoint_names_are_not_sdk_calls():
    source = """
def create():
    return None

def generate_content():
    return None

create()
generate_content()
"""

    assert detector.scan_source(source, "same_names.py") == []


def test_async_api_call_is_detected_inside_its_loop():
    source = """
async def summarize_all(documents):
    async for document in documents:
        await client.messages.create(
            model="claude-sonnet-5",
            max_tokens=128,
            messages=[],
        )
"""

    detected = detector.scan_source(source, "async_calls.py")

    assert len(detected) == 1
    assert detected[0].model == "claude-sonnet-5"
    assert detected[0].loop_depth == 1


def test_unknown_call_shape_produces_no_findings():
    source = "service.generate(model='unknown', max_tokens=10)"

    detected = detector.scan_source(source, "unknown_sdk.py")

    assert detected == []
    assert rules.check_all(detected) == []


def test_unparseable_source_is_skipped_not_fatal():
    with pytest.warns(
        detector.ParseFailureWarning, match="Could not parse Python source"
    ) as caught:
        assert detector.scan_source("def broken(:\n", "broken.py") == []

    assert caught[0].filename == "broken.py"
    assert caught[0].lineno == 1
    assert "column" in str(caught[0].message)


def test_source_without_calls_does_not_emit_parse_failure_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", detector.ParseFailureWarning)
        assert detector.scan_source("value = 1\n", "valid.py") == []


def test_scan_path_reports_broken_file_and_keeps_valid_results(tmp_path: Path):
    valid = tmp_path / "valid.py"
    broken = tmp_path / "broken.py"
    valid.write_text(
        'client.messages.create(model="claude-sonnet-5", max_tokens=10)\n',
        encoding="utf-8",
    )
    broken.write_text("def broken(:\n", encoding="utf-8")

    with pytest.warns(detector.ParseFailureWarning) as caught:
        detected = detector.scan_path(tmp_path)

    assert len(detected) == 1
    assert detected[0].model == "claude-sonnet-5"
    assert Path(caught[0].filename) == broken
    assert caught[0].lineno == 1


def test_call_in_loop_raises_a_warning():
    looped = next(c for c in calls() if c.loop_depth == 1)
    assert any(f.rule == "call-in-loop" for f in rules.check(looped))


def test_nested_loop_raises_the_nested_rule():
    nested = next(c for c in calls() if c.loop_depth == 2)
    assert any(f.rule == "call-in-nested-loop" for f in rules.check(nested))


def test_plain_call_raises_no_warnings():
    plain = next(c for c in calls() if c.model == "claude-sonnet-5")
    assert [f for f in rules.check(plain) if f.severity == "warn"] == []
