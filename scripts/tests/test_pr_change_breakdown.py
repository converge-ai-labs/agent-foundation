from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
MODULE = ROOT / ".github/scripts/pr-change-breakdown.cjs"
RULES = ROOT / ".github/scripts/pr-change-rules.cjs"
PR = {
    "number": 42,
    "state": "open",
    "html_url": "https://github.com/owner/repo/pull/42",
    "head": {"sha": "a" * 40},
    "base": {"sha": "b" * 40},
}


def node(script, data):
    runner = "const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));\n" + script
    result = subprocess.run(
        ["node", "-e", runner], input=json.dumps(data), text=True, capture_output=True, check=True, cwd=ROOT
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("packages/a13n-harness/a13n_harness/agent.py", "Core libraries & services"),
        ("packages/a13n-harness/tests/fixtures/compatibility/agent.json", "Tests & fixtures"),
        ("packages/a13n-service/tests/test_migrations.py", "Tests & fixtures"),
        ("packages/a13n-service/a13n_service/migrations/env.py", "Protocols & migrations"),
        ("frontend/apps/a13n-harness-ui/src/app.test.tsx", "Tests & fixtures"),
        ("frontend/apps/a13n-console/src/app.tsx", "UI & presentation"),
        ("frontend/apps/a13n-console/src/service-client/schema.ts", "Generated files"),
        ("packages/a13n-envd-client/a13n_envd_client/eip/v1/models.py", "Generated files"),
        ("crates/a13n-envd/src/process.rs", "Core libraries & services"),
        ("crates/a13n-envd/protocol/eip/v1/testdata/golden.json", "Tests & fixtures"),
        ("proto/a13n-envd/eip/v1/common.proto", "Protocols & migrations"),
        ("proto/a13n-service/README.md", "Documentation"),
        ("spec/a13n-harness/01-design.md", "Specifications"),
        ("deploy/docker/images/a13n-service/Dockerfile", "Build, CI & deployment"),
        ("scripts/tests/test_verify.py", "Tests & fixtures"),
        ("dev/live_tests/scripted.py", "Tests & fixtures"),
        ("scripts/verify.py", "Developer tools & examples"),
        ("examples/demo/main.py", "Developer tools & examples"),
        ("examples/demo/README.md", "Documentation"),
        ("packages/a13n-harness/pyproject.toml", "Dependencies & lockfiles"),
        ("frontend/pnpm-lock.yaml", "Dependencies & lockfiles"),
        ("crates/a13n-envd/build_support/mod.rs", "Build, CI & deployment"),
        ("frontend/apps/a13n-console/tsconfig.json", "Build, CI & deployment"),
        ("frontend/packages/a13n-ui/dev/showcase.tsx", "Developer tools & examples"),
        ("frontend/packages/a13n-ui/LICENSE.coss", "Documentation"),
        ("packages/a13n-harness-ui/a13n_harness_ui/interactive/rendering.py", "UI & presentation"),
        ("packages/a13n-harness-ui/a13n_harness_ui/terminal.py", "UI & presentation"),
        ("packages/a13n-harness-ui/a13n_harness_ui/thread_service.py", "Core libraries & services"),
        ("packages/a13n-harness-ui/tests/interactive/test_rendering.py", "Tests & fixtures"),
        ("frontend/apps/a13n-harness-ui/src/api.generated.ts", "Generated files"),
        ("some-new-surface/file.xyz", "Unclassified"),
    ],
)
def test_classification_precedence(path, category):
    assert (
        node(
            "console.log(JSON.stringify(require(input.module).classify(input.path)))",
            {"module": str(RULES), "path": path},
        )
        == category
    )


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("deploy/docker/images/a13n-service/Dockerfile", "a13n-service"),
        ("deploy/docker/compose/a13n-harness-ui.yaml", "a13n-harness-ui"),
        ("deploy/kubernetes/helm/a13n-service/values.yaml", "a13n-service"),
        ("deploy/kubernetes/README.md", "Repository"),
    ],
)
def test_deploy_paths_belong_to_their_component(path, name):
    assert (
        node(
            "console.log(JSON.stringify(require(input.module).component(input.path)))",
            {"module": str(RULES), "path": path},
        )
        == name
    )


def file(path, **overrides):
    return {"filename": path, "status": "modified", "additions": 10, "deletions": 2, **overrides}


def render(files, **overrides):
    return node(
        "console.log(JSON.stringify(require(input.module).report(input.pr, input.files)))",
        {"module": str(MODULE), "pr": {**PR, "changed_files": len(files), **overrides}, "files": files},
    )


def test_totals_components_renames_deletions_and_nontext_changes():
    body = render(
        [
            file("packages/a13n-harness/a13n_harness/agent.py"),
            file("frontend/apps/a13n-harness-ui/src/app.tsx"),
            file("docs/new.md", previous_filename="old.md", status="renamed", additions=0, deletions=0),
            file("docs/old.md", status="removed", additions=0, deletions=4),
            file("asset.png", additions=0, deletions=0),
        ]
    )
    assert "5 files · +20 / -8" in body
    assert "> [!IMPORTANT]" in body
    assert "**Core libraries & services · 1 file · +10 / -2**" in body
    assert "**43% of all changed lines**" in body
    assert "UI & presentation: **1 file · +10 / -2**" in body
    assert "| **Total** | **5** | **+20** | **-8** |" in body
    assert "| **🟣 Core libraries &#38; services** | **1** | **+10** | **-2** |" in body
    assert "| 🔷 UI &#38; presentation | 1 | +10 | -2 |" in body
    assert "| a13n-harness | 1 | +10 | -2 |" in body
    assert "| a13n-harness-ui | 1 | +10 | -2 |" in body
    assert "old.md &#8594; docs/new.md" in body
    assert "| removed | +0 | -4 |" in body
    assert "no textual delta" in body
    assert "/files#diff-" in body


def test_incomplete_and_oversized_reports_preserve_honest_totals():
    assert "Incomplete report" in render([file("README.md")], changed_files=3001)
    body = render([file(f"docs/{'long' * 100}-{i}.md") for i in range(3000)])
    assert len(body) < 60000
    assert "3000 files · +30000 / -6000" in body
    assert "file links omitted" in body
    assert body.count("<details>") == body.count("</details>")
    assert "Incomplete report" not in body


def test_untrusted_paths_cannot_inject_markup_or_mentions():
    body = render([file("docs/<details>[@everyone](https://evil)`\n${process.exit(1)}.md")])
    assert "@everyone" not in body
    assert "https://evil" not in body
    assert "${process.exit(1)}" not in body
    assert body.count("<details>") == 1


def publish(files, comments=(), current=None):
    return node(
        """
const calls = [];
let reads = 0;
const github = {
  paginate: async method => method === 'files' ? input.files : input.comments,
  rest: {
    pulls: {listFiles: 'files', get: async () => ({data: reads++ ? input.current : input.pr})},
    issues: {
      listComments: 'comments',
      createComment: async args => calls.push({action: 'create', ...args}),
      updateComment: async args => calls.push({action: 'update', ...args}),
    },
  },
};
require(input.module).publish({github,
  context: {repo: {owner: 'owner', repo: 'repo'}, payload: {pull_request: input.pr}},
  core: {notice: () => {}},
}).then(() => console.log(JSON.stringify(calls)));
""",
        {
            "module": str(MODULE),
            "files": files,
            "comments": comments,
            "pr": {**PR, "changed_files": len(files)},
            "current": current or PR,
        },
    )


def test_publish_updates_one_bot_comment_and_leaves_human_content_alone():
    files = [file("README.md")]
    created = publish(files)[0]
    assert created["action"] == "create"
    human = {"id": 1, "user": {"login": "human"}, "body": created["body"]}
    assert publish(files, [human])[0]["action"] == "create"
    bot = {"id": 2, "user": {"login": "github-actions[bot]"}, "body": created["body"]}
    assert publish(files, [bot]) == []
    updated = publish([file("README.md", additions=20)], [human, bot])[0]
    assert updated["action"] == "update"
    assert updated["comment_id"] == 2
    assert "1 file · +20 / -2" in updated["body"]
    assert "0 files · +0 / -0" in publish([], [bot])[0]["body"]


@pytest.mark.parametrize("change", [{"head": {"sha": "new"}}, {"base": {"sha": "new"}}, {"state": "closed"}])
def test_revision_change_during_collection_does_not_publish(change):
    assert publish([file("README.md")], current={**PR, **change}) == []


def test_workflow_loads_only_base_code_and_ci_runs_tests():
    workflow = yaml.load((ROOT / ".github/workflows/pr-change-breakdown.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"pull_request_target"}
    assert set(workflow["on"]["pull_request_target"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "edited",
    }
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["breakdown"]["steps"]
    assert len(steps) == 2
    assert steps[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert "${{" not in steps[1]["with"]["script"]
    ci = yaml.load((ROOT / ".github/workflows/ci-automation.yml").read_text(), Loader=yaml.BaseLoader)
    for event in ("push", "pull_request"):
        assert "scripts/tests/test_pr_change_breakdown.py" in ci["on"][event]["paths"]
    assert "scripts/tests/test_pr_change_breakdown.py" in ci["jobs"]["release-tooling"]["steps"][-1]["run"]


def test_no_core_and_zero_text_delta_do_not_imply_runtime_changes():
    docs = render([file("README.md")])
    assert "> [!NOTE]" in docs
    assert "No core library or service files changed" in docs
    assert "Review focus:" not in docs
    assert "### Changes by component" not in docs
    ui = render([file("frontend/apps/a13n-console/src/app.tsx", additions=0, deletions=0)])
    assert "No textual delta" in ui
    assert "%" not in ui
    empty = render([])
    assert "NaN" not in empty and "Infinity" not in empty
    assert "| **Total** | **0** | **+0** | **-0** |" in empty


def test_incomplete_report_scopes_percentages_and_absence_to_returned_files():
    body = render([file("README.md")], changed_files=3001)
    assert "> [!WARNING]" in body
    assert "No core library or service files in the returned files" in body
    assert "0% of returned changed lines" in body
    assert "**Returned changes:" in body
    assert "| **Total**" not in body


def test_many_components_and_hostile_names_keep_report_bounded():
    files = [file(f"packages/a13n-{'x' * 100}-{i}/src/main.py") for i in range(3000)]
    body = render(files)
    assert len(body) < 60000
    assert "component rows omitted" in body
    assert "**3000** | **+30000** | **-6000**" in body
    assert body.count("<details>") == body.count("</details>")
    hostile = render([file("packages/a13n-<script>@everyone/src/main.py")])
    assert "<script>" not in hostile and "@everyone" not in hostile
