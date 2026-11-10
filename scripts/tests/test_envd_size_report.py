from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
REPORTER = ROOT / ".github/scripts/envd-size-comment.cjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
REPORT = dict(
    target="x86_64-unknown-linux-gnu",
    variant="default",
    binary_bytes=12,
    archive_bytes=7,
    binary_budget=32,
    archive_budget=16,
    microsandbox_copies=None,
)


def test_reporter_uses_trusted_code_and_completed_run_artifacts():
    workflow = yaml.safe_load((ROOT / ".github/workflows/envd-size-report.yml").read_text())
    assert workflow["permissions"]["pull-requests"] == "write"
    steps = workflow["jobs"]["comment"]["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.workflow_sha }}"
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
    assert "workflow_run" in workflow[True]


@pytest.mark.parametrize(
    "scenario", ["create", "update", "fork", "wrong_repository", "stale", "moved", "newer", "empty", "invalid"]
)
def test_reporter_posts_only_for_current_head_and_upserts(tmp_path, scenario):
    directory = tmp_path / "envd-size-test"
    directory.mkdir()
    if scenario != "empty":
        (directory / "report.json").write_text(
            json.dumps(REPORT | ({"target": "<untrusted>"} if scenario == "invalid" else {}))
        )
    program = r"""
const {post, render} = require(process.argv[1]);
const scenario = process.argv[2];
const run = {id: 10, run_attempt: 1, event: 'pull_request', head_sha: 'abc123', head_repository: {id: 20}};
const context = {repo: {owner: 'owner', repo: 'repo'}, payload: {workflow_run: run}};
// A fork head belongs to a different repository than the base, but still matches
// the completed run's head_repository. A mismatched repository must be ignored.
if (scenario === 'fork') run.head_repository.id = 999;
const head = {sha: scenario === 'stale' ? 'previous' : 'abc123',
    repo: {id: scenario === 'wrong_repository' ? 888 : run.head_repository.id}};
const calls = [];
const previous = ['update', 'newer'].includes(scenario) ? [{id: 42, user: {login: 'github-actions[bot]'},
    body: render([], {...run, id: scenario === 'newer' ? 11 : 9}, context.repo)}] : [];
const github = {rest: {pulls: {list: 'pulls', get: async () => ({data: {head: {sha: scenario === 'moved' ? 'changed' : 'abc123'}}})},
    issues: {listComments: 'comments', createComment: async args => calls.push(['create', args]), updateComment: async args => calls.push(['update', args])}},
    paginate: async method => method === 'pulls' ? [{number: 7, head}] : previous};
post({github, context, core: {info() {}}, directory: process.cwd()})
  .then(() => console.log(JSON.stringify({calls})))
  .catch(error => console.log(JSON.stringify({error: error.message, calls})));
"""
    result = subprocess.run(
        ["node", "-e", program, str(REPORTER), scenario], cwd=tmp_path, text=True, capture_output=True, check=True
    )
    data = json.loads(result.stdout)
    if scenario in {"create", "update", "fork"}:
        assert len(data["calls"]) == 1
        operation, args = data["calls"][0]
        assert operation == ("create" if scenario == "fork" else scenario)
        assert "| Linux x86_64 | Default |" in args["body"]
        assert "**1 build within budget.**" in args["body"]
        assert "<summary>Budgets and build details</summary>" in args["body"]
        assert "1/6 build reports" in args["body"]
    else:
        assert data["calls"] == []
        if scenario == "invalid":
            assert data["error"] == "Invalid build identity"


@pytest.mark.parametrize("copies", [0, 1, 7])
def test_report_render_shows_vm_duplication_failure(copies):
    report = REPORT | {"variant": "vm", "microsandbox_copies": copies}
    program = "console.log(require(process.argv[1]).render([JSON.parse(process.argv[2])], {id: 1, head_sha: 'abc', run_attempt: 1}, {owner: 'o', repo: 'r'}))"
    result = subprocess.run(
        ["node", "-e", program, str(REPORTER), json.dumps(report)], capture_output=True, text=True, check=True
    )
    assert ("**FAIL**" in result.stdout) is (copies != 1)
    assert (f"{copies} runtime copies (expected 1)" in result.stdout) is (copies != 1)
