from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
PREPARER = REPOSITORY_ROOT / "scripts" / "prepare-release-version.py"
CHECKER = REPOSITORY_ROOT / "scripts" / "check-release-version.py"
RELEASE_FILES = (
    Path("pyproject.toml"),
    Path("uv.lock"),
    Path("packages/a13n-environment/pyproject.toml"),
    Path("packages/a13n-harness/pyproject.toml"),
    Path("packages/a13n-stream-protocol/pyproject.toml"),
    Path("packages/a13n-harness-ui/pyproject.toml"),
    Path("packages/a13n-logging/pyproject.toml"),
    Path("packages/a13n-service/pyproject.toml"),
    Path("packages/a13n-envd-client/pyproject.toml"),
    Path("Cargo.toml"),
    Path("Cargo.lock"),
    Path("crates/a13n-envd/Cargo.toml"),
    Path("sdk/python/pyproject.toml"),
    Path("sdk/python/uv.lock"),
    Path("sdk/rust/Cargo.toml"),
    Path("sdk/rust/Cargo.lock"),
    Path("sdk/rust/a13n-service-cli/Cargo.toml"),
    Path("sdk/rust/a13n-service-cli/Cargo.lock"),
    Path("sdk/typescript/package.json"),
    Path("sdk/typescript/package-lock.json"),
)


def copy_release_files(destination: Path) -> None:
    for relative_path in RELEASE_FILES:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, target)


def select_harness_ui_range(root: Path, constraint: str = ">=3.2.1,<4.0.0") -> None:
    path = root / "packages/a13n-harness-ui/pyproject.toml"
    content = path.read_text(encoding="utf-8")
    content, replacements = re.subn(
        r'^(a13n-(?:environment|harness|stream-protocol)) = "[^"]*"$',
        rf'\1 = "{constraint}"',
        content,
        flags=re.MULTILINE,
    )
    assert replacements == 3
    path.write_text(content, encoding="utf-8")


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path: (root / path).read_bytes() for path in RELEASE_FILES}


def run_script(
    script: Path,
    root: Path,
    component: str,
    version: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), component, version],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("component", "changed_paths"),
    [
        (
            "a13n-harness",
            {
                Path("uv.lock"),
                Path("packages/a13n-environment/pyproject.toml"),
                Path("packages/a13n-harness/pyproject.toml"),
                Path("packages/a13n-stream-protocol/pyproject.toml"),
            },
        ),
        (
            "a13n-harness-ui",
            {
                Path("uv.lock"),
                Path("packages/a13n-harness-ui/pyproject.toml"),
            },
        ),
        (
            "a13n-logging",
            {
                Path("uv.lock"),
                Path("packages/a13n-logging/pyproject.toml"),
            },
        ),
        (
            "a13n-service",
            {
                Path("pyproject.toml"),
                Path("uv.lock"),
                Path("packages/a13n-service/pyproject.toml"),
            },
        ),
        (
            "a13n-envd",
            {
                Path("Cargo.toml"),
                Path("Cargo.lock"),
                Path("packages/a13n-envd-client/pyproject.toml"),
                Path("uv.lock"),
            },
        ),
        (
            "a13n-python",
            {Path("sdk/python/pyproject.toml"), Path("sdk/python/uv.lock")},
        ),
        ("a13n-go", set()),
        (
            "a13n-rust",
            {Path("sdk/rust/Cargo.toml"), Path("sdk/rust/Cargo.lock")},
        ),
        (
            "a13n-service-cli",
            {
                Path("sdk/rust/a13n-service-cli/Cargo.toml"),
                Path("sdk/rust/a13n-service-cli/Cargo.lock"),
            },
        ),
        (
            "a13n-typescript",
            {
                Path("sdk/typescript/package.json"),
                Path("sdk/typescript/package-lock.json"),
            },
        ),
    ],
)
def test_prepares_only_component_files_and_is_idempotent(
    tmp_path: Path,
    component: str,
    changed_paths: set[Path],
) -> None:
    copy_release_files(tmp_path)
    if component == "a13n-harness-ui":
        select_harness_ui_range(tmp_path)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, component, "9.8.7")

    assert result.returncode == 0, result.stderr
    after = snapshot(tmp_path)
    assert {path for path in RELEASE_FILES if before[path] != after[path]} == changed_paths

    check_result = run_script(CHECKER, tmp_path, component, "9.8.7")
    assert check_result.returncode == 0, check_result.stderr

    second_result = run_script(PREPARER, tmp_path, component, "9.8.7")
    assert second_result.returncode == 0, second_result.stderr
    assert snapshot(tmp_path) == after
    assert "no files changed" in second_result.stdout


def test_prepares_ecosystem_specific_rc_versions(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_harness_ui_range(tmp_path, ">=3.2.1rc4,<4.0.0")

    for component in (
        "a13n-harness",
        "a13n-harness-ui",
        "a13n-logging",
        "a13n-service",
        "a13n-envd",
        "a13n-python",
        "a13n-go",
        "a13n-rust",
        "a13n-service-cli",
        "a13n-typescript",
    ):
        result = run_script(PREPARER, tmp_path, component, "9.8.7-rc.2")
        assert result.returncode == 0, result.stderr
        check_result = run_script(CHECKER, tmp_path, component, "9.8.7-rc.2")
        assert check_result.returncode == 0, check_result.stderr

    harness_manifest = (tmp_path / "packages/a13n-harness/pyproject.toml").read_text()
    protocol_manifest = (tmp_path / "packages/a13n-stream-protocol/pyproject.toml").read_text()
    ui_manifest = (tmp_path / "packages/a13n-harness-ui/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/a13n-environment/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in harness_manifest
    assert '"a13n-environment==9.8.7rc2"' in harness_manifest
    assert '"a13n-harness==9.8.7rc2"' in protocol_manifest
    assert 'version = "9.8.7rc2"' in ui_manifest
    assert '"a13n-environment>=3.2.1rc4,<4.0.0"' in ui_manifest
    assert '"a13n-harness>=3.2.1rc4,<4.0.0"' in ui_manifest
    assert '"a13n-stream-protocol>=3.2.1rc4,<4.0.0"' in ui_manifest
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/a13n-logging/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "pyproject.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "Cargo.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/a13n-envd-client/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "sdk/python/pyproject.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "sdk/rust/Cargo.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "sdk/rust/a13n-service-cli/Cargo.toml").read_text()
    assert json.loads((tmp_path / "sdk/typescript/package.json").read_text())["version"] == "9.8.7-rc.2"


@pytest.mark.parametrize("logging_version", ["2.3.4", "2.3.4-rc.1"])
def test_logging_and_service_release_independently(tmp_path: Path, logging_version: str) -> None:
    copy_release_files(tmp_path)
    for component, version in (("a13n-logging", logging_version), ("a13n-service", "9.8.7")):
        result = run_script(PREPARER, tmp_path, component, version)
        assert result.returncode == 0, result.stderr

    for component, version in (("a13n-logging", logging_version), ("a13n-service", "9.8.7")):
        result = run_script(CHECKER, tmp_path, component, version)
        assert result.returncode == 0, result.stderr

    result = run_script(PREPARER, tmp_path, "a13n-logging", "3.0.0")
    assert result.returncode == 0, result.stderr
    result = run_script(CHECKER, tmp_path, "a13n-service", "9.8.7")
    assert result.returncode == 0, result.stderr
    mismatch = run_script(CHECKER, tmp_path, "a13n-logging", logging_version)
    assert mismatch.returncode != 0
    assert "packages/a13n-logging/pyproject.toml: 3.0.0" in mismatch.stderr
    assert "uv.lock package a13n-logging: 3.0.0" in mismatch.stderr


def test_a13n_service_cli_and_rust_sdk_release_independently(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    cli_result = run_script(PREPARER, tmp_path, "a13n-service-cli", "9.8.7")

    assert cli_result.returncode == 0, cli_result.stderr
    sdk_check = run_script(CHECKER, tmp_path, "a13n-rust", "0.0.0")
    assert sdk_check.returncode == 0, sdk_check.stderr

    sdk_result = run_script(PREPARER, tmp_path, "a13n-rust", "7.8.9")

    assert sdk_result.returncode == 0, sdk_result.stderr
    cli_check = run_script(CHECKER, tmp_path, "a13n-service-cli", "9.8.7")
    assert cli_check.returncode == 0, cli_check.stderr


def test_harness_release_does_not_version_harness_ui_or_service(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")

    assert result.returncode == 0, result.stderr
    ui_result = run_script(CHECKER, tmp_path, "a13n-harness-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr
    service_result = run_script(CHECKER, tmp_path, "a13n-service", "0.0.0")
    assert service_result.returncode == 0, service_result.stderr


def test_harness_ui_release_does_not_version_harness_or_service(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_harness_ui_range(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "a13n-harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    service_result = run_script(CHECKER, tmp_path, "a13n-service", "0.0.0")
    assert service_result.returncode == 0, service_result.stderr


def test_harness_release_does_not_version_a13n_envd_packages(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "a13n-envd", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_a13n_service_release_does_not_version_harness_or_harness_ui(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-service", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "a13n-harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    ui_result = run_script(CHECKER, tmp_path, "a13n-harness-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr


def test_a13n_service_release_does_not_version_a13n_envd_client(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-service", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "a13n-envd", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_a13n_envd_release_does_not_version_service_packages(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-envd", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "a13n-service", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_a13n_envd_release_does_not_version_harness_or_harness_ui(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-envd", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "a13n-harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    ui_result = run_script(CHECKER, tmp_path, "a13n-harness-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr


@pytest.mark.parametrize(
    "constraint", ["0.0.0", ">=0.0.0,<0.1.0", ">=0.0.5", "<0.1.0", "==0.0.5", ">=0.1.0,<0.1.0", ">=0.1.0rc1,<0.1.0"]
)
def test_harness_ui_release_requires_valid_range_without_writing(tmp_path: Path, constraint: str) -> None:
    copy_release_files(tmp_path)
    select_harness_ui_range(tmp_path, constraint)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")

    assert result.returncode != 0
    assert result.stderr
    assert snapshot(tmp_path) == before


def test_checker_rejects_provider_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/a13n-harness/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"a13n-environment==9.8.7"',
            '"a13n-environment>=9.8.7"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "a13n-harness", "9.8.7")

    assert result.returncode != 0
    assert "dependency a13n-environment==9.8.7" in result.stderr


def test_checker_rejects_harness_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/a13n-stream-protocol/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"a13n-harness==9.8.7"',
            '"a13n-harness>=9.8.7"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "a13n-harness", "9.8.7")

    assert result.returncode != 0
    assert "dependency a13n-harness==9.8.7" in result.stderr


def test_checker_rejects_harness_ui_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_harness_ui_range(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/a13n-harness-ui/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"a13n-stream-protocol>=3.2.1,<4.0.0"',
            '"a13n-stream-protocol>=3.2.2,<4.0.0"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "a13n-harness-ui", "9.8.7")

    assert result.returncode != 0
    assert "dependency a13n-stream-protocol>=3.2.1,<4.0.0" in result.stderr


def test_rejects_invalid_version_without_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-service", "v1.2.3")

    assert result.returncode != 0
    assert "Release version must use X.Y.Z or X.Y.Z-rc.N syntax" in result.stderr
    assert snapshot(tmp_path) == before


def test_validates_all_targets_before_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    lock_path = tmp_path / "sdk/typescript/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["packages"][""]["version"]
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n", encoding="utf-8")
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'packages[""] version' in result.stderr
    assert snapshot(tmp_path) == before


def test_requires_a13n_envd_workspace_version_inheritance(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    manifest_path = tmp_path / "crates/a13n-envd/Cargo.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "version.workspace = true",
            'version = "0.0.0"',
        ),
        encoding="utf-8",
    )
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-envd", "9.8.7")

    assert result.returncode != 0
    assert "package.version.workspace = true" in result.stderr
    assert snapshot(tmp_path) == before


def test_checker_validates_nested_npm_lock_version(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "a13n-typescript", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    lock_path = tmp_path / "sdk/typescript/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"][""]["version"] = "9.8.6"
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n", encoding="utf-8")

    result = run_script(CHECKER, tmp_path, "a13n-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'sdk/typescript/package-lock.json packages[""]: 9.8.6' in result.stderr


def test_mismatched_ui_ranges_are_blocked_without_writes(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    manifest = tmp_path / "packages/a13n-harness-ui/pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace('a13n-harness = ">=0.0.5,<0.1.0"', 'a13n-harness = ">=0.0.6,<0.1.0"')
    )
    before = snapshot(tmp_path)
    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")
    assert result.returncode != 0
    assert "same compatible range" in result.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("component", "manifest", "dependency", "constraint"),
    [
        ("a13n-harness", "a13n-environment", "a13n-envd-client", ">=0.0.5,<0.1.0"),
        ("a13n-harness", "a13n-harness", "a13n-logging", ">=0.1.0,<0.2.0"),
        ("a13n-harness-ui", "a13n-harness-ui", "a13n-logging", ">=0.1.0,<0.2.0"),
    ],
)
def test_independent_dependency_ranges_are_injected_and_checked(
    tmp_path: Path, component: str, manifest: str, dependency: str, constraint: str
) -> None:
    copy_release_files(tmp_path)
    result = run_script(PREPARER, tmp_path, component, "9.8.7")
    assert result.returncode == 0, result.stderr
    path = tmp_path / "packages" / manifest / "pyproject.toml"
    requirement = f'"{dependency}{constraint}"'
    assert requirement in path.read_text()
    path.write_text(path.read_text().replace(requirement, f'"{dependency}"'))
    checked = run_script(CHECKER, tmp_path, component, "9.8.7")
    assert checked.returncode != 0
    assert f"dependency {dependency}{constraint}" in checked.stderr


@pytest.mark.parametrize(
    "constraint", [">=0.0.5,<=0.1.0", ">=0.0.5,<0.0.4", ">=0.0.5.dev1,<0.1.0", ">=0.0.5-rc.1,<0.1.0"]
)
def test_invalid_independent_dependency_policy_is_atomic(tmp_path: Path, constraint: str) -> None:
    copy_release_files(tmp_path)
    path = tmp_path / "packages/a13n-environment/pyproject.toml"
    path.write_text(path.read_text().replace(">=0.0.5,<0.1.0", constraint))
    before = snapshot(tmp_path)
    result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")
    assert result.returncode != 0
    assert snapshot(tmp_path) == before


def test_range_order_is_normalized_without_changing_policy(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_harness_ui_range(tmp_path, "<4.0.0,>=3.2.1")
    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")
    assert result.returncode == 0, result.stderr
    content = (tmp_path / "packages/a13n-harness-ui/pyproject.toml").read_text()
    assert '"a13n-harness>=3.2.1,<4.0.0"' in content
