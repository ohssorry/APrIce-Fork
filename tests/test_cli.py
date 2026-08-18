import json
from pathlib import Path

from aprice import cli

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app.py"


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
