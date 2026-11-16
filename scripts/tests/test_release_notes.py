from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
CREATE_RELEASE = SCRIPTS_DIRECTORY / "create-github-release.py"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from release_notes import (  # noqa: E402
    COMPONENT_PATHS,
    INITIAL_NOTES,
    ReleaseChange,
    build_release_command,
    collect_release_changes,
    component_paths,
    load_pull_request_metadata,
    previous_release_tag,
    read_manual_release_notes,
    release_notes_path,
    release_tag,
    render_release_notes,
)


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("a13n-harness", "release/a13n-harness-v1.2.3"),
        ("a13n-harness-ui", "release/a13n-harness-ui-v1.2.3"),
        ("a13n-logging", "release/a13n-logging-v1.2.3"),
        ("a13n-service", "release/a13n-service-v1.2.3"),
        ("a13n-envd", "release/a13n-envd-v1.2.3"),
    ],
)
def test_builds_canonical_channel_tag(component: str, expected: str) -> None:
    assert release_tag(component, "1.2.3") == expected


def test_finds_highest_lower_stable_version_in_same_channel() -> None:
    tags = [
        "release/a13n-service-v1.9.0",
        "release/a13n-service-v1.10.0",
        "release/a13n-service-v2.0.0-rc.1",
        "release/a13n-service-v2.0.0-rc.2",
        "release/a13n-service-v2.0.0",
        "release/a13n-service-vnot-a-version",
        "release/a13n-envd-v1.99.0",
        "release/a13n-logging-v1.99.0",
    ]

    assert previous_release_tag("a13n-service", "2.0.0", tags) == "release/a13n-service-v1.10.0"


def test_rc_release_uses_previous_rc_in_the_same_channel() -> None:
    tags = [
        "release/a13n-service-v1.10.0",
        "release/a13n-service-v2.0.0-rc.1",
        "release/a13n-service-v2.0.0-rc.2",
        "release/a13n-harness-v2.0.0-rc.2",
        "release/a13n-harness-ui-v2.0.0-rc.2",
    ]

    assert previous_release_tag("a13n-service", "2.0.0-rc.2", tags) == "release/a13n-service-v2.0.0-rc.1"
    assert previous_release_tag("a13n-service", "2.0.0-rc.1", tags) == "release/a13n-service-v1.10.0"


def test_logging_release_notes_do_not_follow_service_tags() -> None:
    tags = ["release/a13n-service-v9.8.7"]
    assert previous_release_tag("a13n-logging", "1.0.0", tags) is None
    tags.extend(["release/a13n-logging-v1.0.0", "release/a13n-logging-v1.1.0-rc.1"])
    assert previous_release_tag("a13n-logging", "1.1.0", tags) == "release/a13n-logging-v1.0.0"
    assert previous_release_tag("a13n-logging", "1.1.0-rc.2", tags) == "release/a13n-logging-v1.1.0-rc.1"
    command = build_release_command(
        component="a13n-logging",
        version="1.0.0-rc.1",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Logging 1.0.0-rc.1",
        assets=[],
    )
    assert "--prerelease" in command
    assert command[-2:] == ["--notes-file", "-"]


def test_harness_and_harness_ui_release_notes_are_independent() -> None:
    tags = [
        "release/a13n-harness-v1.2.2",
        "release/a13n-harness-ui-v9.8.7",
    ]

    assert previous_release_tag("a13n-harness", "1.2.3", tags) == "release/a13n-harness-v1.2.2"
    assert previous_release_tag("a13n-harness-ui", "9.8.8", tags) == "release/a13n-harness-ui-v9.8.7"


def test_first_rc_ignores_abandoned_rc_train() -> None:
    tags = [
        "release/a13n-service-v1.5.0",
        "release/a13n-service-v1.6.0-rc.3",
    ]

    assert previous_release_tag("a13n-service", "2.0.0-rc.1", tags) == "release/a13n-service-v1.5.0"


def test_first_channel_release_has_no_previous_tag() -> None:
    tags = [
        "release/a13n-service-v1.0.0",
        "release/a13n-envd-v0.0.0",
        "release/a13n-logging-v0.0.0",
    ]

    assert previous_release_tag("a13n-service", "0.0.0", tags) is None


def test_builds_release_command_with_notes_on_stdin() -> None:
    command = build_release_command(
        component="a13n-logging",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Logging 1.2.3",
        assets=["dist/package.whl", "dist/package.tar.gz"],
    )

    assert command == [
        "gh",
        "release",
        "create",
        "release/a13n-logging-v1.2.3",
        "dist/package.whl",
        "dist/package.tar.gz",
        "--repo",
        "converge-ai-labs/agent-foundation",
        "--verify-tag",
        "--title",
        "a13n Logging 1.2.3",
        "--notes-file",
        "-",
    ]


def test_marks_rc_github_release_as_prerelease() -> None:
    command = build_release_command(
        component="a13n-service",
        version="1.2.3-rc.4",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Service 1.2.3-rc.4",
        assets=["dist/package.tar.gz"],
    )

    assert "--prerelease" in command
    assert command[-2:] == ["--notes-file", "-"]


def test_builds_initial_release_command_without_cross_channel_notes() -> None:
    command = build_release_command(
        component="a13n-envd",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Envd 0.0.0",
        assets=[],
    )

    assert "--generate-notes" not in command
    assert "--notes-start-tag" not in command
    assert command[-2:] == ["--notes-file", "-"]
    assert (
        render_release_notes(component="a13n-envd", version="0.0.0", repository="owner/repo", previous_tag=None).strip()
        == INITIAL_NOTES["a13n-envd"]
    )


def test_prepends_manual_notes_to_generated_notes() -> None:
    manual_notes = "## Highlights\n\n- Add Windows binaries."

    notes = render_release_notes(
        component="a13n-envd",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        previous_tag="release/a13n-envd-v1.2.2",
        manual_notes=manual_notes,
    )

    assert notes.startswith(manual_notes + "\n\n## What's Changed")
    assert "No component-scoped changelog entries" in notes
    assert "release%2Fa13n-envd-v1.2.2...release%2Fa13n-envd-v1.2.3" in notes


def test_uses_only_manual_notes_for_first_channel_release() -> None:
    manual_notes = "## Highlights\n\n- First release."

    notes = render_release_notes(
        component="a13n-service",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        previous_tag=None,
        manual_notes=manual_notes,
    )

    assert notes == manual_notes + "\n"


def test_reads_optional_versioned_release_notes(tmp_path: Path) -> None:
    assert read_manual_release_notes(tmp_path, "a13n-service", "1.2.3") is None

    relative_path = release_notes_path("a13n-service", "1.2.3")
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text("\n## Highlights\n\n- New release.\n", encoding="utf-8")

    assert read_manual_release_notes(tmp_path, "a13n-service", "1.2.3") == ("## Highlights\n\n- New release.")


def test_treats_empty_versioned_release_notes_as_absent(tmp_path: Path) -> None:
    path = tmp_path / release_notes_path("a13n-envd", "1.2.3")
    path.parent.mkdir(parents=True)
    path.write_text("\n", encoding="utf-8")

    assert read_manual_release_notes(tmp_path, "a13n-envd", "1.2.3") is None


def test_cli_scopes_generated_notes_to_previous_channel_tag(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("release test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "test release"], cwd=tmp_path, check=True)
    for tag in (
        "release/a13n-logging-v0.0.0",
        "release/a13n-logging-v0.0.1",
        "release/a13n-envd-v9.9.9",
    ):
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)

    asset = tmp_path / "package.whl"
    asset.write_bytes(b"wheel")
    manual_notes_path = tmp_path / release_notes_path("a13n-logging", "0.0.1")
    manual_notes_path.parent.mkdir(parents=True)
    manual_notes_path.write_text("Curated Logging release notes.\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    arguments_file = tmp_path / "gh-arguments"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "release view" ]; then echo "release not found" >&2; exit 1; fi\n'
        'printf "%s\\n" "$@" > "$GH_ARGUMENTS_FILE"\ncat > "$GH_NOTES_FILE"\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "GH_ARGUMENTS_FILE": str(arguments_file),
            "GH_NOTES_FILE": str(tmp_path / "gh-notes"),
            "GITHUB_ACTOR": "tag-pusher",
            "GITHUB_TRIGGERING_ACTOR": "rerun-initiator",
            "GITHUB_REF_NAME": "release/a13n-logging-v0.0.1",
            "GITHUB_REPOSITORY": "converge-ai-labs/agent-foundation",
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CREATE_RELEASE),
            "a13n-logging",
            "0.0.1",
            "a13n Logging 0.0.1",
            str(asset),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Prepending curated notes from" in result.stdout
    assert "release/a13n-logging-v0.0.0...release/a13n-logging-v0.0.1" in result.stdout
    arguments = arguments_file.read_text(encoding="utf-8").splitlines()
    assert arguments[-2:] == ["--notes-file", "-"]
    assert "--generate-notes" not in arguments
    notes = (tmp_path / "gh-notes").read_text(encoding="utf-8")
    assert notes.startswith("Release initiated by @tag-pusher.\n\nCurated Logging release notes.\n")
    assert notes.endswith("## Contributors\n\n@tag-pusher\n")
    assert "rerun-initiator" not in notes
    assert "release%2Fa13n-logging-v0.0.0...release%2Fa13n-logging-v0.0.1" in notes
    assert "release/a13n-envd-v9.9.9" not in notes

    arguments_file.unlink()
    preview = subprocess.run(
        [sys.executable, str(CREATE_RELEASE), "a13n-logging", "0.0.1", "Logging", "--dry-run"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0, preview.stderr
    assert preview.stdout == notes
    assert not arguments_file.exists()


@pytest.mark.parametrize(
    ("subject", "body", "category", "title"),
    [
        ("feat(ui): add previews (#12)", "", "Features", "add previews"),
        ("fix(runtime): retain errors", "", "Bug fixes", "retain errors"),
        ("perf: reduce allocations", "", "Performance", "reduce allocations"),
        ("docs: explain migration", "", "Documentation", "explain migration"),
        ("test: exercise Windows paths", "", "Other changes", "exercise Windows paths"),
        ("feat(api)!: remove legacy fields", "", "Breaking changes", "remove legacy fields"),
        ("fix: change defaults", "BREAKING CHANGE: update config", "Breaking changes", "change defaults"),
        ("fix: change defaults", "BREAKING-CHANGE: update config", "Breaking changes", "change defaults"),
        ("An ordinary commit", "", "Other changes", "An ordinary commit"),
        ("Merge pull request #12 from owner/topic", "feat: add previews\n\nDetails", "Features", "add previews"),
    ],
)
def test_renders_conventional_changes_without_github_labels(subject, body, category, title):
    notes = render_release_notes(
        component="a13n-logging",
        version="0.1.1",
        repository="owner/repo",
        previous_tag="release/a13n-logging-v0.1.0",
        changes=[ReleaseChange("a" * 40, subject, body)],
    )
    assert f"### {category}\n\n- {title}" in notes
    if "#12" in subject:
        assert "[#12](https://github.com/owner/repo/pull/12)" in notes
    else:
        assert f"[aaaaaaa](https://github.com/owner/repo/commit/{'a' * 40})" in notes
    assert "(repository comparison)" in notes


def test_rendering_escapes_titles_and_preserves_category_order():
    notes = render_release_notes(
        component="a13n-logging",
        version="0.1.1",
        repository="owner/repo",
        previous_tag="release/a13n-logging-v0.1.0",
        changes=[
            ReleaseChange("a" * 40, "fix: handle <input> and [links]"),
            ReleaseChange("b" * 40, "feat: add details"),
        ],
    )
    assert notes.index("### Features") < notes.index("### Bug fixes")
    assert r"handle \<input\> and \[links\]" in notes


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet", "--initial-branch=main")
    _git(tmp_path, "config", "user.name", "Release Test")
    _git(tmp_path, "config", "user.email", "release-test@example.com")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _commit(tmp_path, "README.md", "initial repository")
    return tmp_path


def _commit(root: Path, path: str, subject: str) -> str:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(subject + "\n", encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "--quiet", "-m", subject)
    return _git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("component", "path"),
    [
        ("a13n-harness", "packages/a13n-harness/a13n_harness/environment/api.py"),
        ("a13n-harness", "packages/a13n-harness/api.py"),
        ("a13n-harness", "packages/a13n-stream-protocol/api.py"),
        ("a13n-harness-ui", "packages/a13n-harness-ui/api.py"),
        ("a13n-harness-ui", "frontend/apps/a13n-harness-ui/app.tsx"),
        ("a13n-harness-ui", "frontend/packages/a13n-ui/button.tsx"),
        ("a13n-logging", "packages/a13n-logging/api.py"),
        ("a13n-service", "packages/a13n-service/api.py"),
        ("a13n-service", "proto/a13n-service/openapi.json"),
        ("a13n-envd", "proto/a13n-envd/eip/v1/eip.proto"),
        ("a13n-service", "frontend/apps/a13n-console/app.tsx"),
        ("a13n-service", "deploy/docker/images/a13n-service/Dockerfile"),
        ("a13n-service", "deploy/docker/compose/a13n-service.yaml"),
        ("a13n-service", "deploy/kubernetes/helm/a13n-service/values.yaml"),
        ("a13n-service", "deploy/monitoring/alerts.yaml"),
        ("a13n-envd", "crates/a13n-envd/src/lib.rs"),
        ("a13n-envd", "packages/a13n-envd-client/api.py"),
        ("a13n-envd", "scripts/install-a13n-envd.ps1"),
        ("a13n-envd", "deploy/docker/images/sandbox/Dockerfile"),
        ("a13n-envd", "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py"),
    ],
)
def test_collects_actual_component_paths_not_title_scopes(git_repository, component, path):
    root = git_repository
    base = _git(root, "rev-parse", "HEAD")
    selected = _commit(root, path, "fix(unrelated-scope): relevant implementation (#7)")
    _commit(root, "unrelated/file.txt", "feat: unrelated implementation (#8)")
    current = _git(root, "rev-parse", "HEAD")
    _commit(root, path, "feat: not released yet (#9)")
    changes = collect_release_changes(root, component, base, current)
    assert [change.commit for change in changes] == [selected]


def test_all_release_channels_have_scopes():
    from release_version import COMPONENTS

    assert set(COMPONENT_PATHS) == set(COMPONENTS)
    assert all(component_paths(component) for component in COMPONENTS)


def test_merge_pr_is_one_entry_without_branch_commit_duplicates(git_repository):
    root = git_repository
    path = "packages/a13n-logging/api.py"
    _commit(root, path, "feat: initial logging")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", "-c", "topic")
    _commit(root, path, "work in progress")
    _commit(root, "packages/a13n-harness-ui/api.py", "unrelated UI work")
    _git(root, "switch", "main")
    _git(root, "merge", "--no-ff", "topic", "-m", "Merge pull request #42 from owner/topic", "-m", "fix: retain errors")
    merge = _git(root, "rev-parse", "HEAD")
    changes = collect_release_changes(root, "a13n-logging", base, "HEAD")
    assert [change.commit for change in changes] == [merge]
    assert changes[0].body.strip() == "fix: retain errors"


def test_rename_out_of_component_is_included(git_repository):
    root = git_repository
    path = "packages/a13n-logging/api.py"
    _commit(root, path, "feat: initial API")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "mv", path, "moved.py")
    _git(root, "commit", "--quiet", "-m", "refactor!: move API")
    changes = collect_release_changes(root, "a13n-logging", base, "HEAD")
    assert len(changes) == 1
    assert changes[0].subject == "refactor!: move API"


def test_missing_history_does_not_silently_generate_empty_notes(git_repository):
    with pytest.raises(ValueError, match="Cannot collect component changes"):
        collect_release_changes(git_repository, "a13n-logging", "missing-tag", "HEAD")


@pytest.mark.parametrize("component", COMPONENT_PATHS)
@pytest.mark.parametrize("actor", [None, "tag-pusher"])
def test_first_release_preview_mentions_tag_pusher_when_available(git_repository, component, actor):
    root = git_repository
    _git(root, "tag", release_tag(component, "0.1.0"))
    environment = {**os.environ, "GITHUB_REPOSITORY": "owner/repo", "GITHUB_TRIGGERING_ACTOR": "rerun-initiator"}
    environment.pop("GITHUB_ACTOR", None)
    if actor is not None:
        environment["GITHUB_ACTOR"] = actor
    result = subprocess.run(
        [sys.executable, str(CREATE_RELEASE), component, "0.1.0", "Initial release", "--dry-run"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    expected = INITIAL_NOTES[component] + "\n"
    if actor is not None:
        expected = f"Release initiated by @{actor}.\n\n{expected}\n## Contributors\n\n@{actor}\n"
    assert result.stdout == expected


def test_dry_run_ignores_tags_outside_target_ancestry(git_repository):
    root = git_repository
    _git(root, "tag", "release/a13n-logging-v0.1.0")
    _git(root, "switch", "-c", "unreleased")
    _commit(root, "packages/a13n-logging/api.py", "feat: not on main")
    _git(root, "tag", "release/a13n-logging-v0.1.1")
    _git(root, "switch", "main")
    _commit(root, "packages/a13n-logging/api.py", "fix: on main")
    _git(root, "tag", "release/a13n-logging-v0.1.2")
    environment = {**os.environ, "GITHUB_REPOSITORY": "owner/repo"}
    environment.pop("GITHUB_REF_NAME", None)
    result = subprocess.run(
        [sys.executable, str(CREATE_RELEASE), "a13n-logging", "0.1.2", "Logging", "--dry-run"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "release%2Fa13n-logging-v0.1.0...release%2Fa13n-logging-v0.1.2" in result.stdout
    assert "on main" in result.stdout
    assert "not on main" not in result.stdout


@pytest.mark.parametrize(
    ("labels", "category"),
    [
        (("bug",), "Bug fixes"),
        (("enhancement",), "Features"),
        (("documentation",), "Documentation"),
        (("bug", "enhancement"), "Features"),
        (("chore", "breaking-change"), "Breaking changes"),
        (("skip-changelog", "breaking-change"), "Breaking changes"),
        (("chore",), None),
        (("skip-changelog", "bug"), None),
        (("help wanted",), "Other changes"),
    ],
)
def test_labels_classify_and_exclude_component_changes(labels, category):
    notes = render_release_notes(
        component="a13n-logging",
        version="0.1.1",
        repository="owner/repo",
        previous_tag="release/a13n-logging-v0.1.0",
        changes=[ReleaseChange("a" * 40, "An ordinary change (#42)", labels=labels)],
    )
    if category is None:
        assert "#42" not in notes
        assert "No component-scoped changelog entries" in notes
    else:
        assert f"### {category}" in notes
        assert "[#42]" in notes


def test_labels_override_title_classification():
    notes = render_release_notes(
        component="a13n-logging",
        version="0.1.1",
        repository="owner/repo",
        previous_tag="release/a13n-logging-v0.1.0",
        changes=[ReleaseChange("a" * 40, "feat: repair an existing behavior (#42)", labels=("bug",))],
    )
    assert "### Bug fixes" in notes
    assert "### Features" not in notes


def test_metadata_queries_are_only_for_relevant_pr_numbers_and_are_reused(monkeypatch):
    calls = []
    responses = iter(
        [
            {
                "title": "fix(api): retain errors",
                "labels": [{"name": "bug"}, {"name": "help wanted"}],
                "author": {"login": "pr-author"},
            },
            [
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "commits": {
                                    "nodes": [
                                        {
                                            "commit": {
                                                "id": "commit-id",
                                                "authors": {
                                                    "totalCount": 3,
                                                    "nodes": [
                                                        {"user": {"login": "coauthor"}},
                                                        {"user": {"login": "PR-author"}},
                                                        {"user": None},
                                                    ],
                                                },
                                            }
                                        },
                                    ]
                                }
                            }
                        }
                    }
                }
            ],
            [
                [
                    {"user": {"login": "reviewer"}, "state": "APPROVED"},
                    {"user": {"login": "reviewer"}, "state": "COMMENTED"},
                    {"user": {"login": "draft-reviewer"}, "state": "PENDING"},
                    {"user": None, "state": "DISMISSED"},
                ]
            ],
        ]
    )

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(next(responses)))

    monkeypatch.setattr("release_notes.subprocess.run", run)
    changes = [
        ReleaseChange("a" * 40, "fix: first (#42)"),
        ReleaseChange("b" * 40, "Merge pull request #42 from owner/topic"),
        ReleaseChange("c" * 40, "fix: direct commit"),
    ]
    enriched = load_pull_request_metadata(changes, "owner/repo")
    assert len(calls) == 3
    assert calls[0] == ["gh", "pr", "view", "42", "--repo", "owner/repo", "--json", "labels,title,author"]
    assert calls[1][:5] == ["gh", "api", "graphql", "--paginate", "--slurp"]
    assert calls[2] == ["gh", "api", "--paginate", "--slurp", "repos/owner/repo/pulls/42/reviews"]
    assert enriched[0].labels == enriched[1].labels == ("bug", "help wanted")
    assert enriched[0].title == enriched[1].title == "fix(api): retain errors"
    assert enriched[0].authors == enriched[1].authors == ("coauthor", "pr-author")
    assert enriched[0].reviewers == enriched[1].reviewers == ("reviewer",)
    assert enriched[2] == changes[2]
    assert changes[0].labels == ()


def test_release_notes_preserve_pr_titles_and_deduplicate_only_listed_contributors():
    notes = render_release_notes(
        component="a13n-logging",
        version="0.1.1",
        repository="owner/repo",
        previous_tag="release/a13n-logging-v0.1.0",
        releaser="Publisher",
        manual_notes="## Highlights\n\nCurated release notes.",
        changes=[
            ReleaseChange(
                "a" * 40,
                "fix: old merge subject (#42)",
                labels=("enhancement",),
                title="feat(api): preserve [titles] and <values>",
                authors=("Alice", "Bob"),
                reviewers=("alice", "Reviewer"),
            ),
            ReleaseChange("b" * 40, "fix: another PR (#43)", authors=("bob", "publisher"), reviewers=("reviewer",)),
            ReleaseChange("c" * 40, "chore: omitted PR (#44)", labels=("chore",), authors=("not-in-release",)),
        ],
    )
    assert notes.startswith("Release initiated by @Publisher.\n\n## Highlights")
    assert r"- feat(api): preserve \[titles\] and \<values\> — by @Alice, @Bob; reviewed by @Reviewer" in notes
    assert "old merge subject" not in notes
    assert "reviewed by @alice" not in notes
    assert "not-in-release" not in notes
    assert notes.endswith("## Contributors\n\n@Alice @Bob @Publisher @Reviewer\n")
    assert "[#42](https://github.com/owner/repo/pull/42)" in notes
    assert "### Features" in notes


def test_metadata_paginates_commits_coauthors_and_reviewers(monkeypatch):
    calls = []
    responses = iter(
        [
            {"title": "feat: add a feature", "labels": [], "author": None},
            [
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "commits": {
                                    "nodes": [
                                        {
                                            "commit": {
                                                "id": "many-authors",
                                                "authors": {"totalCount": 2, "nodes": [{"user": {"login": "first"}}]},
                                            }
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "commits": {
                                    "nodes": [
                                        {
                                            "commit": {
                                                "id": "late-commit",
                                                "authors": {
                                                    "totalCount": 1,
                                                    "nodes": [{"user": {"login": "late-author"}}],
                                                },
                                            }
                                        },
                                    ]
                                }
                            }
                        }
                    }
                },
            ],
            [
                {"data": {"node": {"authors": {"nodes": [{"user": {"login": "first"}}]}}}},
                {"data": {"node": {"authors": {"nodes": [{"user": {"login": "late-coauthor"}}]}}}},
            ],
            [
                [{"user": {"login": "reviewer"}, "state": "APPROVED"}],
                [{"user": {"login": "late-reviewer"}, "state": "CHANGES_REQUESTED"}],
            ],
        ]
    )

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(next(responses)))

    monkeypatch.setattr("release_notes.subprocess.run", run)
    change = load_pull_request_metadata([ReleaseChange("a" * 40, "feat: feature (#42)")], "owner/repo")[0]
    assert change.authors == ("first", "late-author", "late-coauthor")
    assert change.reviewers == ("late-reviewer", "reviewer")
    assert "--paginate" in calls[1] and "--slurp" in calls[1]
    assert "after: $endCursor" in calls[1][6]
    assert calls[2][-2:] == ["-F", "id=many-authors"]
    assert "--paginate" in calls[2] and "after: $endCursor" in calls[2][6]
    assert "--paginate" in calls[3] and "--slurp" in calls[3]


@pytest.mark.parametrize("failed_query", [0, 1, 2])
@pytest.mark.parametrize("malformed_json", [False, True])
def test_metadata_failures_never_silently_drop_contributors(monkeypatch, failed_query, malformed_json):
    responses = [
        {"title": "fix: repair", "labels": [], "author": {"login": "author"}},
        [{"data": {"repository": {"pullRequest": {"commits": {"nodes": []}}}}}],
        [],
    ]
    calls = []

    def run(command, **kwargs):
        index = len(calls)
        calls.append(command)
        if index == failed_query:
            if malformed_json:
                return subprocess.CompletedProcess(command, 0, stdout="invalid JSON")
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(responses[index]))

    monkeypatch.setattr("release_notes.subprocess.run", run)
    with pytest.raises(ValueError, match="Cannot read metadata for PR #42"):
        load_pull_request_metadata([ReleaseChange("a" * 40, "fix: repair (#42)")], "owner/repo")
    assert len(calls) == failed_query + 1


def _fake_github_cli(root: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        + """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path("gh-calls").open("a") as calls:
    calls.write(json.dumps(args) + "\\n")
if args[:2] == ["release", "view"]:
    error = os.environ.get("GH_LOOKUP_ERROR")
    if error or not Path("published-notes").exists():
        print(error or "release not found", file=sys.stderr)
        sys.exit(1)
elif args[:3] == ["pr", "view", "42"]:
    print(json.dumps({"title": "feat(api): retain release behavior", "author": {"login": "pr-author"}, "labels": [{"name": "bug"}]}))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps([{"data": {"repository": {"pullRequest": {"commits": {"nodes": [
        {"commit": {"id": "commit-id", "authors": {"totalCount": 1, "nodes": [{"user": {"login": "coauthor"}}]}}}
    ]}}}}}]))
elif args[:3] == ["api", "--paginate", "--slurp"] and args[-1] == "repos/owner/repo/pulls/42/reviews":
    error = os.environ.get("GH_REVIEW_ERROR")
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)
    print(json.dumps([[{"user": {"login": "reviewer"}, "state": "APPROVED"}]]))
elif args[:2] == ["release", "create"]:
    error = os.environ.get("GH_CREATE_ERROR")
    if error or Path("published-notes").exists():
        print(error or "release already exists", file=sys.stderr)
        sys.exit(1)
    Path("published-notes").write_text(sys.stdin.read())
else:
    raise SystemExit(f"Unexpected GitHub command: {args}")
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    return fake_bin


def _github_calls(root: Path) -> list[list[str]]:
    return [json.loads(line) for line in (root / "gh-calls").read_text().splitlines()]


@pytest.mark.parametrize("dry_run", [False, True])
def test_release_cli_reads_metadata_before_rendering_and_dry_run_never_publishes(git_repository, dry_run):
    root = git_repository
    _git(root, "tag", "release/a13n-logging-v0.1.0")
    _commit(root, "packages/a13n-logging/api.py", "feat: repair existing behavior (#42)")
    _commit(root, "packages/a13n-harness-ui/api.py", "feat: unrelated UI change (#99)")
    _git(root, "tag", "release/a13n-logging-v0.1.1")
    fake_bin = _fake_github_cli(root)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF_NAME": "release/a13n-logging-v0.1.1",
    }
    command = [sys.executable, str(CREATE_RELEASE), "a13n-logging", "0.1.1", "Logging"]
    if dry_run:
        command.append("--dry-run")
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=True)
    calls = _github_calls(root)
    if not dry_run:
        assert calls.pop(0) == [
            "release",
            "view",
            "release/a13n-logging-v0.1.1",
            "--repo",
            "owner/repo",
            "--json",
            "tagName",
        ]
    assert calls[0][:5] == ["pr", "view", "42", "--repo", "owner/repo"]
    assert len(calls) == (3 if dry_run else 4)
    assert (root / "published-notes").exists() is not dry_run
    notes = result.stdout if dry_run else (root / "published-notes").read_text()
    assert "### Bug fixes" in notes
    assert "### Features" not in notes
    assert "#99" not in notes
    assert "feat(api): retain release behavior — by @coauthor, @pr-author; reviewed by @reviewer" in notes
    assert "## Contributors\n\n@coauthor @pr-author @reviewer" in notes
    if not dry_run:
        assert calls[-1][:3] == ["release", "create", "release/a13n-logging-v0.1.1"]
        assert calls[-1][-2:] == ["--notes-file", "-"]


@pytest.fixture(params=["0.5.1", "0.6.0-rc.1"])
def release_cli(git_repository, request):
    root = git_repository
    version = request.param
    tag = release_tag("a13n-harness", version)
    _git(root, "tag", "release/a13n-harness-v0.5.0")
    _commit(root, "packages/a13n-harness/api.py", "fix: retain release behavior (#42)")
    _git(root, "tag", tag)
    fake_bin = _fake_github_cli(root)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF_NAME": tag,
        "GITHUB_ACTOR": "tag-pusher",
        "GITHUB_TRIGGERING_ACTOR": "tag-pusher",
        "GH_LOOKUP_ERROR": "",
        "GH_CREATE_ERROR": "",
        "GH_REVIEW_ERROR": "",
    }
    command = [sys.executable, str(CREATE_RELEASE), "a13n-harness", version, f"Harness {version}"]
    return root, environment, command


def test_release_cli_retry_preserves_existing_release_and_dry_run_still_previews(release_cli):
    root, environment, command = release_cli
    asset = root / "package.whl"
    asset.write_bytes(b"wheel")
    command.append(str(asset))
    first = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=True)
    assert "Generating release notes" in first.stdout
    notes = (root / "published-notes").read_text()
    assert "### Bug fixes" in notes
    assert notes.startswith("Release initiated by @tag-pusher.\n\n")
    assert notes.endswith("## Contributors\n\n@coauthor @pr-author @reviewer @tag-pusher\n")

    environment["GITHUB_TRIGGERING_ACTOR"] = "rerun-initiator"
    asset.unlink()
    retry = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=True)
    assert "already exists; leaving its notes and assets unchanged" in retry.stdout
    assert (root / "published-notes").read_text() == notes
    calls = _github_calls(root)
    tag = environment["GITHUB_REF_NAME"]
    assert calls[-1] == ["release", "view", tag, "--repo", "owner/repo", "--json", "tagName"]
    assert len(calls) == 6  # Initial lookup, three metadata queries, create, retry lookup.

    preview = subprocess.run(
        [*command[:-1], "--dry-run"], cwd=root, env=environment, text=True, capture_output=True, check=True
    )
    assert preview.stdout == notes
    assert (root / "published-notes").read_text() == notes
    assert _github_calls(root) == [*calls, *calls[1:4]]


def test_release_cli_metadata_failure_never_publishes_partial_notes(release_cli):
    root, environment, command = release_cli
    environment["GH_REVIEW_ERROR"] = "HTTP 403: Forbidden"
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "Cannot read metadata for PR #42" in result.stderr
    assert not (root / "published-notes").exists()
    assert _github_calls(root)[-1] == ["api", "--paginate", "--slurp", "repos/owner/repo/pulls/42/reviews"]


@pytest.mark.parametrize("error", ["HTTP 401: Bad credentials", "HTTP 403: Forbidden", "HTTP 500: Server error"])
def test_release_cli_lookup_errors_do_not_create_or_skip_a_release(release_cli, error):
    root, environment, command = release_cli
    environment["GH_LOOKUP_ERROR"] = error
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "Cannot read GitHub Release" in result.stderr
    assert error in result.stderr
    assert len(_github_calls(root)) == 1
    assert not (root / "published-notes").exists()


def test_release_cli_still_reports_creation_failures(release_cli):
    root, environment, command = release_cli
    environment["GH_CREATE_ERROR"] = "upload failed"
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode == 1
    assert "upload failed" in result.stderr
    assert not (root / "published-notes").exists()


def test_release_cli_validates_tag_before_skipping_an_existing_release(release_cli):
    root, environment, command = release_cli
    (root / "published-notes").write_text("Existing release notes")
    environment["GITHUB_REF_NAME"] = "release/a13n-harness-v9.9.9"
    result = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "Expected release tag" in result.stderr
    assert not (root / "gh-calls").exists()


def test_service_exports_do_not_enter_envd_release_notes(git_repository):
    root = git_repository
    base = _git(root, "rev-parse", "HEAD")
    change = _commit(root, "proto/a13n-service/openapi.json", "fix: update the Service contract")
    assert collect_release_changes(root, "a13n-envd", base, "HEAD") == []
    assert [item.commit for item in collect_release_changes(root, "a13n-service", base, "HEAD")] == [change]
