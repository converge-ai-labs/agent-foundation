from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.release_version import parse_release_version

REPOSITORY_ROOT = Path(__file__).parents[2]
CHECKER = REPOSITORY_ROOT / "scripts" / "check-release-version.py"


def run_checker(component: str, version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), component, version],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "version",
    [
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2",
        "1.2.3.4",
        "v1.2.3",
        "1.2.3rc1",
        "1.2.3-rc1",
        "1.2.3-rc.0",
        "1.2.3-rc.01",
        "1.2.3-beta.1",
        "+1.2.3",
    ],
)
def test_rejects_noncanonical_release_versions(version: str) -> None:
    result = run_checker("a13n-service", version)

    assert result.returncode != 0
    assert "Release version must use X.Y.Z or X.Y.Z-rc.N syntax" in result.stderr


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3-rc.1", "10.20.30-rc.42"])
def test_accepts_canonical_release_versions(version: str) -> None:
    assert parse_release_version(version).canonical == version


def test_rejects_manifest_version_mismatch() -> None:
    package_path = REPOSITORY_ROOT / "packages/a13n-service/pyproject.toml"
    current_version = tomllib.loads(package_path.read_text(encoding="utf-8"))["project"]["version"]
    major, minor, patch = (int(part) for part in current_version.split("."))
    mismatched_version = f"{major}.{minor}.{patch + 1}"
    result = run_checker("a13n-service", mismatched_version)

    assert result.returncode != 0
    assert f"Expected a13n-service version {mismatched_version}" in result.stderr
    assert f"packages/a13n-service/pyproject.toml: {current_version}" in result.stderr
    assert f"uv.lock package a13n-service: {current_version}" in result.stderr


@pytest.mark.parametrize("component", ["a13n-python", "a13n-go", "a13n-rust", "a13n-typescript", "a13n-service-cli"])
def test_sdk_release_channels_are_not_owned_by_main(component: str) -> None:
    result = run_checker(component, "1.2.3")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
