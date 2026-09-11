from __future__ import annotations

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
    load_pull_request_labels,
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
        ("a13n-service-cli", "release/a13n-service-cli-v1.2.3"),
        ("a13n-python", "release/a13n/python/1.2.3"),
        ("a13n-go", "release/a13n/go/1.2.3"),
        ("a13n-rust", "release/a13n/rust/1.2.3"),
        ("a13n-typescript", "release/a13n/typescript/1.2.3"),
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
        "release/a13n/python/1.99.0",
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


def test_a13n_service_and_service_cli_release_notes_are_independent() -> None:
    tags = [
        "release/a13n-service-v1.2.3",
        "release/a13n-service-cli-v9.8.7",
    ]

    assert previous_release_tag("a13n-service", "1.2.4", tags) == "release/a13n-service-v1.2.3"
    assert previous_release_tag("a13n-service-cli", "9.8.8", tags) == "release/a13n-service-cli-v9.8.7"


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
        "release/a13n/python/0.0.0",
    ]

    assert previous_release_tag("a13n-service", "0.0.0", tags) is None


def test_builds_release_command_with_notes_on_stdin() -> None:
    command = build_release_command(
        component="a13n-python",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        title="a13n SDK for Python 1.2.3",
        assets=["dist/package.whl", "dist/package.tar.gz"],
    )

    assert command == [
        "gh",
        "release",
        "create",
        "release/a13n/python/1.2.3",
        "dist/package.whl",
        "dist/package.tar.gz",
        "--repo",
        "converge-ai-labs/agent-foundation",
        "--verify-tag",
        "--title",
        "a13n SDK for Python 1.2.3",
        "--notes-file",
        "-",
    ]


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
def test_a13n_service_cli_never_updates_latest_release(version: str) -> None:
    command = build_release_command(
        component="a13n-service-cli",
        version=version,
        repository="converge-ai-labs/agent-foundation",
        title=f"a13n Service CLI {version}",
        assets=["dist/a13n-service-cli.zip"],
    )

    assert "--latest=false" in command


def test_marks_rc_github_release_as_prerelease() -> None:
    command = build_release_command(
        component="a13n-typescript",
        version="1.2.3-rc.4",
        repository="converge-ai-labs/agent-foundation",
        title="a13n SDK for TypeScript 1.2.3-rc.4",
        assets=["dist/package.tgz"],
    )

    assert "--prerelease" in command
    assert command[-2:] == ["--notes-file", "-"]


def test_builds_initial_release_command_without_cross_channel_notes() -> None:
    command = build_release_command(
        component="a13n-service-cli",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Service CLI 0.0.0",
        assets=[],
    )

    assert "--generate-notes" not in command
    assert "--notes-start-tag" not in command
    assert command[-2:] == ["--notes-file", "-"]
    assert (
        render_release_notes(
            component="a13n-service-cli", version="0.0.0", repository="owner/repo", previous_tag=None
        ).strip()
        == INITIAL_NOTES["a13n-service-cli"]
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
        component="a13n-go",
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
    path = tmp_path / release_notes_path("a13n-rust", "1.2.3")
    path.parent.mkdir(parents=True)
    path.write_text("\n", encoding="utf-8")

    assert read_manual_release_notes(tmp_path, "a13n-rust", "1.2.3") is None


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
        "release/a13n/python/0.0.0",
        "release/a13n/python/0.0.1",
        "release/a13n/rust/9.9.9",
    ):
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)

    asset = tmp_path / "package.whl"
    asset.write_bytes(b"wheel")
    manual_notes_path = tmp_path / release_notes_path("a13n-python", "0.0.1")
    manual_notes_path.parent.mkdir(parents=True)
    manual_notes_path.write_text("Curated Python SDK release notes.\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    arguments_file = tmp_path / "gh-arguments"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGUMENTS_FILE"\ncat > "$GH_NOTES_FILE"\n', encoding="utf-8"
    )
    fake_gh.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "GH_ARGUMENTS_FILE": str(arguments_file),
            "GH_NOTES_FILE": str(tmp_path / "gh-notes"),
            "GITHUB_REF_NAME": "release/a13n/python/0.0.1",
            "GITHUB_REPOSITORY": "converge-ai-labs/agent-foundation",
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CREATE_RELEASE),
            "a13n-python",
            "0.0.1",
            "a13n SDK for Python 0.0.1",
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
    assert "release/a13n/python/0.0.0...release/a13n/python/0.0.1" in result.stdout
    arguments = arguments_file.read_text(encoding="utf-8").splitlines()
    assert arguments[-2:] == ["--notes-file", "-"]
    assert "--generate-notes" not in arguments
    notes = (tmp_path / "gh-notes").read_text(encoding="utf-8")
    assert notes.startswith("Curated Python SDK release notes.\n")
    assert "release%2Fa13n%2Fpython%2F0.0.0...release%2Fa13n%2Fpython%2F0.0.1" in notes
    assert "release/a13n/rust/9.9.9" not in notes

    arguments_file.unlink()
    preview = subprocess.run(
        [sys.executable, str(CREATE_RELEASE), "a13n-python", "0.0.1", "Python SDK", "--dry-run"],
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
        ("a13n-harness", "packages/a13n-environment/api.py"),
        ("a13n-harness", "packages/a13n-harness/api.py"),
        ("a13n-harness", "packages/a13n-stream-protocol/api.py"),
        ("a13n-harness-ui", "packages/a13n-harness-ui/api.py"),
        ("a13n-harness-ui", "frontend/apps/a13n-harness-ui/app.tsx"),
        ("a13n-harness-ui", "frontend/packages/a13n-ui/button.tsx"),
        ("a13n-logging", "packages/a13n-logging/api.py"),
        ("a13n-service", "packages/a13n-service/api.py"),
        ("a13n-service", "frontend/apps/a13n-console/app.tsx"),
        ("a13n-service", "deploy/containers/a13n-service/Dockerfile"),
        ("a13n-envd", "crates/a13n-envd/src/lib.rs"),
        ("a13n-envd", "packages/a13n-envd-client/api.py"),
        ("a13n-envd", "scripts/install-a13n-envd.ps1"),
        ("a13n-envd", "deploy/containers/sandbox/Dockerfile"),
        ("a13n-service-cli", "sdk/rust/a13n-service-cli/src/main.rs"),
        ("a13n-python", "sdk/python/api.py"),
        ("a13n-go", "sdk/go/api.go"),
        ("a13n-rust", "sdk/rust/src/lib.rs"),
        ("a13n-typescript", "sdk/typescript/index.ts"),
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


def test_rust_sdk_excludes_independent_service_cli(git_repository):
    root = git_repository
    base = _git(root, "rev-parse", "HEAD")
    _commit(root, "sdk/rust/a13n-service-cli/src/main.rs", "fix: only the CLI")
    assert collect_release_changes(root, "a13n-rust", base, "HEAD") == []


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


def test_label_queries_are_only_for_relevant_pr_numbers_and_are_reused(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="bug\nhelp wanted\n")

    monkeypatch.setattr("release_notes.subprocess.run", run)
    changes = [
        ReleaseChange("a" * 40, "fix: first (#42)"),
        ReleaseChange("b" * 40, "Merge pull request #42 from owner/topic"),
        ReleaseChange("c" * 40, "fix: direct commit"),
    ]
    enriched = load_pull_request_labels(changes, "owner/repo")
    assert len(calls) == 1
    assert calls[0] == ["gh", "pr", "view", "42", "--repo", "owner/repo", "--json", "labels", "--jq", ".labels[].name"]
    assert enriched[0].labels == enriched[1].labels == ("bug", "help wanted")
    assert enriched[2] == changes[2]
    assert changes[0].labels == ()


def test_failed_label_query_does_not_silently_change_categories(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("release_notes.subprocess.run", run)
    with pytest.raises(ValueError, match="Cannot read labels for PR #42"):
        load_pull_request_labels([ReleaseChange("a" * 40, "fix: something (#42)")], "owner/repo")


@pytest.mark.parametrize("dry_run", [False, True])
def test_release_cli_reads_labels_before_rendering_and_dry_run_never_publishes(git_repository, dry_run):
    root = git_repository
    _git(root, "tag", "release/a13n-logging-v0.1.0")
    _commit(root, "packages/a13n-logging/api.py", "feat: repair existing behavior (#42)")
    _commit(root, "packages/a13n-harness-ui/api.py", "feat: unrelated UI change (#99)")
    _git(root, "tag", "release/a13n-logging-v0.1.1")
    fake_bin = root / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> gh-calls\n'
        'if [ "$1" = pr ]; then printf "bug\\n"; exit 0; fi\n'
        "cat > published-notes\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
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
    calls = (root / "gh-calls").read_text().splitlines()
    assert calls[0].startswith("pr view 42 --repo owner/repo")
    assert len(calls) == (1 if dry_run else 2)
    assert (root / "published-notes").exists() is not dry_run
    notes = result.stdout if dry_run else (root / "published-notes").read_text()
    assert "### Bug fixes" in notes
    assert "### Features" not in notes
    assert "#99" not in notes
    if not dry_run:
        assert calls[1].startswith("release create release/a13n-logging-v0.1.1 ")
        assert calls[1].endswith("--notes-file -")
