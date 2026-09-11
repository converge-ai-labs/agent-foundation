from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/pr-labels.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.parametrize(
    ("title", "body", "existing", "expected"),
    [
        ("feat(ui): add previews", "", [], ["enhancement"]),
        ("perf(runtime): reduce allocations", "", [], ["enhancement"]),
        ("fix(envd): retain errors", "", [], ["bug"]),
        ("revert(ui): restore defaults", "", [], ["bug"]),
        ("docs: explain setup", "", [], ["documentation"]),
        ("chore(ci): update runners", "", [], ["chore"]),
        ("refactor(runtime): simplify calls", "", [], ["chore"]),
        ("test(runtime): cover recovery", "", [], ["chore"]),
        ("ci: adjust jobs", "", [], ["chore"]),
        ("build: update tooling", "", [], ["chore"]),
        ("style: format sources", "", [], ["chore"]),
        ("feat(api)!: remove old fields", "", [], ["enhancement", "breaking-change"]),
        ("fix(api): update defaults", "Details\n\nBREAKING CHANGE: migrate config", [], ["bug", "breaking-change"]),
        ("fix(api): update defaults", "BREAKING-CHANGE: migrate config", [], ["bug", "breaking-change"]),
        ("feat(api)!: update defaults", "", ["bug", "help wanted"], ["breaking-change"]),
        ("feat(api)!: update defaults", "", ["bug", "breaking-change"], []),
        ("feat(ui): add previews", "", ["enhancement"], []),
        ("feat(ui): add previews", "", ["chore"], []),
        ("fix(ui): correct layout", "", ["help wanted"], ["bug"]),
        ("Update UI", "", [], []),
        ("constructor: not a recognized type", "", [], []),
        ("feat(ui): ${process.exit(1)}", "", [], ["enhancement"]),
    ],
)
def test_auto_labels_preserve_manual_choices_and_treat_pr_text_as_data(title, body, existing, expected):
    script = _workflow()["jobs"]["labels"]["steps"][0]["with"]["script"]
    runner = """
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const notices = [];
const context = {repo: {owner: 'owner', repo: 'repo'}, payload: {pull_request: input.pr}};
const github = {rest: {issues: {addLabels: async args => calls.push(args)}}};
const core = {notice: message => notices.push(message)};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
await new AsyncFunction('context', 'github', 'core', input.script)(context, github, core);
console.log(JSON.stringify({calls, notices}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        input=json.dumps(
            {
                "script": script,
                "pr": {"number": 42, "title": title, "body": body, "labels": [{"name": label} for label in existing]},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["calls"] == (
        [{"owner": "owner", "repo": "repo", "issue_number": 42, "labels": expected}] if expected else []
    )
    if title in ("Update UI", "constructor: not a recognized type"):
        assert output["notices"]


def test_auto_labels_run_only_on_open_and_ready_without_executing_pr_code():
    workflow = _workflow()
    assert workflow["on"] == {"pull_request_target": {"types": ["opened", "ready_for_review"]}}
    assert workflow["permissions"] == {"pull-requests": "write"}
    steps = workflow["jobs"]["labels"]["steps"]
    assert len(steps) == 1
    assert steps[0]["uses"] == "actions/github-script@v8"
    assert "${{" not in steps[0]["with"]["script"]


def test_release_jobs_can_read_labels_without_changing_publish_graphs():
    workflows = list((ROOT / ".github/workflows").glob("release-a13n-*.yml"))
    assert len(workflows) == 10
    for path in workflows:
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert set(workflow["on"]) == {"push"}
        release = workflow["jobs"]["create-release"]
        assert release["permissions"]["pull-requests"] == "read"
        checkout = next(step for step in release["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["fetch-depth"] == "0"
        assert "preflight" not in workflow["jobs"]


def test_all_pr_ci_entry_jobs_skip_drafts_and_listen_for_readiness():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        events = workflow["on"]
        if "pull_request" not in events:
            continue
        assert "ready_for_review" in events["pull_request"]["types"], path.name
        for name, job in workflow["jobs"].items():
            # Ordinary dependent jobs inherit their entry job's skip. Aggregate
            # jobs using always() need their own guard to avoid waking a runner.
            condition = job.get("if", "")
            if not job.get("needs") or "always()" in condition:
                assert "github.event_name != 'pull_request'" in condition, (path.name, name)
                assert "github.event.pull_request.draft == false" in condition, (path.name, name)
