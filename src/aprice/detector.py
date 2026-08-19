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
    ("messages", "batches", "create"): "anthropic",
    ("chat", "completions", "create"): "openai",
    ("responses", "create"): "openai",
    ("completions", "create"): "openai",
    ("embeddings", "create"): "openai",
    ("images", "generate"): "openai",
    ("images", "edit"): "openai",
    ("images", "create_variation"): "openai",
    ("audio", "speech", "create"): "openai",
    ("audio", "transcriptions", "create"): "openai",
    ("audio", "translations", "create"): "openai",
    ("generate_content",): "google",
    ("models", "generate_content"): "google",
    ("models", "embed_content"): "google",
}

# Prefer the most specific suffix without sorting the same static table for
# every Call node visited in a source tree.
CALL_SIGNATURES_LONGEST_FIRST = tuple(sorted(CALL_SIGNATURES, key=len, reverse=True))

# SDKs name the same output ceiling differently. Keep this translation next
# to call detection so pricing and rules can continue to use one normalized
# ApiCall field without depending on provider-specific argument names.
OUTPUT_TOKEN_ARGUMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("chat", "completions", "create"): ("max_completion_tokens", "max_tokens"),
    ("responses", "create"): ("max_output_tokens", "max_tokens"),
}
DEFAULT_OUTPUT_TOKEN_ARGUMENTS = ("max_tokens",)


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


def _match_signature(path: tuple[str, ...]) -> tuple[str, ...] | None:
    for signature in CALL_SIGNATURES_LONGEST_FIRST:
        # A bare local function can share an endpoint name. SDK calls always
        # have a client or model object before a one-part signature.
        if len(signature) == 1 and len(path) == 1:
            continue
        if len(path) >= len(signature) and path[-len(signature) :] == signature:
            return signature
    return None


def _literal(node: ast.AST | None) -> object | None:
    """Return the value of a literal node, or None if it is not a literal."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _output_token_limit(signature: tuple[str, ...], kwargs: dict[str, ast.AST]) -> object | None:
    argument_names = OUTPUT_TOKEN_ARGUMENTS.get(signature, DEFAULT_OUTPUT_TOKEN_ARGUMENTS)
    for name in argument_names:
        if name in kwargs:
            return _literal(kwargs[name])
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

    def _visit_comprehension(
        self, generators: list[ast.comprehension], result_nodes: tuple[ast.AST, ...]
    ) -> None:
        starting_depth = self._loop_depth
        try:
            for generator in generators:
                # The first iterable is evaluated before its loop starts. Each
                # later iterable is evaluated inside all preceding loops.
                self.visit(generator.iter)
                self._loop_depth += 1
                self.visit(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            for result_node in result_nodes:
                self.visit(result_node)
        finally:
            self._loop_depth = starting_depth

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_Call(self, node: ast.Call) -> None:
        signature = _match_signature(_dotted_path(node.func))
        if signature is not None:
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            model = _literal(kwargs.get("model"))
            max_tokens = _output_token_limit(signature, kwargs)
            self.calls.append(
                ApiCall(
                    provider=CALL_SIGNATURES[signature],
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
