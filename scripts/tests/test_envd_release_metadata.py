from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import envd_release_metadata as metadata


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=directory, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    repository = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(repository))
    return repository


def stable(remote: Path) -> str:
    return git(remote, "show", f"refs/heads/{metadata.BRANCH}:{metadata.POINTER.as_posix()}")


def test_first_publication_creates_only_the_pointer_with_no_source_history(remote: Path) -> None:
    assert metadata.publish(str(remote), "1.2.3")
    assert stable(remote) == "1.2.3"
    assert git(remote, "ls-tree", "-r", "--name-only", metadata.BRANCH) == "a13n-envd/stable.txt"
    assert git(remote, "rev-list", "--count", metadata.BRANCH) == "1"
    assert git(remote, "show", "--format=%P", "--no-patch", metadata.BRANCH) == ""
    assert git(remote, "show", "--format=%s", "--no-patch", metadata.BRANCH) == (
        "chore(releases): advance a13n-envd stable to 1.2.3"
    )


@pytest.mark.parametrize("version", ["1.2.3", "1.2.2", "0.99.99", "2.0.0-rc.1"])
def test_same_older_and_rc_retries_leave_the_pointer_and_commit_unchanged(remote: Path, version: str) -> None:
    metadata.publish(str(remote), "1.2.3")
    before = git(remote, "rev-parse", metadata.BRANCH)
    assert not metadata.publish(str(remote), version)
    assert git(remote, "rev-parse", metadata.BRANCH) == before
    assert stable(remote) == "1.2.3"


def test_rc_does_not_initialize_or_access_a_remote(tmp_path: Path) -> None:
    assert not metadata.publish(str(tmp_path / "does-not-exist.git"), "1.2.3-rc.1")


def test_advancement_compares_versions_numerically_and_preserves_other_metadata(remote: Path, tmp_path: Path) -> None:
    metadata.publish(str(remote), "1.9.9")
    checkout = tmp_path / "other-channel"
    git(tmp_path, "clone", "--branch", metadata.BRANCH, str(remote), str(checkout))
    (checkout / "other-component.txt").write_text("untouched\n")
    git(checkout, "add", "other-component.txt")
    git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Add another channel")
    git(checkout, "push", "origin", metadata.BRANCH)
    previous = git(remote, "rev-parse", metadata.BRANCH)

    assert metadata.publish(str(remote), "1.10.0")

    assert stable(remote) == "1.10.0"
    assert git(remote, "rev-parse", f"{metadata.BRANCH}^") == previous
    assert git(remote, "show", f"{metadata.BRANCH}:other-component.txt") == "untouched"


@pytest.mark.parametrize("version", ["latest", "01.2.3", "1.2.3rc1", "1.2.3\nevil"])
def test_invalid_versions_fail_before_accessing_the_remote(tmp_path: Path, version: str) -> None:
    with pytest.raises(ValueError, match="Release version"):
        metadata.publish(str(tmp_path / "does-not-exist.git"), version)


@pytest.mark.parametrize("content", ["not a version\n", "1.2.3-rc.1\n", ""])
def test_invalid_existing_pointer_is_not_overwritten(remote: Path, tmp_path: Path, content: str) -> None:
    metadata.publish(str(remote), "1.2.3")
    checkout = tmp_path / "corrupt-metadata"
    git(tmp_path, "clone", "--branch", metadata.BRANCH, str(remote), str(checkout))
    (checkout / metadata.POINTER).write_text(content)
    git(checkout, "add", ".")
    git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Corrupt pointer")
    git(checkout, "push", "origin", metadata.BRANCH)
    before = git(remote, "rev-parse", metadata.BRANCH)

    with pytest.raises(ValueError):
        metadata.publish(str(remote), "1.2.4")
    assert git(remote, "rev-parse", metadata.BRANCH) == before


def test_remote_lookup_failure_does_not_initialize_metadata(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        metadata.publish(str(tmp_path / "does-not-exist.git"), "1.2.3")


def test_concurrent_advance_rejects_the_push_without_overwriting_the_remote(remote: Path, monkeypatch) -> None:
    metadata.publish(str(remote), "1.2.3")
    original = metadata.git
    raced = False

    def concurrent_git(directory, *arguments, **kwargs):
        nonlocal raced
        if arguments[0] == "push" and not raced:
            raced = True
            metadata.publish(str(remote), "1.2.5")
        return original(directory, *arguments, **kwargs)

    monkeypatch.setattr(metadata, "git", concurrent_git)
    with pytest.raises(subprocess.CalledProcessError):
        metadata.publish(str(remote), "1.2.4")
    assert stable(remote) == "1.2.5"
    assert not metadata.publish(str(remote), "1.2.4")
