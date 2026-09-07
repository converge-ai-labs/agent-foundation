from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    result = run_checker("a13n-go", version)

    assert result.returncode != 0
    assert "Release version must use X.Y.Z or X.Y.Z-rc.N syntax" in result.stderr


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3-rc.1", "10.20.30-rc.42"])
def test_accepts_canonical_release_versions(version: str) -> None:
    result = run_checker("a13n-go", version)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Validated a13n-go version {version}\n"


def test_rejects_manifest_version_mismatch() -> None:
    package_path = REPOSITORY_ROOT / "sdk" / "typescript" / "package.json"
    current_version = json.loads(package_path.read_text(encoding="utf-8"))["version"]
    major, minor, patch = (int(part) for part in current_version.split("."))
    mismatched_version = f"{major}.{minor}.{patch + 1}"
    result = run_checker("a13n-typescript", mismatched_version)

    assert result.returncode != 0
    assert f"Expected a13n-typescript version {mismatched_version}" in result.stderr
    assert f"sdk/typescript/package.json: {current_version}" in result.stderr
    assert f"sdk/typescript/package-lock.json: {current_version}" in result.stderr
