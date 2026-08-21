"""Command-line entry point: ``aprice-advisor analyze <events.jsonl>``."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta
from pathlib import Path

from .aggregate import aggregate_usage
from .cache import find_cache_misses
from .duplicates import DEFAULT_DUPLICATE_WINDOW, find_potential_duplicates
from .loader import load_jsonl
from .recommendations import CallsiteMapError, build_recommendations, load_callsite_map
from .report import AnalysisResult, render_json, render_markdown
from .retry import find_retry_usage

_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
_DURATION_PATTERN = re.compile(r"(\d+)([smh])")


def _parse_duration(value: str) -> timedelta:
    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"invalid duration {value!r}, expected e.g. '30s', '5m', '1h'"
        )
    amount, unit = match.groups()
    seconds = int(amount) * _DURATION_UNIT_SECONDS[unit]
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than zero")
    return timedelta(seconds=seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aprice-advisor",
        description="Analyze real API usage logs for waste static analysis can't see.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_cmd = sub.add_parser("analyze", help="Analyze a JSONL usage log.")
    analyze_cmd.add_argument("path", type=Path, help="JSONL usage log to analyze.")
    analyze_cmd.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown).",
    )
    analyze_cmd.add_argument(
        "--duplicate-window",
        type=_parse_duration,
        default=DEFAULT_DUPLICATE_WINDOW,
        metavar="DURATION",
        help="Observation window for duplicate detection, e.g. '5m' (default: 5m).",
    )
    analyze_cmd.add_argument(
        "--callsites",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional JSON file mapping callsite_id to {file, line}.",
    )
    return parser


def _run_analyze(args: argparse.Namespace) -> int:
    if not args.path.exists():
        print(f"aprice-advisor: no such file: {args.path}", file=sys.stderr)
        return 2

    callsites: dict = {}
    if args.callsites is not None:
        try:
            callsites = load_callsite_map(args.callsites)
        except CallsiteMapError as exc:
            print(f"aprice-advisor: {exc}", file=sys.stderr)
            return 2

    load_result = load_jsonl(args.path)
    events = load_result.events

    result = AnalysisResult(
        input_file=str(args.path),
        duplicate_window_seconds=args.duplicate_window.total_seconds(),
        load_errors=load_result.errors,
        aggregate=aggregate_usage(events),
        retry=find_retry_usage(events),
        duplicates=find_potential_duplicates(events, duplicate_window=args.duplicate_window),
        cache_misses=find_cache_misses(events),
        recommendations=build_recommendations(
            events, callsites=callsites, duplicate_window=args.duplicate_window
        ),
    )

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page (cp949, cp1252, ...) that
    # cannot encode much of what a report may contain -- file paths included.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    return _run_analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())
