from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "guard.yml"


def load():
    # YAML parses the bare `on:` key as the boolean True, not the string
    # "on" -- unrelated to what this test checks, but worth knowing if this
    # file is ever inspected by hand.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_guard_workflow_is_valid_yaml_and_exists():
    assert WORKFLOW.exists()
    assert load() is not None


def test_guard_triggers_only_on_pull_requests_targeting_develop():
    workflow = load()
    assert workflow[True]["pull_request"]["branches"] == ["develop"]


def test_guard_can_write_pull_request_comments():
    workflow = load()
    assert workflow["permissions"]["pull-requests"] == "write"


def test_guard_job_runs_diff_then_comments_then_gates():
    steps = load()["jobs"]["guard"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]

    diff_index = next(i for i, n in enumerate(names) if n == "Run aprice diff")
    comment_index = next(i for i, n in enumerate(names) if "Guard comment" in n)
    gate_index = next(i for i, n in enumerate(names) if "Gate" in n)

    # The comment must be posted before the gate step can fail the job --
    # otherwise a blocking PR would never get its analysis posted.
    assert diff_index < comment_index < gate_index


def test_guard_diff_step_never_fails_the_job_itself():
    steps = load()["jobs"]["guard"]["steps"]
    diff_step = next(s for s in steps if s.get("name") == "Run aprice diff")
    assert "set +e" in diff_step["run"]
    assert "--fail-on-risk" in diff_step["run"]


def test_guard_gate_step_is_skipped_when_diff_passed():
    steps = load()["jobs"]["guard"]["steps"]
    gate_step = next(s for s in steps if "Gate" in s.get("name", ""))
    assert gate_step["if"] == "steps.diff.outputs.exit_code != '0'"


def test_guard_comment_step_is_skipped_on_execution_error():
    steps = load()["jobs"]["guard"]["steps"]
    comment_step = next(s for s in steps if "Guard comment" in s.get("name", ""))
    assert comment_step["if"] == "steps.diff.outputs.exit_code != '2'"
