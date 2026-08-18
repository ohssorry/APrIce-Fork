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


def test_model_from_variable_is_detected_but_not_resolved():
    dynamic = next(c for c in calls() if c.model is None and c.max_tokens == 512)
    assert dynamic.provider == "anthropic"


def test_missing_max_tokens_is_none_not_zero():
    no_ceiling = next(c for c in calls() if c.model == "gpt-4o-mini")
    assert no_ceiling.max_tokens is None


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
