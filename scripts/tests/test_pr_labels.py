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
    assert workflow["on"] == {
        "pull_request_target": {"types": ["opened", "synchronize", "reopened", "ready_for_review"]}
    }
    assert workflow["jobs"]["labels"]["if"] == (
        "github.event.action == 'opened' || github.event.action == 'ready_for_review'"
    )
    assert workflow["permissions"] == {"pull-requests": "write"}
    steps = workflow["jobs"]["labels"]["steps"]
    assert len(steps) == 1
    assert steps[0]["uses"] == "actions/github-script@v8"
    assert "${{" not in steps[0]["with"]["script"]


@pytest.mark.parametrize("status", ["added", "modified", "removed", "renamed"])
def test_compatibility_notice_handles_baseline_changes_and_renames(status):
    prefix = "packages/a13n-harness/tests/fixtures/compatibility/"
    file = {"filename": prefix + "agent.json", "status": status}
    if status == "renamed":
        file.update(filename="elsewhere/agent.json", previous_filename=prefix + "agent.json")
    calls = _compatibility_notice([file])
    assert len(calls) == 1
    assert calls[0]["action"] == "create"
    assert "human review requested" in calls[0]["body"]
    assert "explicit human agreement" in calls[0]["body"]
    assert "1 changed files" in calls[0]["body"]


def _compatibility_notice(files, comments=()):
    script = _workflow()["jobs"]["compatibility-review"]["steps"][0]["with"]["script"]
    runner = """
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const context = {repo: {owner: 'owner', repo: 'repo'}, payload: {pull_request: {number: 42}}};
const github = {
  paginate: async method => method === 'files' ? input.files : input.comments,
  rest: {
    pulls: {listFiles: 'files'},
    issues: {
      listComments: 'comments',
      createComment: async args => calls.push({action: 'create', ...args}),
      updateComment: async args => calls.push({action: 'update', ...args}),
    },
  },
};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
await new AsyncFunction('context', 'github', input.script)(context, github);
console.log(JSON.stringify(calls));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        input=json.dumps({"script": script, "files": files, "comments": comments}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_compatibility_notice_is_idempotent_and_never_edits_human_comments():
    files = [{"filename": "packages/a13n-harness/tests/fixtures/compatibility/agent.json", "status": "modified"}]
    body = _compatibility_notice(files)[0]["body"]
    human = {"id": 7, "user": {"login": "human"}, "body": body}
    assert _compatibility_notice(files, [human])[0]["action"] == "create"
    previous = {"id": 8, "user": {"login": "github-actions[bot]"}, "body": body}
    assert _compatibility_notice(files, [previous]) == []
    changed = _compatibility_notice(files * 2, [human, previous])
    assert changed[0]["action"] == "update"
    assert changed[0]["comment_id"] == 8
    cleared = _compatibility_notice([], [previous])
    assert "No Harness compatibility baseline changes remain" in cleared[0]["body"]
    assert _compatibility_notice([], [human]) == []
    assert _compatibility_notice([{"filename": "README.md", "status": "modified"}]) == []


def test_compatibility_notice_never_evaluates_or_echoes_untrusted_filenames():
    files = [
        {
            "filename": "packages/a13n-harness/tests/fixtures/compatibility/`@everyone${process.exit(1)}",
            "status": "added",
        }
    ]
    body = _compatibility_notice(files)[0]["body"]
    assert "@everyone" not in body
    assert "process.exit" not in body
    job = _workflow()["jobs"]["compatibility-review"]
    assert job["concurrency"]["cancel-in-progress"] == "false"
    assert len(job["steps"]) == 1
    assert job["steps"][0]["uses"] == "actions/github-script@v8"
    assert "${{" not in job["steps"][0]["with"]["script"]


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
