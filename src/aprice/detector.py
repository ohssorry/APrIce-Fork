"""AST-based detection of paid API call sites in Python source.

We match on the *attribute path* of the callee rather than on the imported
symbol, because every major SDK is used through a client instance whose name
varies by codebase::

    client.messages.create(...)          -> anthropic
    self.oai.chat.completions.create(..) -> openai
    model.generate_content(...)          -> google

Matching the tail of the dotted path keeps the detector independent of how the
client was named or constructed. Regex over raw text would also "work" here,
but it cannot tell a call inside a loop from one in a comment, and it cannot
read keyword arguments -- both of which we need.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

from .models import ApiCall


class ParseFailureWarning(UserWarning):
    """A Python file could not be parsed and was excluded from analysis."""


# Suffix of the callee's dotted path -> provider key in the price database.
CALL_SIGNATURES: dict[tuple[str, ...], str] = {
    ("messages", "create"): "anthropic",
    ("messages", "stream"): "anthropic",
    ("chat", "completions", "create"): "openai",
    ("responses", "create"): "openai",
    ("generate_content",): "google",
    ("models", "generate_content"): "google",
}


def _dotted_path(node: ast.AST) -> tuple[str, ...]:
    """Flatten ``a.b.c`` (or ``a().b.c``) into ``("a", "b", "c")``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.extend(reversed(_dotted_path(node.func)))
    return tuple(reversed(parts))


def _match_provider(path: tuple[str, ...]) -> str | None:
    for signature, provider in CALL_SIGNATURES.items():
        if len(path) >= len(signature) and path[-len(signature) :] == signature:
            return provider
    return None


def _literal(node: ast.AST | None) -> object | None:
    """Return the value of a literal node, or None if it is not a literal."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls: list[ApiCall] = []
        self._loop_depth = 0

    def _visit_loop(self, node: ast.AST) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop
    visit_ListComp = _visit_loop
    visit_SetComp = _visit_loop
    visit_DictComp = _visit_loop
    visit_GeneratorExp = _visit_loop

    def visit_Call(self, node: ast.Call) -> None:
        provider = _match_provider(_dotted_path(node.func))
        if provider is not None:
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            model = _literal(kwargs.get("model"))
            max_tokens = _literal(kwargs.get("max_tokens"))
            self.calls.append(
                ApiCall(
                    provider=provider,
                    file=self.filename,
                    line=node.lineno,
                    model=model if isinstance(model, str) else None,
                    max_tokens=max_tokens if isinstance(max_tokens, int) else None,
                    loop_depth=self._loop_depth,
                )
            )
        self.generic_visit(node)


def scan_source(source: str, filename: str) -> list[ApiCall]:
    """Detect API calls in one Python source string.

    A file we cannot parse is skipped rather than fatal: a scan across a repo
    should not die on one syntactically broken file.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        line = error.lineno or 1
        column = error.offset if error.offset is not None else "unknown"
        warnings.warn_explicit(
            f"Could not parse Python source at column {column}: {error.msg}",
            ParseFailureWarning,
            filename=filename,
            lineno=line,
        )
        return []
    visitor = _CallVisitor(filename)
    visitor.visit(tree)
    return visitor.calls


def scan_path(root: Path) -> list[ApiCall]:
    """Detect API calls in a file, or recursively in a directory."""
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    calls: list[ApiCall] = []
    for path in files:
        if any(part in {".venv", "venv", "node_modules", ".git"} for part in path.parts):
            continue
        calls.extend(scan_source(path.read_text(encoding="utf-8"), str(path)))
    return calls
