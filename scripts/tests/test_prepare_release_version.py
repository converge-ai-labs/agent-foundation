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
    Path("packages/agent-environment-provider/pyproject.toml"),
    Path("packages/agent-harness/pyproject.toml"),
    Path("packages/agent-stream-protocol/pyproject.toml"),
    Path("packages/agent-ui/pyproject.toml"),
    Path("packages/logging/pyproject.toml"),
    Path("packages/foundation-service/pyproject.toml"),
    Path("packages/agent-envd-client/pyproject.toml"),
    Path("Cargo.toml"),
    Path("Cargo.lock"),
    Path("crates/agent-envd/Cargo.toml"),
    Path("sdk/python/pyproject.toml"),
    Path("sdk/python/uv.lock"),
    Path("sdk/rust/Cargo.toml"),
    Path("sdk/rust/Cargo.lock"),
    Path("sdk/rust/agent-foundation-cli/Cargo.toml"),
    Path("sdk/rust/agent-foundation-cli/Cargo.lock"),
    Path("sdk/typescript/package.json"),
    Path("sdk/typescript/package-lock.json"),
)


def copy_release_files(destination: Path) -> None:
    for relative_path in RELEASE_FILES:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, target)


def select_agent_ui_harness_release(root: Path, version: str) -> None:
    path = root / "packages/agent-ui/pyproject.toml"
    content = path.read_text(encoding="utf-8")
    updated, replacements = re.subn(
        r'^harness-version = "[^"]*"$',
        f'harness-version = "{version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    assert replacements == 1
    path.write_text(updated, encoding="utf-8")


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
            "harness",
            {
                Path("uv.lock"),
                Path("packages/agent-environment-provider/pyproject.toml"),
                Path("packages/agent-harness/pyproject.toml"),
                Path("packages/agent-stream-protocol/pyproject.toml"),
            },
        ),
        (
            "agent-ui",
            {
                Path("uv.lock"),
                Path("packages/agent-ui/pyproject.toml"),
            },
        ),
        (
            "foundation",
            {
                Path("pyproject.toml"),
                Path("uv.lock"),
                Path("packages/logging/pyproject.toml"),
                Path("packages/foundation-service/pyproject.toml"),
            },
        ),
        (
            "agent-envd",
            {
                Path("Cargo.toml"),
                Path("Cargo.lock"),
                Path("packages/agent-envd-client/pyproject.toml"),
                Path("uv.lock"),
            },
        ),
        (
            "sdk-python",
            {Path("sdk/python/pyproject.toml"), Path("sdk/python/uv.lock")},
        ),
        ("sdk-go", set()),
        (
            "sdk-rust",
            {Path("sdk/rust/Cargo.toml"), Path("sdk/rust/Cargo.lock")},
        ),
        (
            "foundation-cli",
            {
                Path("sdk/rust/agent-foundation-cli/Cargo.toml"),
                Path("sdk/rust/agent-foundation-cli/Cargo.lock"),
            },
        ),
        (
            "sdk-typescript",
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
    if component == "agent-ui":
        select_agent_ui_harness_release(tmp_path, "3.2.1")
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
    select_agent_ui_harness_release(tmp_path, "3.2.1-rc.4")

    for component in (
        "harness",
        "agent-ui",
        "foundation",
        "agent-envd",
        "sdk-python",
        "sdk-go",
        "sdk-rust",
        "foundation-cli",
        "sdk-typescript",
    ):
        result = run_script(PREPARER, tmp_path, component, "9.8.7-rc.2")
        assert result.returncode == 0, result.stderr
        check_result = run_script(CHECKER, tmp_path, component, "9.8.7-rc.2")
        assert check_result.returncode == 0, check_result.stderr

    harness_manifest = (tmp_path / "packages/agent-harness/pyproject.toml").read_text()
    protocol_manifest = (tmp_path / "packages/agent-stream-protocol/pyproject.toml").read_text()
    ui_manifest = (tmp_path / "packages/agent-ui/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/agent-environment-provider/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in harness_manifest
    assert '"converge-agent-environment-provider==9.8.7rc2"' in harness_manifest
    assert '"converge-agent-harness==9.8.7rc2"' in protocol_manifest
    assert 'version = "9.8.7rc2"' in ui_manifest
    assert '"converge-agent-environment-provider==3.2.1rc4"' in ui_manifest
    assert '"converge-agent-harness==3.2.1rc4"' in ui_manifest
    assert '"converge-agent-stream-protocol==3.2.1rc4"' in ui_manifest
    assert 'version = "9.8.7rc2"' in (tmp_path / "pyproject.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "Cargo.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/agent-envd-client/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "sdk/python/pyproject.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "sdk/rust/Cargo.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "sdk/rust/agent-foundation-cli/Cargo.toml").read_text()
    assert json.loads((tmp_path / "sdk/typescript/package.json").read_text())["version"] == "9.8.7-rc.2"


def test_foundation_cli_and_rust_sdk_release_independently(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    cli_result = run_script(PREPARER, tmp_path, "foundation-cli", "9.8.7")

    assert cli_result.returncode == 0, cli_result.stderr
    sdk_check = run_script(CHECKER, tmp_path, "sdk-rust", "0.0.0")
    assert sdk_check.returncode == 0, sdk_check.stderr

    sdk_result = run_script(PREPARER, tmp_path, "sdk-rust", "7.8.9")

    assert sdk_result.returncode == 0, sdk_result.stderr
    cli_check = run_script(CHECKER, tmp_path, "foundation-cli", "9.8.7")
    assert cli_check.returncode == 0, cli_check.stderr


def test_harness_release_does_not_version_agent_ui_or_foundation(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "harness", "9.8.7")

    assert result.returncode == 0, result.stderr
    ui_result = run_script(CHECKER, tmp_path, "agent-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr
    foundation_result = run_script(CHECKER, tmp_path, "foundation", "0.0.0")
    assert foundation_result.returncode == 0, foundation_result.stderr


def test_agent_ui_release_does_not_version_harness_or_foundation(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_agent_ui_harness_release(tmp_path, "3.2.1")

    result = run_script(PREPARER, tmp_path, "agent-ui", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    foundation_result = run_script(CHECKER, tmp_path, "foundation", "0.0.0")
    assert foundation_result.returncode == 0, foundation_result.stderr


def test_harness_release_does_not_version_agent_envd_packages(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "harness", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "agent-envd", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_foundation_release_does_not_version_harness_or_agent_ui(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "foundation", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    ui_result = run_script(CHECKER, tmp_path, "agent-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr


def test_foundation_release_does_not_version_agent_envd_client(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "foundation", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "agent-envd", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_agent_envd_release_does_not_version_foundation_packages(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-envd", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "foundation", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_agent_envd_release_does_not_version_harness_or_agent_ui(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-envd", "9.8.7")

    assert result.returncode == 0, result.stderr
    harness_result = run_script(CHECKER, tmp_path, "harness", "0.0.0")
    assert harness_result.returncode == 0, harness_result.stderr
    ui_result = run_script(CHECKER, tmp_path, "agent-ui", "0.0.0")
    assert ui_result.returncode == 0, ui_result.stderr


def test_agent_ui_release_requires_selected_harness_release_without_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_agent_ui_harness_release(tmp_path, "0.0.0")
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-ui", "9.8.7")

    assert result.returncode != 0
    assert "Select a published Harness release" in result.stderr
    assert snapshot(tmp_path) == before


def test_checker_rejects_provider_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "harness", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/agent-harness/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"converge-agent-environment-provider==9.8.7"',
            '"converge-agent-environment-provider>=9.8.7"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "harness", "9.8.7")

    assert result.returncode != 0
    assert "dependency converge-agent-environment-provider==9.8.7" in result.stderr


def test_checker_rejects_harness_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "harness", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/agent-stream-protocol/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"converge-agent-harness==9.8.7"',
            '"converge-agent-harness>=9.8.7"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "harness", "9.8.7")

    assert result.returncode != 0
    assert "dependency converge-agent-harness==9.8.7" in result.stderr


def test_checker_rejects_agent_ui_dependency_drift(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    select_agent_ui_harness_release(tmp_path, "3.2.1")
    prepare_result = run_script(PREPARER, tmp_path, "agent-ui", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / "packages/agent-ui/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"converge-agent-stream-protocol==3.2.1"',
            '"converge-agent-stream-protocol==3.2.2"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, "agent-ui", "9.8.7")

    assert result.returncode != 0
    assert "dependency converge-agent-stream-protocol==3.2.1" in result.stderr


def test_rejects_invalid_version_without_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "foundation", "v1.2.3")

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

    result = run_script(PREPARER, tmp_path, "sdk-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'packages[""] version' in result.stderr
    assert snapshot(tmp_path) == before


def test_requires_agent_envd_workspace_version_inheritance(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    manifest_path = tmp_path / "crates/agent-envd/Cargo.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "version.workspace = true",
            'version = "0.0.0"',
        ),
        encoding="utf-8",
    )
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-envd", "9.8.7")

    assert result.returncode != 0
    assert "package.version.workspace = true" in result.stderr
    assert snapshot(tmp_path) == before


def test_checker_validates_nested_npm_lock_version(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "sdk-typescript", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    lock_path = tmp_path / "sdk/typescript/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"][""]["version"] = "9.8.6"
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n", encoding="utf-8")

    result = run_script(CHECKER, tmp_path, "sdk-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'sdk/typescript/package-lock.json packages[""]: 9.8.6' in result.stderr
