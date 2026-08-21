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


def _init_repo(tmp_path: Path) -> None:
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)


def _commit_app(tmp_path: Path, source: str, message: str, *, branch: str | None = None) -> None:
    (tmp_path / "app.py").write_text(source)
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "--allow-empty", "-m", message], tmp_path)
    if branch:
        _git(["branch", branch], tmp_path)


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
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "app.py" in status.stdout  # the uncommitted edit is still pending, not lost or committed
    worktrees = subprocess.run(
        ["git", "worktree", "list"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert worktrees.stdout.count("\n") == 1  # only the main worktree remains


def test_diff_text_format_shows_no_changes_between_identical_refs(tmp_path, monkeypatch, capsys):
    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "HEAD", "--head", "HEAD"])
    out = capsys.readouterr().out
    assert code == 0
    assert "No API call changes detected" in out


def test_diff_handles_non_ascii_repository_path(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "한글-저장소"
    repo.mkdir()
    _diff_repo(repo)
    monkeypatch.chdir(repo)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD"])
    capsys.readouterr()
    assert code == 0


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


def test_diff_detects_a_retry_bound_change_with_no_other_change(tmp_path, monkeypatch, capsys):
    # Same call site, same model, same max_tokens -- only the literal retry
    # count changes. This must still show up, since retry-loop bound is what
    # #24/#31 added the loop_bounds contract for.
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)

    call_site = (
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
        "for attempt in range({n}):\n"
        "    client.messages.create(model='claude-sonnet-5', max_tokens=1000, messages=[])\n"
    )
    (tmp_path / "app.py").write_text(call_site.format(n=2))
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "base"], tmp_path)
    _git(["branch", "base"], tmp_path)

    (tmp_path / "app.py").write_text(call_site.format(n=5))
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "head"], tmp_path)

    monkeypatch.chdir(tmp_path)
    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0

    [entry] = payload["entries"]
    assert entry["status"] == "changed"
    assert entry["loop_bounds"] == [5]


# -- diff: prices can change between refs too --

_PRICE_YAML = """\
provider: anthropic
currency: USD
models:
  - id: test-diff-model
    input_per_mtok: {input_price}
    output_per_mtok: {output_price}
    verified_on: 2026-08-19
"""


def _price_diff_repo(tmp_path: Path, base_price: float, head_price: float) -> Path:
    """Same call site in base and head; only the price table changes --
    the scenario a B price-update PR creates."""
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test"], tmp_path)

    prices_dir = tmp_path / "src" / "aprice" / "prices"
    prices_dir.mkdir(parents=True)
    (tmp_path / "app.py").write_text(
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
        "client.messages.create(model='test-diff-model', max_tokens=1000, messages=[])\n"
    )

    (prices_dir / "anthropic.yaml").write_text(
        _PRICE_YAML.format(input_price=base_price, output_price=base_price * 5)
    )
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "base"], tmp_path)
    _git(["branch", "base"], tmp_path)

    (prices_dir / "anthropic.yaml").write_text(
        _PRICE_YAML.format(input_price=head_price, output_price=head_price * 5)
    )
    _git(["add", "-A"], tmp_path)
    # --allow-empty: when base_price == head_price this commit has no actual
    # diff, but we still need a distinct HEAD to compare against base.
    _git(["commit", "-q", "--allow-empty", "-m", "price update"], tmp_path)

    return tmp_path


def test_diff_detects_a_price_table_change_with_no_code_change(tmp_path, monkeypatch, capsys):
    _price_diff_repo(tmp_path, base_price=3.0, head_price=30.0)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0

    assert payload["entries"], "a price-only change must not be reported as no change"
    [entry] = payload["entries"]
    assert entry["status"] == "changed"
    assert entry["base_cost_usd"]["low"] < entry["head_cost_usd"]["low"]
    assert payload["total_delta_usd"]["low"] > 0
    assert payload["total_delta_usd"]["high"] > 0


def test_diff_shows_no_change_when_price_is_identical_on_both_refs(tmp_path, monkeypatch, capsys):
    _price_diff_repo(tmp_path, base_price=3.0, head_price=3.0)
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["entries"] == []


def test_diff_restores_the_installed_price_directory_afterward(tmp_path, monkeypatch, capsys):
    from aprice import pricing

    _diff_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    original_dir = pricing.PRICES_DIR

    cli.main(["diff", "--base", "base", "--head", "HEAD"])
    capsys.readouterr()

    assert pricing.PRICES_DIR == original_dir
    assert pricing.lookup("anthropic", "claude-sonnet-5") is not None


# -- diff: --fail-on-risk --

_CALL = "client.messages.create(model='claude-sonnet-5', max_tokens=1000, messages=[])\n"


def test_fail_on_risk_blocks_a_new_call_added_inside_a_loop(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path, "import anthropic\nclient = anthropic.Anthropic()\n", "base", branch="base"
    )
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor doc in docs:\n    {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["blocking_risks"][0]["kind"] == "new-loop-call"


def test_fail_on_risk_blocks_loop_depth_increase_on_an_existing_call(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor x in a:\n    {_CALL}",
        "base",
        branch="base",
    )
    _commit_app(
        tmp_path,
        "import anthropic\nclient = anthropic.Anthropic()\n"
        f"for x in a:\n    for y in b:\n        {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["blocking_risks"][0]["kind"] == "loop-depth-increased"


def test_fail_on_risk_blocks_a_retry_bound_becoming_larger(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor attempt in range(2):\n    {_CALL}",
        "base",
        branch="base",
    )
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor attempt in range(5):\n    {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["blocking_risks"][0]["kind"] == "loop-bound-worse"


def test_fail_on_risk_blocks_a_known_retry_bound_becoming_unknown(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor attempt in range(2):\n    {_CALL}",
        "base",
        branch="base",
    )
    _commit_app(
        tmp_path,
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
        "n = get_n()\n"
        f"for attempt in range(n):\n    {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["blocking_risks"][0]["kind"] == "loop-bound-worse"


def test_fail_on_risk_does_not_block_a_loop_being_removed(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor x in a:\n    {_CALL}",
        "base",
        branch="base",
    )
    _commit_app(tmp_path, f"import anthropic\nclient = anthropic.Anthropic()\n{_CALL}", "head")
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk"])
    capsys.readouterr()
    assert code == 0


def test_fail_on_risk_does_not_block_a_retry_bound_shrinking(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor attempt in range(5):\n    {_CALL}",
        "base",
        branch="base",
    )
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor attempt in range(2):\n    {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk"])
    capsys.readouterr()
    assert code == 0


def test_fail_on_risk_does_not_block_cost_or_model_changes_alone(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path,
        "import anthropic\nclient = anthropic.Anthropic()\n"
        "client.messages.create(model='claude-sonnet-5', max_tokens=1000, messages=[])\n",
        "base",
        branch="base",
    )
    _commit_app(
        tmp_path,
        "import anthropic\nclient = anthropic.Anthropic()\n"
        "client.messages.create(model='claude-opus-5', max_tokens=4000, messages=[])\n",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["blocking_risks"] == []
    assert payload["entries"]  # the model/cost change is still reported, just not blocking


def test_fail_on_risk_blocks_a_changed_file_that_fails_to_parse_on_head(
    tmp_path, monkeypatch, capsys
):
    _init_repo(tmp_path)
    _commit_app(tmp_path, "value = 1\n", "base", branch="base")
    _commit_app(tmp_path, "def broken(:\n", "head")
    monkeypatch.chdir(tmp_path)

    code = cli.main(
        ["diff", "--base", "base", "--head", "HEAD", "--fail-on-risk", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    [risk] = payload["blocking_risks"]
    assert risk["kind"] == "parse-failure"
    assert risk["location"] == "app.py"


def test_diff_without_fail_on_risk_flag_exits_zero_despite_a_blocking_risk(
    tmp_path, monkeypatch, capsys
):
    _init_repo(tmp_path)
    _commit_app(
        tmp_path, "import anthropic\nclient = anthropic.Anthropic()\n", "base", branch="base"
    )
    _commit_app(
        tmp_path,
        f"import anthropic\nclient = anthropic.Anthropic()\nfor doc in docs:\n    {_CALL}",
        "head",
    )
    monkeypatch.chdir(tmp_path)

    # Same scenario as the first --fail-on-risk test, but the flag is omitted.
    code = cli.main(["diff", "--base", "base", "--head", "HEAD", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["blocking_risks"]  # still reported, just not gating without the flag
