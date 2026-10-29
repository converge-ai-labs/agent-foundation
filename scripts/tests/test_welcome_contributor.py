from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/welcome-contributor.yml"
MARKER = "<!-- a13n-first-pr-welcome -->"
PR = {"number": 42, "state": "open", "user": {"login": "new-contributor", "type": "User"}}


def workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def welcome(*, pages=(), comments=(), pr=None, current=None, error=None, branch="main"):
    script = workflow()["jobs"]["welcome"]["steps"][0]["with"]["script"]
    runner = """
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const context = {
  repo: {owner: 'owner', repo: 'repo'},
  payload: {pull_request: input.pr, repository: {default_branch: input.branch}},
};
const github = {
  paginate: Object.assign(async (method, args) => {
    calls.push({action: method, ...args});
    if (input.error === method) throw new Error('API unavailable');
    return input.comments;
  }, {
    iterator: async function* (method, args) {
      calls.push({action: method, ...args});
      if (input.error === method) throw new Error('API unavailable');
      for (const data of input.pages) yield {data};
    },
  }),
  rest: {
    pulls: {get: async args => {
      calls.push({action: 'get', ...args});
      return {data: input.current};
    }},
    issues: {
      listForRepo: 'history', listComments: 'comments',
      createComment: async args => {
        calls.push({action: 'create', ...args});
        if (input.error === 'create') throw new Error('API unavailable');
        return {data: {id: 1234}};
      },
    },
    reactions: {createForIssueComment: async args => {
      calls.push({action: 'react', ...args});
      if (input.error === 'react') throw new Error('API unavailable');
      return {data: {id: 5678}};
    }},
  },
};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
let error = null;
try {
  await new AsyncFunction('context', 'github', input.script)(context, github);
} catch (failure) {
  error = failure.message;
}
console.log(JSON.stringify({calls, error}));
"""
    pr = {**PR, **(pr or {})}
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        input=json.dumps(
            {
                "script": script,
                "pages": pages,
                "comments": comments,
                "pr": pr,
                "current": {**pr, **(current or {})},
                "error": error,
                "branch": branch,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def creations(output):
    return [call for call in output["calls"] if call["action"] == "create"]


@pytest.mark.parametrize("draft", [True, False])
def test_first_pr_welcomes_fork_authors_and_drafts_with_approved_copy(draft):
    output = welcome(
        pages=[[{"number": 42, "pull_request": {"url": "current"}}]],
        pr={"draft": draft, "head": {"repo": {"full_name": "new-contributor/repo"}}},
    )
    assert output["error"] is None
    assert [call["action"] for call in output["calls"]][-2:] == ["create", "react"]
    assert output["calls"][-1] == {
        "action": "react",
        "owner": "owner",
        "repo": "repo",
        "comment_id": 1234,
        "content": "hooray",
    }
    [comment] = creations(output)
    assert (comment["owner"], comment["repo"], comment["issue_number"]) == ("owner", "repo", 42)
    assert comment["body"].startswith(MARKER)
    assert "Hi @new-contributor, thanks for opening your first pull request" in comment["body"]
    assert "ask here in the PR" in comment["body"]
    assert "https://github.com/owner/repo/blob/main/CONTRIBUTING.md" in comment["body"]
    assert "https://raw.githubusercontent.com/owner/repo/main/.github/assets/contributor-welcome.jpg" in comment["body"]
    assert 'width="640"' in comment["body"]
    assert "join.slack.com/t/a13n/" in comment["body"]
    assert comment["body"].endswith("Glad to have you here!")
    # GitHub comments render single newlines as breaks; let each paragraph wrap naturally.
    paragraphs = comment["body"].split("\n\n")
    assert all("\n" not in paragraph for paragraph in paragraphs)
    assert "ask here in the PR. Our [contributing guide]" in comment["body"]
    assert ") to meet other contributors" in comment["body"]
    history = next(call for call in output["calls"] if call["action"] == "history")
    assert history == {
        "action": "history",
        "owner": "owner",
        "repo": "repo",
        "creator": "new-contributor",
        "state": "all",
        "sort": "created",
        "direction": "asc",
        "per_page": 100,
    }


@pytest.mark.parametrize("state,merged_at", [("open", None), ("closed", None), ("closed", "2026-01-01T00:00:00Z")])
def test_any_earlier_pr_suppresses_welcome_even_on_later_history_pages(state, merged_at):
    output = welcome(
        pages=[
            [{"number": n} for n in range(1, 31)],
            [{"number": 31, "state": state, "pull_request": {"merged_at": merged_at}}],
        ]
    )
    assert creations(output) == []
    assert [call["action"] for call in output["calls"]] == ["get", "history"]


def test_earlier_issues_and_later_prs_do_not_suppress_the_first_pr():
    assert (
        len(
            creations(
                welcome(
                    pages=[[{"number": 1}], [{"number": 42, "pull_request": {}}, {"number": 43, "pull_request": {}}]],
                    pr={"author_association": "MEMBER"},
                )
            )
        )
        == 1
    )


def test_bot_authors_are_skipped_even_when_a_human_reruns_the_workflow():
    output = welcome(pr={"user": {"login": "dependabot[bot]", "type": "Bot"}})
    assert output["calls"] == []


def test_pr_closed_before_job_starts_is_not_welcomed():
    output = welcome(current={"state": "closed"})
    assert creations(output) == []
    assert [call["action"] for call in output["calls"]] == ["get"]


def test_rerun_is_idempotent_and_human_markers_are_not_trusted():
    body = creations(welcome())[0]["body"]
    human = {"user": {"login": "someone"}, "body": body}
    bot = {"id": 999, "user": {"login": "github-actions[bot]"}, "body": body}
    assert len(creations(welcome(comments=[human]))) == 1
    unrelated = [{"user": {"login": "github-actions[bot]"}, "body": "Different report"}] * 100
    rerun = welcome(comments=[*unrelated, human, bot])
    assert creations(rerun) == []
    assert rerun["error"] is None
    assert rerun["calls"][-1] == {
        "action": "react",
        "owner": "owner",
        "repo": "repo",
        "comment_id": 999,
        "content": "hooray",
    }
    assert creations(welcome(comments=[{**bot, "body": MARKER + "\nOld welcome copy"}])) == []
    assert len(creations(welcome(comments=[{"user": None, "body": None}]))) == 1


@pytest.mark.parametrize("method", ["history", "comments"])
def test_api_failure_stops_before_publishing(method):
    output = welcome(error=method)
    assert output["error"] == "API unavailable"
    assert creations(output) == []


def test_comment_failure_does_not_attempt_a_reaction():
    output = welcome(error="create")
    assert output["error"] == "API unavailable"
    assert all(call["action"] != "react" for call in output["calls"])


def test_reaction_failure_can_be_retried_without_posting_another_welcome():
    failed = welcome(error="react")
    assert failed["error"] == "API unavailable"
    [created] = creations(failed)
    retry = welcome(comments=[{"id": 1234, "user": {"login": "github-actions[bot]"}, "body": created["body"]}])
    assert retry["error"] is None
    assert creations(retry) == []
    assert retry["calls"][-1]["action"] == "react"
    assert retry["calls"][-1]["comment_id"] == 1234


def test_untrusted_pr_content_is_not_evaluated_or_echoed_and_default_branch_is_used():
    hostile = "${process.exit(1)} <script>alert(1)</script> @everyone"
    [comment] = creations(welcome(pr={"title": hostile, "body": hostile}, branch="release/community"))
    assert hostile not in comment["body"]
    assert "/blob/release%2Fcommunity/CONTRIBUTING.md" in comment["body"]


def test_workflow_is_metadata_only_and_registered_in_automation_ci():
    config = workflow()
    assert config["on"] == {"pull_request_target": {"types": ["opened"]}}
    assert config["permissions"] == {"pull-requests": "write", "issues": "write"}
    assert config["concurrency"] == {
        "group": "welcome-contributor-${{ github.event.pull_request.number }}",
        "cancel-in-progress": "false",
    }
    steps = config["jobs"]["welcome"]["steps"]
    assert len(steps) == 1
    assert steps[0]["uses"] == "actions/github-script@v8"
    assert "${{" not in steps[0]["with"]["script"]
    ci = yaml.load((ROOT / ".github/workflows/ci-automation.yml").read_text(), Loader=yaml.BaseLoader)
    for event in ("push", "pull_request"):
        assert "scripts/tests/test_welcome_contributor.py" in ci["on"][event]["paths"]
    assert "scripts/tests/test_welcome_contributor.py" in ci["jobs"]["release-tooling"]["steps"][-1]["run"]
    from scripts.verify_dependencies import tests_for

    assert Path(__file__) in tests_for(".github/workflows/welcome-contributor.yml", ROOT)
