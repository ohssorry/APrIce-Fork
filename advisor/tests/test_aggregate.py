from aprice_advisor import aggregate_usage
from aprice_advisor.models import Event

# claude-sonnet-5 is $3.00 / $15.00 per Mtok in src/aprice/prices/anthropic.yaml.
_SONNET_INPUT_RATE = 3.00
_SONNET_OUTPUT_RATE = 15.00


def event(**overrides):
    base = dict(
        schema_version=1,
        timestamp="2026-08-20T12:00:00Z",
        project_id="proj-a",
        provider="anthropic",
        model="claude-sonnet-5",
        operation="messages.create",
        request_id="req-1",
        input_tokens=100,
        output_tokens=50,
        status="success",
    )
    base.update(overrides)
    return Event(**base)


def sonnet_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * _SONNET_INPUT_RATE
        + output_tokens / 1_000_000 * _SONNET_OUTPUT_RATE
    )


# -- grouping and sum consistency --


def test_no_events_produces_no_groups_and_zero_totals():
    result = aggregate_usage([])
    assert result.groups == []
    assert result.total_request_count == 0
    assert result.total_input_tokens == 0
    assert result.total_output_tokens == 0
    assert result.total_standard_cost_usd == 0.0
    assert result.unpriced_request_count == 0


def test_a_single_group_sums_its_events():
    events = [
        event(request_id="req-1", input_tokens=100, output_tokens=50),
        event(request_id="req-2", input_tokens=200, output_tokens=80),
    ]
    result = aggregate_usage(events)

    [group] = result.groups
    assert group.request_count == 2
    assert group.input_tokens == 300
    assert group.output_tokens == 130
    assert group.standard_cost_usd == sonnet_cost(300, 130)


def test_group_totals_sum_to_the_overall_total():
    events = [
        event(request_id="req-1", project_id="proj-a", input_tokens=100, output_tokens=50),
        event(request_id="req-2", project_id="proj-b", input_tokens=200, output_tokens=80),
        event(
            request_id="req-3",
            project_id="proj-b",
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=5,
        ),
    ]
    result = aggregate_usage(events)

    assert len(result.groups) == 3
    assert sum(g.request_count for g in result.groups) == result.total_request_count
    assert sum(g.input_tokens for g in result.groups) == result.total_input_tokens
    assert sum(g.output_tokens for g in result.groups) == result.total_output_tokens
    assert (
        sum(g.standard_cost_usd for g in result.groups if g.standard_cost_usd is not None)
        == result.total_standard_cost_usd
    )


def test_events_are_grouped_by_project_provider_model_and_operation():
    events = [
        event(request_id="req-1", project_id="proj-a"),
        event(request_id="req-2", project_id="proj-b"),
        event(request_id="req-3", project_id="proj-a", model="claude-opus-5"),
        event(request_id="req-4", project_id="proj-a", operation="messages.stream"),
    ]
    result = aggregate_usage(events)

    keys = {(g.key.project_id, g.key.model, g.key.operation) for g in result.groups}
    assert keys == {
        ("proj-a", "claude-sonnet-5", "messages.create"),
        ("proj-b", "claude-sonnet-5", "messages.create"),
        ("proj-a", "claude-opus-5", "messages.create"),
        ("proj-a", "claude-sonnet-5", "messages.stream"),
    }
    assert all(g.request_count == 1 for g in result.groups)


# -- pricing --


def test_a_priced_model_computes_standard_cost_from_the_verified_rate():
    result = aggregate_usage([event(input_tokens=1_000_000, output_tokens=1_000_000)])
    [group] = result.groups
    assert group.standard_cost_usd == _SONNET_INPUT_RATE + _SONNET_OUTPUT_RATE
    assert result.total_standard_cost_usd == _SONNET_INPUT_RATE + _SONNET_OUTPUT_RATE
    assert result.unpriced_request_count == 0


def test_an_unregistered_model_is_reported_as_usage_with_no_guessed_cost():
    result = aggregate_usage([event(model="some-unreleased-model", input_tokens=500)])
    [group] = result.groups
    assert group.input_tokens == 500
    assert group.standard_cost_usd is None
    assert result.total_standard_cost_usd == 0.0
    assert result.unpriced_request_count == 1


def test_unpriced_usage_is_excluded_from_the_cost_total_but_not_from_token_totals():
    events = [
        event(request_id="req-1", input_tokens=100, output_tokens=50),
        event(request_id="req-2", model="unknown-model", input_tokens=999, output_tokens=999),
    ]
    result = aggregate_usage(events)

    assert result.total_standard_cost_usd == sonnet_cost(100, 50)
    assert result.total_input_tokens == 1099
    assert result.unpriced_request_count == 1


def test_a_non_token_priced_entry_is_treated_as_unpriced(monkeypatch):
    from aprice.models import Price

    import aprice_advisor.aggregate as aggregate_module

    monkeypatch.setattr(
        aggregate_module.pricing,
        "lookup",
        lambda provider, model: Price(
            provider=provider,
            model=model,
            input_per_mtok=1.0,
            output_per_mtok=1.0,
            unit="image",
        ),
    )

    result = aggregate_usage([event()])
    [group] = result.groups
    assert group.standard_cost_usd is None
    assert result.unpriced_request_count == 1


# -- no fabricated projects --


def test_a_project_with_no_logged_usage_never_appears():
    result = aggregate_usage([event(project_id="proj-a")])
    assert {g.key.project_id for g in result.groups} == {"proj-a"}
