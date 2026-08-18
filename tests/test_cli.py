import json
import subprocess
from pathlib import Path

from aprice import cli

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app.py"

BASE_SOURCE = (
    "import anthropic\n"
    "client = anthropic.Anthropic()\n"
    "\n"
    "def summarize(doc):\n"
    "    return client.messages.create(model='claude-sonnet-5', max_tokens=1000, messages=[])\n"
    "\n"
    "def old_call(doc):\n"
    "    return client.messages.create(model='claude-haiku-4-5', max_tokens=200, messages=[])\n"
)

HEAD_SOURCE = (
    "import anthropic\n"
    "client = anthropic.Anthropic()\n"
    "\n"
    "def summarize(doc):\n"
    "    return client.messages.create(model='claude-sonnet-5', max_tokens=4000, messages=[])\n"
    "\n"
    "def new_call(doc):\n"
    "    for x in range(3):\n"
    "        client.messages.create(model='claude-opus-5', max_tokens=2000, messages=[])\n"
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _diff_repo(tmp_path: Path) -> Path:
    """A repo with two commits: a 'base' branch, and HEAD with an added,
    a removed, and a changed call relative to it."""
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)

    (tmp_path / "app.py").write_text(BASE_SOURCE)
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "base"], tmp_path)
    _git(["branch", "base"], tmp_path)

    (tmp_path / "app.py").write_text(HEAD_SOURCE)
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "head"], tmp_path)

    return tmp_path


def test_format_choices_include_text_markdown_json():
    parser = cli.build_parser()
    command = next(a for a in parser._actions if a.dest == "command")
    scan_parser = command.choices["scan"]
    format_action = next(a for a in scan_parser._actions if a.dest == "format")
    assert set(format_action.choices) == {"text", "markdown", "json"}


def test_scan_returns_calls_for_fixture():
    result = cli.scan(FIXTURE, input_tokens=1000)
    assert result.calls
    assert result.estimates or result.unpriced


def test_main_errors_on_missing_path(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.py"
    code = cli.main(["scan", str(missing)])
    assert code == 2
    assert "no such file or directory" in capsys.readouterr().err


def test_main_text_format_runs_clean(capsys):
    code = cli.main(["scan", str(FIXTURE)])
    assert code == 0
    assert "APrIce" in capsys.readouterr().out


def test_main_markdown_format_runs_clean(capsys):
    code = cli.main(["scan", str(FIXTURE), "--format", "markdown"])
    assert code == 0
    assert "## APrIce cost report" in capsys.readouterr().out


def test_main_json_format_is_parseable(capsys):
    code = cli.main(["scan", str(FIXTURE), "--format", "json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1


def test_fail_on_warning_exits_nonzero_when_warning_present(capsys):
    # The fixture has a call inside a loop, which raises call-in-loop (warn).
    code = cli.main(["scan", str(FIXTURE), "--fail-on-warning"])
    capsys.readouterr()
    assert code == 1


def test_fail_on_warning_is_zero_without_warnings(tmp_path, capsys):
    clean = tmp_path / "clean.py"
    clean.write_text(
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
        "client.messages.create(model='claude-sonnet-5', max_tokens=100, messages=[])\n"
    )
    code = cli.main(["scan", str(clean), "--fail-on-warning"])
    capsys.readouterr()
    assert code == 0


# -- diff --


def test_diff_detects_added_removed_and_changed_calls(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0

    by_status = {e["status"]: e for e in payload["entries"]}
    assert by_status["added"]["model"] == "claude-opus-5"
    assert by_status["removed"]["model"] == "claude-haiku-4-5"
    assert by_status["changed"]["model"] == "claude-sonnet-5"


def test_diff_reports_low_and_high_delta_separately_not_collapsed(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    delta = payload["total_delta_usd"]
    assert set(delta) == {"low", "high"}
    assert delta["low"] != delta["high"]


def test_diff_does_not_modify_the_working_tree(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    (tmp_path / "app.py").write_text(HEAD_SOURCE + "\n# uncommitted local edit\n")
    monkeypatch.chdir(tmp_path)

    before = (tmp_path / "app.py").read_text()
    cli.main(["diff", "--base", "base", "--head", "HEAD"])
    capsys.readouterr()
    after = (tmp_path / "app.py").read_text()

    assert before == after
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    )
    assert "app.py" in status.stdout  # the uncommitted edit is still pending, not lost or committed
    worktrees = subprocess.run(
        ["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True
    )
    assert worktrees.stdout.count("\n") == 1  # only the main worktree remains


def test_diff_text_format_shows_no_changes_between_identical_refs(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "HEAD", "--head", "HEAD"])
    out = capsys.readouterr().out
    assert code == 0
    assert "No API call changes detected" in out


def test_diff_unknown_ref_errors_with_clear_message_and_exit_code(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "does-not-exist", "--head", "HEAD"])
    err = capsys.readouterr().err
    assert code == 2
    assert "does-not-exist" in err


def test_diff_outside_a_git_repo_errors_with_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["diff", "--base", "main", "--head", "HEAD"])
    err = capsys.readouterr().err
    assert code == 2
    assert "git repository" in err
