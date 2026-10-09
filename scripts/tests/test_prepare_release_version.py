from __future__ import annotations

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
    Path("packages/a13n-harness/pyproject.toml"),
    Path("packages/a13n-stream-protocol/pyproject.toml"),
    Path("packages/a13n-harness-ui/pyproject.toml"),
    Path("packages/a13n-logging/pyproject.toml"),
    Path("packages/a13n-service/pyproject.toml"),
    Path("packages/a13n-envd-client/pyproject.toml"),
    Path("Cargo.toml"),
    Path("Cargo.lock"),
    Path("crates/a13n-envd/Cargo.toml"),
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
        r'^(a13n-(?:harness|stream-protocol)) = "[^"]*"$',
        rf'\1 = "{constraint}"',
        content,
        flags=re.MULTILINE,
    )
    assert replacements == 2
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
    ):
        result = run_script(PREPARER, tmp_path, component, "9.8.7-rc.2")
        assert result.returncode == 0, result.stderr
        check_result = run_script(CHECKER, tmp_path, component, "9.8.7-rc.2")
        assert check_result.returncode == 0, check_result.stderr

    harness_manifest = (tmp_path / "packages/a13n-harness/pyproject.toml").read_text()
    protocol_manifest = (tmp_path / "packages/a13n-stream-protocol/pyproject.toml").read_text()
    ui_manifest = (tmp_path / "packages/a13n-harness-ui/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in harness_manifest
    assert '"a13n-harness==9.8.7rc2"' in protocol_manifest
    assert 'version = "9.8.7rc2"' in ui_manifest
    assert '"a13n-harness[docker,e2b,modal]>=3.2.1rc4,<4.0.0"' in ui_manifest
    assert '"a13n-stream-protocol>=3.2.1rc4,<4.0.0"' in ui_manifest
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/a13n-logging/pyproject.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "pyproject.toml").read_text()
    assert 'version = "9.8.7-rc.2"' in (tmp_path / "Cargo.toml").read_text()
    assert 'version = "9.8.7rc2"' in (tmp_path / "packages/a13n-envd-client/pyproject.toml").read_text()


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


def test_current_release_sequence_preserves_consumer_requirements(tmp_path: Path) -> None:
    import tomllib

    copy_release_files(tmp_path)
    releases = (
        ("a13n-envd", "0.1.0"),
        ("a13n-logging", "0.2.0"),
        ("a13n-harness", "0.3.0"),
        ("a13n-harness-ui", "0.3.0"),
    )
    for component, version in releases:
        prepared = run_script(PREPARER, tmp_path, component, version)
        assert prepared.returncode == 0, prepared.stderr
    for component, version in releases:
        checked = run_script(CHECKER, tmp_path, component, version)
        assert checked.returncode == 0, checked.stderr

    for package in ("a13n-harness", "a13n-harness-ui"):
        manifest = tomllib.loads((tmp_path / f"packages/{package}/pyproject.toml").read_text())
        assert "a13n-envd-client>=0.1.0,<0.2.0" in manifest["project"]["dependencies"]
        assert "a13n-logging>=0.2.0,<0.3.0" in manifest["project"]["dependencies"]
    service = tomllib.loads((tmp_path / "packages/a13n-service/pyproject.toml").read_text())
    assert service["project"]["version"] == "0.0.0"
    assert "a13n-logging" in service["project"]["dependencies"]


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
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        lock_path.read_text().replace('name = "a13n-service"', 'name = "missing-service"'),
        encoding="utf-8",
    )
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "a13n-service", "9.8.7")

    assert result.returncode != 0
    assert "package a13n-service" in result.stderr
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


def test_mismatched_ui_ranges_are_blocked_without_writes(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    manifest = tmp_path / "packages/a13n-harness-ui/pyproject.toml"
    manifest.write_text(re.sub(r'a13n-harness = "[^"]+"', 'a13n-harness = ">=9.0.0,<10.0.0"', manifest.read_text()))
    before = snapshot(tmp_path)
    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", "9.8.7")
    assert result.returncode != 0
    assert "same compatible range" in result.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("component", "manifest", "dependency", "constraint"),
    [
        ("a13n-harness", "a13n-harness", "a13n-envd-client", ">=0.1.0,<0.2.0"),
        ("a13n-harness", "a13n-harness", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-harness-ui", "a13n-harness-ui", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-service", "a13n-service", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-harness-ui", "a13n-harness-ui", "a13n-envd-client", ">=0.1.0,<0.2.0"),
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
    path = tmp_path / "packages/a13n-harness/pyproject.toml"
    path.write_text(path.read_text().replace(">=0.1.0,<0.2.0", constraint))
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
    assert '"a13n-harness[docker,e2b,modal]>=3.2.1,<4.0.0"' in content


@pytest.mark.parametrize("version", ["9.8.7", "9.8.7-rc.2"])
def test_prepared_ui_release_exports_locked_provider_dependencies(tmp_path: Path, version: str) -> None:
    copy_release_files(tmp_path)
    result = run_script(PREPARER, tmp_path, "a13n-harness-ui", version)
    assert result.returncode == 0, result.stderr
    before = snapshot(tmp_path)

    exported = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--offline",
            "--package",
            "a13n-harness-ui",
            "--no-dev",
            "--no-emit-workspace",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert exported.returncode == 0, exported.stderr
    assert snapshot(tmp_path) == before
    for package in ("docker", "e2b", "modal"):
        assert re.search(rf"^{package}==", exported.stdout, re.MULTILINE)


@pytest.mark.parametrize("version", ["9.8.7", "9.8.7-rc.2"])
def test_same_group_pins_preserve_extras(tmp_path: Path, version: str) -> None:
    copy_release_files(tmp_path)
    manifest = tmp_path / "packages/a13n-stream-protocol/pyproject.toml"
    manifest.write_text(manifest.read_text().replace('"a13n-harness",', '"a13n-harness[docker]",'))

    result = run_script(PREPARER, tmp_path, "a13n-harness", version)

    assert result.returncode == 0, result.stderr
    python_version = version.replace("-rc.", "rc")
    assert f'"a13n-harness[docker]=={python_version}"' in manifest.read_text()
    checked = run_script(CHECKER, tmp_path, "a13n-harness", version)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize(
    ("component", "version", "requirement"),
    [
        ("a13n-service", "0.1.0", ">=0.6.0,<0.7.0"),
        ("a13n-harness-ui", "0.8.0", ">=0.8.0,<0.9.0"),
    ],
)
def test_consumer_contract_line_is_injected_without_changing_source_versions(tmp_path, component, version, requirement):
    import tomllib

    copy_release_files(tmp_path)
    source_path = tmp_path / f"packages/{component}/pyproject.toml"
    source = tomllib.loads(source_path.read_text())
    assert source["project"]["version"] == "0.0.0"
    assert source["tool"]["a13n"]["release-dependencies"]["a13n-harness"] == requirement
    source_requirement = next(
        item for item in source["project"]["dependencies"] if item.split("[")[0] == "a13n-harness"
    )
    assert ">=" not in source_requirement
    result = run_script(PREPARER, tmp_path, component, version)
    assert result.returncode == 0, result.stderr
    prepared = tomllib.loads(source_path.read_text())
    assert source_requirement + requirement in prepared["project"]["dependencies"]
    assert "a13n-stream-protocol" + requirement in prepared["project"]["dependencies"]
    checked = run_script(CHECKER, tmp_path, component, version)
    assert checked.returncode == 0, checked.stderr
