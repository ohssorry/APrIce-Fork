"""Compares per-request API cost between two git refs.

Scans each ref from a throwaway ``git worktree`` rather than checking it out
in place, so the user's actual working tree (staged and unstaged changes
included) is never touched.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import detector, pricing, rules
from .models import ApiCall, ScanResult

# A call is matched across refs by (file, provider, model), not by line
# number: inserting a line above a call shifts every line below it, which
# would make an unrelated call look "removed" and "added". This key is not
# perfect either -- a call whose *model* changes at the same call site shows
# up as a removal plus an addition rather than a single "changed" entry --
# but it is the next-cheapest stable option. See docs/tasks/C.md C-003.
MatchKey = tuple[str, str, str | None]


class GitRefError(Exception):
    """The path isn't inside a git repo, or a ref can't be resolved."""


@dataclass(frozen=True)
class CallDiff:
    key: MatchKey
    status: str  # "added" | "removed" | "changed"
    base: ApiCall | None
    head: ApiCall | None
    base_cost: tuple[float, float] | None
    head_cost: tuple[float, float] | None


@dataclass(frozen=True)
class DiffResult:
    base_ref: str
    head_ref: str
    entries: list[CallDiff]
    base_low: float
    base_high: float
    head_low: float
    head_high: float

    @property
    def delta_low(self) -> float:
        return self.head_low - self.base_low

    @property
    def delta_high(self) -> float:
        return self.head_high - self.base_high


def _match_key(call: ApiCall) -> MatchKey:
    return (call.file.replace("\\", "/"), call.provider, call.model)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # Git for Windows emits UTF-8 paths even when Python inherits a legacy
    # console encoding such as cp949. Decode explicitly so non-ASCII repo
    # paths cannot make an otherwise successful command lose stdout.
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _repo_root() -> Path:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=Path.cwd())
    if result.returncode != 0:
        raise GitRefError(f"not a git repository: {Path.cwd()}")
    return Path(result.stdout.strip())


def _scan_ref(repo_root: Path, ref: str, rel_path: Path, input_tokens: int) -> ScanResult:
    verify = _run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_root)
    if verify.returncode != 0:
        raise GitRefError(f"unknown git ref: {ref}")

    tmp_parent = Path(tempfile.mkdtemp(prefix="aprice-diff-"))
    worktree = tmp_parent / "wt"
    try:
        add = _run_git(["worktree", "add", "--detach", str(worktree), ref], cwd=repo_root)
        if add.returncode != 0:
            raise GitRefError(f"failed to check out {ref}: {add.stderr.strip()}")

        target = worktree / rel_path
        if not target.exists():
            return ScanResult()

        calls = detector.scan_path(target)
        # Rewrite paths relative to the worktree root (not the throwaway temp
        # dir), so the same call gets the same match key on both sides.
        calls = [
            dataclasses.replace(call, file=str(Path(call.file).relative_to(worktree)))
            for call in calls
        ]

        # A price-table change (B updating prices/*.yaml) is a normal part of
        # this repo's history, and it changes cost per request just as much
        # as a code change does. pricing.load_prices() is cached and reads
        # from the installed package's own prices/ directory by default, so
        # without this swap both scans would silently reuse whatever prices
        # happen to be on disk right now instead of each ref's own prices.
        with _prices_from(worktree):
            result = ScanResult(calls=calls)
            for call in calls:
                estimate = pricing.estimate(call, input_tokens=input_tokens)
                if estimate is None:
                    result.unpriced.append(call)
                else:
                    result.estimates.append(estimate)
            result.findings = rules.check_all(calls)
            return result
    finally:
        _run_git(["worktree", "remove", "--force", str(worktree)], cwd=repo_root)
        shutil.rmtree(tmp_parent, ignore_errors=True)


@contextmanager
def _prices_from(worktree: Path) -> Iterator[None]:
    prices_dir = worktree / "src" / "aprice" / "prices"
    original = pricing.PRICES_DIR
    if prices_dir.is_dir():
        pricing.PRICES_DIR = prices_dir
    pricing.load_prices.cache_clear()
    try:
        yield
    finally:
        pricing.PRICES_DIR = original
        pricing.load_prices.cache_clear()


_Cost = tuple[float, float] | None
_KeyIndex = dict[MatchKey, list[tuple[ApiCall, _Cost]]]


def _index(result: ScanResult) -> _KeyIndex:
    cost_by_call: dict[ApiCall, tuple[float, float]]
    cost_by_call = {e.call: (e.low_usd, e.high_usd) for e in result.estimates}
    index: _KeyIndex = {}
    for call in result.calls:
        index.setdefault(_match_key(call), []).append((call, cost_by_call.get(call)))
    return index


def compare(path: Path, base: str, head: str, input_tokens: int) -> DiffResult:
    """Scan ``base`` and ``head`` and report how per-request cost changed.

    ``path`` is resolved relative to the repository root so it means the same
    subtree on both refs, even though each is scanned from its own worktree.
    """
    repo_root = _repo_root()
    try:
        rel_path = path.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise GitRefError(f"{path} is not inside the git repository at {repo_root}") from exc

    base_result = _scan_ref(repo_root, base, rel_path, input_tokens)
    head_result = _scan_ref(repo_root, head, rel_path, input_tokens)

    base_by_key = _index(base_result)
    head_by_key = _index(head_result)

    entries: list[CallDiff] = []
    all_keys = sorted(
        set(base_by_key) | set(head_by_key),
        key=lambda k: (k[0], k[1], k[2] or ""),
    )
    for key in all_keys:
        base_items = base_by_key.get(key, [])
        head_items = head_by_key.get(key, [])
        # Same key can appear more than once in one file (two calls to the
        # same model). Pair them up positionally rather than dropping
        # duplicates, and treat any leftover on either side as added/removed.
        for (b_call, b_cost), (h_call, h_cost) in zip(base_items, head_items, strict=False):
            if (
                b_call.max_tokens != h_call.max_tokens
                or b_call.loop_depth != h_call.loop_depth
                or b_call.loop_bounds != h_call.loop_bounds
                or b_cost != h_cost
            ):
                entries.append(CallDiff(key, "changed", b_call, h_call, b_cost, h_cost))
        for b_call, b_cost in base_items[len(head_items) :]:
            entries.append(CallDiff(key, "removed", b_call, None, b_cost, None))
        for h_call, h_cost in head_items[len(base_items) :]:
            entries.append(CallDiff(key, "added", None, h_call, None, h_cost))

    return DiffResult(
        base_ref=base,
        head_ref=head,
        entries=entries,
        base_low=base_result.low_usd,
        base_high=base_result.high_usd,
        head_low=head_result.low_usd,
        head_high=head_result.high_usd,
    )
