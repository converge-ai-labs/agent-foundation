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
    INITIAL_NOTES,
    build_release_command,
    previous_release_tag,
    read_manual_release_notes,
    release_notes_path,
    release_tag,
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
        previous_tag=None,
    )
    assert "--prerelease" in command
    assert INITIAL_NOTES["a13n-logging"] in command


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


def test_builds_generated_notes_command_for_later_release() -> None:
    command = build_release_command(
        component="a13n-python",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        title="a13n SDK for Python 1.2.3",
        assets=["dist/package.whl", "dist/package.tar.gz"],
        previous_tag="release/a13n/python/1.2.2",
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
        "--generate-notes",
        "--notes-start-tag",
        "release/a13n/python/1.2.2",
    ]


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3-rc.1"])
def test_a13n_service_cli_never_updates_latest_release(version: str) -> None:
    command = build_release_command(
        component="a13n-service-cli",
        version=version,
        repository="converge-ai-labs/agent-foundation",
        title=f"a13n Service CLI {version}",
        assets=["dist/a13n-service-cli.zip"],
        previous_tag=None,
    )

    assert "--latest=false" in command


def test_marks_rc_github_release_as_prerelease() -> None:
    command = build_release_command(
        component="a13n-typescript",
        version="1.2.3-rc.4",
        repository="converge-ai-labs/agent-foundation",
        title="a13n SDK for TypeScript 1.2.3-rc.4",
        assets=["dist/package.tgz"],
        previous_tag="release/a13n/typescript/1.2.3-rc.3",
    )

    assert "--prerelease" in command
    assert command[-2:] == ["--notes-start-tag", "release/a13n/typescript/1.2.3-rc.3"]


def test_builds_initial_release_command_without_cross_channel_notes() -> None:
    command = build_release_command(
        component="a13n-service-cli",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        title="a13n Service CLI 0.0.0",
        assets=[],
        previous_tag=None,
    )

    assert "--generate-notes" not in command
    assert "--notes-start-tag" not in command
    assert command[-2:] == ["--notes", INITIAL_NOTES["a13n-service-cli"]]


def test_prepends_manual_notes_to_generated_notes() -> None:
    manual_notes = "## Highlights\n\n- Add Windows binaries."

    command = build_release_command(
        component="a13n-envd",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        title="a13n-envd 1.2.3",
        assets=[],
        previous_tag="release/a13n-envd-v1.2.2",
        manual_notes=manual_notes,
    )

    notes_index = command.index("--notes")
    generated_index = command.index("--generate-notes")
    assert command[notes_index + 1] == manual_notes
    assert notes_index < generated_index


def test_uses_only_manual_notes_for_first_channel_release() -> None:
    manual_notes = "## Highlights\n\n- First release."

    command = build_release_command(
        component="a13n-go",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        title="a13n SDK for Go 0.0.0",
        assets=[],
        previous_tag=None,
        manual_notes=manual_notes,
    )

    assert "--generate-notes" not in command
    assert command[-2:] == ["--notes", manual_notes]


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
    fake_gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGUMENTS_FILE"\n', encoding="utf-8")
    fake_gh.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "GH_ARGUMENTS_FILE": str(arguments_file),
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
    notes_index = arguments.index("--notes")
    assert arguments[notes_index + 1] == "Curated Python SDK release notes."
    assert arguments[-2:] == ["--notes-start-tag", "release/a13n/python/0.0.0"]
    assert "release/a13n/rust/9.9.9" not in arguments
