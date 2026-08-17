"""Command-line entry point: ``aprice scan <path>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import detector, pricing, report, rules
from .models import ScanResult


def scan(root: Path, input_tokens: int) -> ScanResult:
    result = ScanResult(calls=detector.scan_path(root))
    for call in result.calls:
        estimate = pricing.estimate(call, input_tokens=input_tokens)
        if estimate is None:
            result.unpriced.append(call)
        else:
            result.estimates.append(estimate)
    result.findings = rules.check_all(result.calls)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aprice",
        description="Estimate API cost from source code.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_cmd = sub.add_parser("scan", help="Scan a file or directory.")
    scan_cmd.add_argument("path", type=Path, help="File or directory to scan.")
    scan_cmd.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format (default: text).",
    )
    scan_cmd.add_argument(
        "--input-tokens",
        type=int,
        default=pricing.DEFAULT_INPUT_TOKENS,
        help=(
            "Assumed input tokens per call. Input length cannot be derived "
            f"from source (default: {pricing.DEFAULT_INPUT_TOKENS})."
        ),
    )
    scan_cmd.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit 1 if any cost risk is found. Use this to gate CI.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page (cp949, cp1252, ...) that
    # cannot encode much of what a report may contain -- file paths included.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    if not args.path.exists():
        print(f"aprice: no such file or directory: {args.path}", file=sys.stderr)
        return 2

    result = scan(args.path, input_tokens=args.input_tokens)

    if args.format == "markdown":
        print(report.render_markdown(result))
    else:
        print(report.render_terminal(result))

    if args.fail_on_warning and any(f.severity == "warn" for f in result.findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
