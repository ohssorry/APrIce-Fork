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
        findings.append(
            Finding(
                call=call,
                rule="call-in-loop",
                severity="warn",
                message=(
                    "API call inside a loop: cost scales with the number of "
                    "iterations, which this tool cannot see."
                ),
            )
        )
    elif call.loop_depth >= 2:
        findings.append(
            Finding(
                call=call,
                rule="call-in-nested-loop",
                severity="warn",
                message=(
                    f"API call nested {call.loop_depth} loops deep: cost scales multiplicatively."
                ),
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
