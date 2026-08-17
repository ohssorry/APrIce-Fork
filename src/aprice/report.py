"""Rendering a ScanResult as terminal text or as a PR comment."""

from __future__ import annotations

from .models import ScanResult


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
