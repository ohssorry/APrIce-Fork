"""Core data structures shared across the detector, pricing, and report layers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiCall:
    """A single API call site found in the source tree.

    ``model`` is None when the model name is not a literal in the source
    (passed through a variable, read from config, ...). We keep the call
    rather than dropping it, so the report can say "found, but unpriced".
    """

    provider: str
    file: str
    line: int
    model: str | None = None
    max_tokens: int | None = None
    loop_depth: int = 0

    @property
    def location(self) -> str:
        # Forward slashes regardless of platform: these strings end up in PR
        # comments that a reader on any OS should be able to click.
        return f"{self.file.replace(chr(92), '/')}:{self.line}"


@dataclass(frozen=True)
class Price:
    """Unit price for one model, in USD per million tokens."""

    provider: str
    model: str
    input_per_mtok: float
    output_per_mtok: float
    unit: str = "token"
    note: str = ""


@dataclass(frozen=True)
class CostEstimate:
    """Per-call cost estimate.

    We deliberately report a range instead of a single number. Output length
    is bounded by ``max_tokens`` but rarely reaches it, and input length is
    only partly knowable from source. See docs/methodology.md.
    """

    call: ApiCall
    price: Price
    low_usd: float
    high_usd: float

    @property
    def mid_usd(self) -> float:
        return (self.low_usd + self.high_usd) / 2


@dataclass(frozen=True)
class Finding:
    """A structural cost risk that does not depend on knowing the price."""

    call: ApiCall
    rule: str
    severity: str  # "warn" | "info"
    message: str


@dataclass
class ScanResult:
    calls: list[ApiCall] = field(default_factory=list)
    estimates: list[CostEstimate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    unpriced: list[ApiCall] = field(default_factory=list)

    @property
    def low_usd(self) -> float:
        return sum(e.low_usd for e in self.estimates)

    @property
    def high_usd(self) -> float:
        return sum(e.high_usd for e in self.estimates)
