from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _aprice_command() -> Path:
    """Use the installed console entry point, not an in-process CLI call."""
    executable = "aprice.exe" if os.name == "nt" else "aprice"
    command = Path(sys.executable).with_name(executable)
    assert command.exists(), f"installed aprice command not found: {command}"
    return command


def _run_aprice(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_aprice_command()), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_installed_scan_connects_detection_pricing_rules_and_json(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        """
client.messages.create(model="claude-sonnet-5", max_tokens=128, messages=[])

for item in range(2):
    oai.responses.create(model="gpt-4o", max_output_tokens=256, input=[])

gemini.generate_content(model="gemini-2.5-flash", max_tokens=64)
client.messages.create(model=selected_model, max_tokens=32, messages=[])
""",
        encoding="utf-8",
    )

    scan = _run_aprice(
        "scan",
        str(source),
        "--input-tokens",
        "1000",
        "--format",
        "json",
    )

    assert scan.returncode == 0, scan.stderr
    payload = json.loads(scan.stdout)
    assert {entry["provider"] for entry in payload["estimates"]} == {
        "anthropic",
        "google",
        "openai",
    }
    assert len(payload["unpriced"]) == 1
    assert payload["unpriced"][0]["model"] is None
    assert any(finding["rule"] == "call-in-loop" for finding in payload["findings"])
    assert payload["total_cost_usd"]["low"] < payload["total_cost_usd"]["high"]

    gated = _run_aprice("scan", str(source), "--fail-on-warning")
    assert gated.returncode == 1
    assert "call-in-loop" in gated.stdout


def test_installed_scan_reports_parse_failure_and_keeps_valid_results(tmp_path: Path) -> None:
    (tmp_path / "valid.py").write_text(
        'model.generate_content(model="gemini-2.5-flash", max_tokens=64)\n',
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    result = _run_aprice("scan", str(tmp_path), "--format", "json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert [entry["provider"] for entry in payload["estimates"]] == ["google"]
    assert "Could not parse Python source" in result.stderr
    assert "broken.py" in result.stderr


def test_installed_scan_distinguishes_no_calls_from_analysis_failure(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("value = 1\n", encoding="utf-8")

    result = _run_aprice("scan", str(clean))

    assert result.returncode == 0
    assert result.stdout.strip() == "APrIce: no paid API calls found."
    assert result.stderr == ""
    # The report must survive the legacy Windows consoles documented by the
    # project; this fixture path and every user-facing message are ASCII.
    assert result.stdout.isascii()


def test_installed_diff_reports_changes_without_touching_the_repo(tmp_path: Path) -> None:
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)

    app = tmp_path / "app.py"
    app.write_text(
        'client.messages.create(model="claude-sonnet-5", max_tokens=100, messages=[])\n',
        encoding="utf-8",
    )
    _git(["add", "app.py"], tmp_path)
    _git(["commit", "-q", "-m", "base"], tmp_path)
    _git(["branch", "base"], tmp_path)

    app.write_text(
        """
client.messages.create(model="claude-sonnet-5", max_tokens=400, messages=[])
for item in items:
    oai.responses.create(model="gpt-4o", max_output_tokens=200, input=[])
""",
        encoding="utf-8",
    )
    _git(["add", "app.py"], tmp_path)
    _git(["commit", "-q", "-m", "head"], tmp_path)

    result = _run_aprice(
        "diff",
        "--base",
        "base",
        "--head",
        "HEAD",
        "--input-tokens",
        "1000",
        "--format",
        "json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert {entry["status"] for entry in payload["entries"]} == {"added", "changed"}
    assert set(payload["total_delta_usd"]) == {"low", "high"}

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert status.stdout == ""

    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert worktrees.stdout.count("worktree ") == 1
