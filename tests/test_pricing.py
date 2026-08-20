import datetime as dt
from pathlib import Path

import pytest

from aprice import pricing
from aprice.models import ApiCall


def call(model="claude-sonnet-5", max_tokens=1000, provider="anthropic"):
    return ApiCall(provider=provider, file="f.py", line=1, model=model, max_tokens=max_tokens)


def test_known_model_resolves_to_a_price():
    price = pricing.lookup("anthropic", "claude-opus-5")
    assert price is not None
    assert price.input_per_mtok == 5.00
    assert price.output_per_mtok == 25.00


def test_unknown_model_has_no_price():
    assert pricing.lookup("anthropic", "claude-does-not-exist") is None


def test_dynamic_model_has_no_price():
    assert pricing.lookup("anthropic", None) is None


def test_estimate_is_a_range_not_a_point():
    est = pricing.estimate(call(), input_tokens=1000)
    assert est is not None
    assert est.low_usd < est.high_usd


def test_upper_bound_assumes_max_tokens_is_fully_used():
    est = pricing.estimate(call(max_tokens=1000), input_tokens=1000)
    # 1000 input @ $3/Mtok + 1000 output @ $15/Mtok
    assert est.high_usd == pytest.approx(0.003 + 0.015)


def test_unpriceable_call_returns_none_rather_than_guessing():
    assert pricing.estimate(call(model=None)) is None


def test_negative_input_tokens_is_rejected():
    with pytest.raises(ValueError, match="input_tokens must be >= 0"):
        pricing.estimate(call(), input_tokens=-1)


def test_negative_input_tokens_is_rejected_even_for_an_unpriced_model():
    # A bad input_tokens is invalid regardless of whether the model would
    # have been priceable -- it must not be masked by the None-for-unknown
    # path returning quietly instead.
    with pytest.raises(ValueError, match="input_tokens must be >= 0"):
        pricing.estimate(call(model=None), input_tokens=-1)


def test_zero_input_tokens_is_a_valid_estimate():
    est = pricing.estimate(call(), input_tokens=0)
    assert est is not None
    assert est.low_usd >= 0
    assert est.high_usd >= 0


def test_every_price_entry_is_wellformed():
    for (provider, model), price in pricing.load_prices().items():
        assert provider and model
        assert price.input_per_mtok >= 0
        assert price.output_per_mtok >= 0


def test_openai_model_resolves_to_a_price():
    price = pricing.lookup("openai", "gpt-4o")
    assert price is not None
    assert price.input_per_mtok == 2.50
    assert price.output_per_mtok == 10.00


def test_openai_prices_have_no_zero_placeholders():
    # B-01: unverified models must be left out of the file rather than
    # kept with a 0.00 placeholder, so a priced OpenAI model should never
    # come back free.
    for (provider, _model), price in pricing.load_prices().items():
        if provider != "openai":
            continue
        assert price.input_per_mtok > 0
        assert price.output_per_mtok > 0


def test_google_model_resolves_to_a_price():
    price = pricing.lookup("google", "gemini-2.5-flash")
    assert price is not None
    assert price.input_per_mtok == 0.30
    assert price.output_per_mtok == 2.50


def test_google_prices_have_no_zero_placeholders():
    # B-02: unverified models must be left out of the file rather than
    # kept with a 0.00 placeholder, so a priced Google model should never
    # come back free.
    for (provider, _model), price in pricing.load_prices().items():
        if provider != "google":
            continue
        assert price.input_per_mtok > 0
        assert price.output_per_mtok > 0


# B-03: schema validation for prices/*.yaml. Exercises _validate_entry
# directly with in-memory dicts so a bad-data test doesn't need a temp file
# on disk or to disturb the load_prices() cache used by the tests above.


def entry(**overrides):
    base = {
        "id": "test-model",
        "input_per_mtok": 1.0,
        "output_per_mtok": 2.0,
        "verified_on": "2026-01-01",
    }
    base.update(overrides)
    return base


def test_valid_entry_builds_a_price():
    price = pricing._validate_entry(entry(), "test", Path("test.yaml"), set())
    assert price.model == "test-model"
    assert price.input_per_mtok == 1.0


@pytest.mark.parametrize(
    "missing_field", ["id", "input_per_mtok", "output_per_mtok", "verified_on"]
)
def test_missing_required_field_is_rejected(missing_field):
    bad = entry()
    del bad[missing_field]
    with pytest.raises(pricing.PriceFileError, match="missing required field"):
        pricing._validate_entry(bad, "test", Path("test.yaml"), set())


def test_duplicate_model_id_is_rejected():
    seen = {"test-model"}
    with pytest.raises(pricing.PriceFileError, match="duplicate model id"):
        pricing._validate_entry(entry(), "test", Path("test.yaml"), seen)


@pytest.mark.parametrize("field", ["input_per_mtok", "output_per_mtok"])
def test_negative_price_is_rejected(field):
    with pytest.raises(pricing.PriceFileError, match="negative price"):
        pricing._validate_entry(entry(**{field: -1.0}), "test", Path("test.yaml"), set())


@pytest.mark.parametrize("field", ["input_per_mtok", "output_per_mtok"])
def test_zero_placeholder_price_is_rejected(field):
    with pytest.raises(pricing.PriceFileError, match="placeholder"):
        pricing._validate_entry(entry(**{field: 0.0}), "test", Path("test.yaml"), set())


def test_malformed_verified_on_date_is_rejected():
    with pytest.raises(pricing.PriceFileError, match="YYYY-MM-DD"):
        pricing._validate_entry(entry(verified_on="not-a-date"), "test", Path("test.yaml"), set())


def test_future_verified_on_date_is_rejected():
    with pytest.raises(pricing.PriceFileError, match="future"):
        pricing._validate_entry(entry(verified_on="2999-01-01"), "test", Path("test.yaml"), set())


def test_verified_on_dated_tomorrow_is_rejected():
    # Boundary case: no forward grace, so even a date one day past UTC
    # "today" must be caught as future -- see PR review on #12.
    tomorrow = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()
    with pytest.raises(pricing.PriceFileError, match="future"):
        pricing._validate_entry(entry(verified_on=tomorrow), "test", Path("test.yaml"), set())


def test_error_message_names_the_file_and_the_model():
    with pytest.raises(pricing.PriceFileError, match=r"bogus\.yaml.*test-model"):
        pricing._validate_entry(entry(input_per_mtok=-1.0), "test", Path("bogus.yaml"), set())


# Integration tests for load_prices() itself: these exercise the real
# file-loading path (not _validate_entry directly), so a YAML syntax error or
# a document that isn't a mapping still surfaces as a PriceFileError naming
# the offending file, rather than a bare yaml.YAMLError/AttributeError.


def _reload_prices_from(monkeypatch, tmp_path):
    monkeypatch.setattr(pricing, "PRICES_DIR", tmp_path)
    pricing.load_prices.cache_clear()


def test_load_prices_wraps_invalid_yaml_syntax(tmp_path, monkeypatch):
    (tmp_path / "broken.yaml").write_text("provider: openai\nmodels: [\n")
    _reload_prices_from(monkeypatch, tmp_path)
    try:
        with pytest.raises(pricing.PriceFileError, match="broken.yaml"):
            pricing.load_prices()
    finally:
        pricing.load_prices.cache_clear()


def test_load_prices_wraps_non_mapping_document(tmp_path, monkeypatch):
    (tmp_path / "list.yaml").write_text("- just\n- a\n- list\n")
    _reload_prices_from(monkeypatch, tmp_path)
    try:
        with pytest.raises(pricing.PriceFileError, match="list.yaml"):
            pricing.load_prices()
    finally:
        pricing.load_prices.cache_clear()


def test_load_prices_wraps_non_list_models_field(tmp_path, monkeypatch):
    (tmp_path / "bad_models.yaml").write_text("provider: openai\nmodels: not-a-list\n")
    _reload_prices_from(monkeypatch, tmp_path)
    try:
        with pytest.raises(pricing.PriceFileError, match="bad_models.yaml"):
            pricing.load_prices()
    finally:
        pricing.load_prices.cache_clear()
