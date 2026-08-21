"""Rendering a ScanResult as terminal text, a PR comment, or JSON."""

from __future__ import annotations

import json

from .diff import CallDiff, DiffResult
from .models import ApiCall, ScanResult

# Bump when the JSON shape changes so consumers (the diff command, CI
# integrations) can detect a format they don't understand instead of
# misparsing it.
JSON_SCHEMA_VERSION = 1


def _usd(amount: float) -> str:
    # Per-request costs are routinely fractions of a cent, so two decimals
    # would render most of this report as "$0.00".
    if amount < 1:
        return f"${amount:.5f}"
    return f"${amount:,.2f}"


def _range(low: float, high: float) -> str:
    # ASCII hyphen, not an en-dash: this string reaches Windows consoles whose
    # code page (cp949, cp1252, ...) cannot encode it.
    return f"{_usd(low)} - {_usd(high)}"


def render_terminal(result: ScanResult) -> str:
    lines: list[str] = []

    if not result.calls:
        return "APrIce: no paid API calls found."

    lines.append(f"APrIce: {len(result.calls)} API call(s) found\n")

    if result.estimates:
        lines.append("Cost per request")
        for est in sorted(result.estimates, key=lambda e: -e.high_usd):
            lines.append(
                f"  {est.call.location}  {est.price.model:<24} {_range(est.low_usd, est.high_usd)}"
            )
        lines.append(f"\n  Total per request: {_range(result.low_usd, result.high_usd)}\n")

    if result.unpriced:
        lines.append("Unpriced (model name is not a literal, or not in the price DB)")
        for call in result.unpriced:
            lines.append(f"  {call.location}  {call.provider}/{call.model or '<dynamic>'}")
        lines.append("")

    if result.findings:
        lines.append("Findings")
        for finding in result.findings:
            marker = "!" if finding.severity == "warn" else "-"
            lines.append(f"  {marker} {finding.call.location}  [{finding.rule}] {finding.message}")

    return "\n".join(lines)


def render_markdown(result: ScanResult, title: str = "APrIce cost report") -> str:
    """Render a PR-comment-sized Markdown summary."""
    lines = [f"## {title}", ""]

    if not result.calls:
        lines.append("No paid API calls found in the scanned files.")
        return "\n".join(lines)

    lines.append(
        f"**Estimated cost per request: {_range(result.low_usd, result.high_usd)}** "
        f"across {len(result.calls)} call site(s)."
    )
    lines.append("")

    if result.estimates:
        lines.append("| Location | Model | Cost per request |")
        lines.append("|---|---|---|")
        for est in sorted(result.estimates, key=lambda e: -e.high_usd):
            lines.append(
                f"| `{est.call.location}` | `{est.price.model}` | "
                f"{_range(est.low_usd, est.high_usd)} |"
            )
        lines.append("")

    warnings = [f for f in result.findings if f.severity == "warn"]
    if warnings:
        lines.append("### Cost risks")
        for finding in warnings:
            lines.append(f"- **`{finding.call.location}`** — {finding.message}")
        lines.append("")

    if result.unpriced:
        lines.append(
            f"<sub>{len(result.unpriced)} call site(s) could not be priced "
            "(model name is not a literal in the source).</sub>"
        )

    lines.append("")
    lines.append(
        "<sub>Ranges, not point estimates: the upper bound assumes `max_tokens` is "
        "fully used. Call volume is not inferred from source. "
        "See `docs/methodology.md`.</sub>"
    )
    return "\n".join(lines)


def _call_dict(call: ApiCall) -> dict:
    return {
        "location": call.location,
        "file": call.file.replace("\\", "/"),
        "line": call.line,
        "provider": call.provider,
        "model": call.model,
        "max_tokens": call.max_tokens,
        "loop_depth": call.loop_depth,
        # Outermost to innermost, matching ApiCall.loop_bounds. An element is
        # the literal iteration cap (range(5) -> 5) or null when the bound
        # isn't visible in source (range(n), a general iterable, while).
        "loop_bounds": list(call.loop_bounds),
    }


def render_json(result: ScanResult) -> str:
    """Render a ScanResult as machine-readable JSON.

    Cost is always a {low, high} pair, never collapsed to one number -- the
    whole point of the range is that call volume isn't known. See
    docs/methodology.md.
    """
    payload = {
        "schema_version": JSON_SCHEMA_VERSION,
        "estimates": [
            {
                **_call_dict(est.call),
                "cost_usd": {"low": est.low_usd, "high": est.high_usd},
            }
            for est in result.estimates
        ],
        "total_cost_usd": {"low": result.low_usd, "high": result.high_usd},
        "unpriced": [_call_dict(call) for call in result.unpriced],
        "findings": [
            {
                "location": finding.call.location,
                "rule": finding.rule,
                "severity": finding.severity,
                "message": finding.message,
            }
            for finding in result.findings
        ],
    }
    return json.dumps(payload, indent=2)


_STATUS_MARKER = {"added": "+", "removed": "-", "changed": "~"}


def _signed_usd(amount: float) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{_usd(abs(amount))}"


def _entry_call(entry: CallDiff) -> ApiCall:
    call = entry.head if entry.head is not None else entry.base
    assert call is not None
    return call


def _entry_cost_text(entry: CallDiff) -> str:
    # base_cost and head_cost are both None for an added/removed call that
    # was never priced (dynamic model, unknown price) -- report it as
    # "unpriced" rather than dropping it, so a volume change in unpriced
    # calls is still visible.
    if entry.base_cost is None and entry.head_cost is None:
        return "unpriced"
    b_low, b_high = entry.base_cost or (0.0, 0.0)
    h_low, h_high = entry.head_cost or (0.0, 0.0)
    return f"low {_signed_usd(h_low - b_low)}  high {_signed_usd(h_high - b_high)}"


def render_diff_text(diff: DiffResult) -> str:
    lines = [f"APrIce: cost delta {diff.base_ref} -> {diff.head_ref}\n"]

    if not diff.entries:
        lines.append("No API call changes detected.")
    else:
        for entry in diff.entries:
            call = _entry_call(entry)
            model = call.model or "<dynamic>"
            lines.append(
                f"  {_STATUS_MARKER[entry.status]} {call.location}  "
                f"{call.provider}/{model:<24} {_entry_cost_text(entry)}  [{entry.status}]"
            )

    lines.append(
        f"\n  Net change per request: low {_signed_usd(diff.delta_low)}  "
        f"high {_signed_usd(diff.delta_high)}"
    )
    return "\n".join(lines)


def render_diff_markdown(diff: DiffResult) -> str:
    lines = [f"## APrIce cost delta: `{diff.base_ref}` -> `{diff.head_ref}`", ""]

    if not diff.entries:
        lines.append("No API call changes detected.")
    else:
        lines.append("| | Location | Model | Cost change per request |")
        lines.append("|---|---|---|---|")
        for entry in diff.entries:
            call = _entry_call(entry)
            lines.append(
                f"| {_STATUS_MARKER[entry.status]} | `{call.location}` | "
                f"`{call.provider}/{call.model or '<dynamic>'}` | {_entry_cost_text(entry)} |"
            )
        lines.append("")

    lines.append(
        f"**Net change per request: low {_signed_usd(diff.delta_low)}, "
        f"high {_signed_usd(diff.delta_high)}**"
    )
    lines.append("")
    lines.append(
        "<sub>Per-request cost only -- call volume is not inferred from source. "
        "See `docs/methodology.md`.</sub>"
    )
    return "\n".join(lines)


def render_diff_json(diff: DiffResult) -> str:
    def cost_obj(cost: tuple[float, float] | None) -> dict | None:
        return {"low": cost[0], "high": cost[1]} if cost is not None else None

    payload = {
        "schema_version": JSON_SCHEMA_VERSION,
        "base_ref": diff.base_ref,
        "head_ref": diff.head_ref,
        "entries": [
            {
                "status": entry.status,
                **_call_dict(_entry_call(entry)),
                "base_cost_usd": cost_obj(entry.base_cost),
                "head_cost_usd": cost_obj(entry.head_cost),
            }
            for entry in diff.entries
        ],
        "total_delta_usd": {"low": diff.delta_low, "high": diff.delta_high},
    }
    return json.dumps(payload, indent=2)
