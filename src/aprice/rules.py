"""Structural cost-risk rules.

These fire without knowing the price of anything. A call inside a loop is a
cost risk regardless of which model it uses, and it is exactly the shape of
mistake that produces a surprise invoice.
"""

from __future__ import annotations

from .models import ApiCall, Finding

# Above this, `max_tokens` is usually a copy-pasted ceiling rather than a
# considered bound -- and it is what the upper bound of every estimate uses.
LARGE_MAX_TOKENS = 32_000


def check(call: ApiCall) -> list[Finding]:
    findings: list[Finding] = []

    if call.loop_depth == 1:
        bound = call.loop_bounds[0] if len(call.loop_bounds) == 1 else None
        if bound is None:
            message = (
                "API call inside a loop: cost scales with the number of "
                "iterations, which this tool cannot see."
            )
        else:
            message = (
                f"API call inside a loop with a static upper bound of {bound} "
                "iterations: cost can scale up to that bound."
            )
        findings.append(
            Finding(
                call=call,
                rule="call-in-loop",
                severity="warn",
                message=message,
            )
        )
    elif call.loop_depth >= 2:
        if len(call.loop_bounds) == call.loop_depth and any(
            bound is not None for bound in call.loop_bounds
        ):
            bounds = ", ".join(
                "unknown" if bound is None else str(bound) for bound in call.loop_bounds
            )
            message = (
                f"API call nested {call.loop_depth} loops deep with iteration bounds "
                f"[{bounds}]: cost can scale multiplicatively."
            )
        else:
            message = f"API call nested {call.loop_depth} loops deep: cost scales multiplicatively."
        findings.append(
            Finding(
                call=call,
                rule="call-in-nested-loop",
                severity="warn",
                message=message,
            )
        )

    if call.max_tokens is None:
        findings.append(
            Finding(
                call=call,
                rule="no-max-tokens",
                severity="info",
                message="No literal max_tokens: the output cost has no visible ceiling.",
            )
        )
    elif call.max_tokens >= LARGE_MAX_TOKENS:
        findings.append(
            Finding(
                call=call,
                rule="large-max-tokens",
                severity="info",
                message=(
                    f"max_tokens={call.max_tokens:,} sets a high output ceiling. "
                    "Lower it if responses are known to be short."
                ),
            )
        )

    if call.model is None:
        findings.append(
            Finding(
                call=call,
                rule="model-not-literal",
                severity="info",
                message="Model name is not a literal, so this call cannot be priced.",
            )
        )

    return findings


def check_all(calls: list[ApiCall]) -> list[Finding]:
    return [finding for call in calls for finding in check(call)]
