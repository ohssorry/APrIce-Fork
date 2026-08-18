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
