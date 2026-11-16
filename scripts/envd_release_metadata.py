"""Publish the Envd stable pointer without changing source or versioned releases."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from release_version import parse_release_version

BRANCH = "release-metadata"
POINTER = Path("a13n-envd/stable.txt")


def git(directory: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=directory, check=check, capture_output=True, text=True)


def publish(remote: str, version: str) -> bool:
    selected = parse_release_version(version)
    if selected.is_prerelease:
        print(f"Not advancing Envd stable for prerelease {version}")
        return False

    # Use an independent repository so a first publication has no source history,
    # and the release checkout (including its injected versions) remains untouched.
    with tempfile.TemporaryDirectory(prefix="a13n-envd-metadata-") as temporary:
        directory = Path(temporary)
        git(directory, "init", "--quiet", f"--initial-branch={BRANCH}")
        git(directory, "remote", "add", "origin", remote)
        git(directory, "config", "user.name", "github-actions[bot]")
        git(directory, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        # gh reads the job's GH_TOKEN; no token is written into a URL or Git config.
        git(directory, "config", "credential.https://github.com.helper", "")
        git(directory, "config", "--add", "credential.https://github.com.helper", "!gh auth git-credential")
        reference = f"refs/heads/{BRANCH}"
        lookup = git(directory, "ls-remote", "--exit-code", "--heads", "origin", reference, check=False)
        if lookup.returncode == 0:
            git(directory, "fetch", "--quiet", "--depth=1", "origin", reference)
            git(directory, "checkout", "--quiet", "-B", BRANCH, "FETCH_HEAD")
        elif lookup.returncode != 2:  # Only an absent branch allows initialization.
            lookup.check_returncode()

        pointer = directory / POINTER
        if pointer.exists():
            current = parse_release_version(pointer.read_text(encoding="utf-8").rstrip("\n"))
            if current.is_prerelease:
                raise ValueError("Envd stable metadata must not contain a prerelease")
            if selected.precedence_key <= current.precedence_key:
                print(f"Envd stable remains {current.canonical}; not advancing to {version}")
                return False
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(f"{version}\n", encoding="utf-8")
        git(directory, "add", "--", POINTER.as_posix())
        git(directory, "commit", "--quiet", "-m", f"chore(releases): advance a13n-envd stable to {version}")
        # A concurrent writer rejects this ordinary push instead of being overwritten.
        git(directory, "push", "origin", f"HEAD:{reference}")
    print(f"Published Envd stable {version}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Canonical Envd release version")
    args = parser.parse_args()
    try:
        publish(f"https://github.com/{os.environ['GITHUB_REPOSITORY']}.git", args.version)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error)) from error
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
