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
    Path("release-dependencies.toml"),
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


@pytest.mark.parametrize(
    "manifest",
    [
        "packages/a13n-harness/pyproject.toml",
        "packages/a13n-stream-protocol/pyproject.toml",
        "packages/a13n-harness-ui/pyproject.toml",
        "packages/a13n-service/pyproject.toml",
        "examples/agent-app/pyproject.toml",
        "examples/plugins/pyproject.toml",
    ],
)
def test_pydantic_ai_dependencies_pin_the_reviewed_minor_line(manifest: str) -> None:
    import tomllib

    project = tomllib.loads((REPOSITORY_ROOT / manifest).read_text())["project"]
    requirements = [
        requirement
        for requirement in project["dependencies"]
        if re.match(r"pydantic-ai(?:-slim)?(?:\[|[<>=!~;\s]|$)", requirement)
    ]
    assert len(requirements) == 1
    assert re.fullmatch(r"pydantic-ai(?:-slim)?(?:\[[^\]]+\])?==2\.76\.\*", requirements[0])


def copy_release_files(destination: Path) -> None:
    for relative_path in RELEASE_FILES:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, target)


def _replace_release_dependency(root: Path, component: str, dependency: str, constraint: str) -> None:
    path = root / "release-dependencies.toml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    active_table: str | None = None
    replacements = 0
    pattern = re.compile(rf'^({re.escape(dependency)}\s*=\s*")[^"]*(".*?)(\r?\n)?$')
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            active_table = stripped[1:-1]
            continue
        if active_table != component:
            continue
        match = pattern.fullmatch(line)
        if match is not None:
            lines[index] = f"{match.group(1)}{constraint}{match.group(2)}{match.group(3) or ''}"
            replacements += 1
    assert replacements == 1
    path.write_text("".join(lines), encoding="utf-8")


def select_harness_pin(root: Path, component: str, constraint: str = "==3.2.1") -> None:
    for dependency in ("a13n-harness", "a13n-stream-protocol"):
        _replace_release_dependency(root, component, dependency, constraint)


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
        select_harness_pin(tmp_path, "a13n-harness-ui")
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
    for component in ("a13n-harness-ui", "a13n-service"):
        select_harness_pin(tmp_path, component, "==3.2.1rc4")

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
    assert '"a13n-harness[docker,e2b,modal]==3.2.1rc4"' in ui_manifest
    assert '"a13n-stream-protocol==3.2.1rc4"' in ui_manifest
    service_manifest = (tmp_path / "packages/a13n-service/pyproject.toml").read_text()
    assert '"a13n-harness[boxd,e2b,kubernetes]==3.2.1rc4"' in service_manifest
    assert '"a13n-stream-protocol==3.2.1rc4"' in service_manifest
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


def test_release_sequence_preserves_consumer_requirements(tmp_path: Path) -> None:
    import tomllib

    from packaging.requirements import Requirement

    copy_release_files(tmp_path)
    harness_version = "3.2.1"
    for component in ("a13n-harness-ui", "a13n-service"):
        select_harness_pin(tmp_path, component, f"=={harness_version}")
    releases = (
        ("a13n-envd", "0.8.3"),
        ("a13n-logging", "0.2.0"),
        ("a13n-harness", harness_version),
        ("a13n-harness-ui", "3.2.4"),
        ("a13n-service", "1.2.3"),
    )
    for component, version in releases:
        prepared = run_script(PREPARER, tmp_path, component, version)
        assert prepared.returncode == 0, prepared.stderr
    for component, version in releases:
        checked = run_script(CHECKER, tmp_path, component, version)
        assert checked.returncode == 0, checked.stderr

    released_versions = dict(releases)
    released_versions["a13n-envd-client"] = released_versions["a13n-envd"]
    released_versions["a13n-stream-protocol"] = released_versions["a13n-harness"]
    for package, envd_requirement in (
        ("a13n-harness", ">=0.8.3,<0.9.0"),
        ("a13n-harness-ui", ">=0.8.3,<0.9.0"),
        ("a13n-service", ">=0.8.3,<0.9.0"),
    ):
        manifest = tomllib.loads((tmp_path / f"packages/{package}/pyproject.toml").read_text())
        assert f"a13n-envd-client{envd_requirement}" in manifest["project"]["dependencies"]
        assert "a13n-logging>=0.2.0,<0.3.0" in manifest["project"]["dependencies"]
        for dependency in manifest["project"]["dependencies"]:
            requirement = Requirement(dependency)
            if requirement.name in released_versions:
                assert released_versions[requirement.name] in requirement.specifier, dependency
        if package in {"a13n-harness-ui", "a13n-service"}:
            harness = next(dep for dep in manifest["project"]["dependencies"] if dep.startswith("a13n-harness"))
            pin = f"=={released_versions['a13n-harness']}"
            assert harness.endswith(pin)
            assert f"a13n-stream-protocol{pin}" in manifest["project"]["dependencies"]


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
    select_harness_pin(tmp_path, "a13n-harness-ui")

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
    "constraint",
    [
        "0.0.0",
        ">=0.0.0,<0.1.0",
        ">=0.0.5",
        "<0.1.0",
        "==0.0.0",
        "==0.19.*",
        "==0.19.1-rc.1",
        "==0.19.1.dev1",
        ">=0.1.0,<0.2.0",
    ],
)
@pytest.mark.parametrize("component", ["a13n-harness-ui", "a13n-service"])
def test_consumer_release_requires_exact_pin_without_writing(tmp_path: Path, constraint: str, component: str) -> None:
    copy_release_files(tmp_path)
    select_harness_pin(tmp_path, component, constraint)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, component, "9.8.7")

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


@pytest.mark.parametrize("component", ["a13n-harness-ui", "a13n-service"])
def test_checker_rejects_consumer_dependency_drift(tmp_path: Path, component: str) -> None:
    copy_release_files(tmp_path)
    select_harness_pin(tmp_path, component)
    prepare_result = run_script(PREPARER, tmp_path, component, "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    manifest = tmp_path / f"packages/{component}/pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"a13n-stream-protocol==3.2.1"',
            '"a13n-stream-protocol==3.2.2"',
        ),
        encoding="utf-8",
    )

    result = run_script(CHECKER, tmp_path, component, "9.8.7")

    assert result.returncode != 0
    assert "dependency a13n-stream-protocol==3.2.1" in result.stderr


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


@pytest.mark.parametrize("component", ["a13n-harness-ui", "a13n-service"])
def test_mismatched_consumer_pins_are_blocked_without_writes(tmp_path: Path, component: str) -> None:
    copy_release_files(tmp_path)
    _replace_release_dependency(tmp_path, component, "a13n-harness", "==9.0.0")
    before = snapshot(tmp_path)
    result = run_script(PREPARER, tmp_path, component, "9.8.7")
    assert result.returncode != 0
    assert "same requirement" in result.stderr
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("component", "manifest", "dependency", "constraint"),
    [
        ("a13n-harness", "a13n-harness", "a13n-envd-client", ">=0.8.3,<0.9.0"),
        ("a13n-harness", "a13n-harness", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-harness-ui", "a13n-harness-ui", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-service", "a13n-service", "a13n-logging", ">=0.2.0,<0.3.0"),
        ("a13n-service", "a13n-service", "a13n-envd-client", ">=0.8.3,<0.9.0"),
        ("a13n-harness-ui", "a13n-harness-ui", "a13n-envd-client", ">=0.8.3,<0.9.0"),
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
    _replace_release_dependency(tmp_path, "a13n-harness", "a13n-envd-client", constraint)
    before = snapshot(tmp_path)
    result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")
    assert result.returncode != 0
    assert snapshot(tmp_path) == before


def test_range_order_is_normalized_without_changing_policy(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    _replace_release_dependency(tmp_path, "a13n-harness", "a13n-envd-client", "<0.9.0,>=0.8.3")
    path = tmp_path / "packages/a13n-harness/pyproject.toml"
    result = run_script(PREPARER, tmp_path, "a13n-harness", "9.8.7")
    assert result.returncode == 0, result.stderr
    assert '"a13n-envd-client>=0.8.3,<0.9.0"' in path.read_text()


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


@pytest.mark.parametrize("component", ["a13n-service", "a13n-harness-ui"])
def test_consumer_contract_line_is_injected_without_changing_source_versions(tmp_path: Path, component: str) -> None:
    import tomllib

    copy_release_files(tmp_path)
    source_path = tmp_path / f"packages/{component}/pyproject.toml"
    source = tomllib.loads(source_path.read_text())
    assert source["project"]["version"] == "0.0.0"
    assert "release-dependencies" not in source.get("tool", {}).get("a13n", {})
    release_requirements = tomllib.loads((tmp_path / "release-dependencies.toml").read_text())[component]
    requirement = release_requirements["a13n-harness"]
    assert requirement.startswith("==")
    assert release_requirements["a13n-stream-protocol"] == requirement
    version = "1.2.3"
    source_requirement = next(
        item for item in source["project"]["dependencies"] if item.split("[")[0] == "a13n-harness"
    )
    assert ">=" not in source_requirement
    assert "==" not in source_requirement
    result = run_script(PREPARER, tmp_path, component, version)
    assert result.returncode == 0, result.stderr
    prepared = tomllib.loads(source_path.read_text())
    assert source_requirement + requirement in prepared["project"]["dependencies"]
    assert "a13n-stream-protocol" + requirement in prepared["project"]["dependencies"]
    checked = run_script(CHECKER, tmp_path, component, version)
    assert checked.returncode == 0, checked.stderr


def test_release_dependencies_rejects_unexpected_tables_and_keys() -> None:
    from scripts.release_version import ReleaseVersionError, parse_release_dependencies

    base = (REPOSITORY_ROOT / "release-dependencies.toml").read_text(encoding="utf-8")
    assert set(parse_release_dependencies(base)) == {"a13n-harness", "a13n-harness-ui", "a13n-service"}

    with pytest.raises(ReleaseVersionError, match="tables"):
        parse_release_dependencies(base + '\n[extra]\na13n-logging = ">=0.2.0,<0.3.0"\n')

    with pytest.raises(ReleaseVersionError, match="keys"):
        parse_release_dependencies(base.replace('a13n-logging = ">=0.2.0,<0.3.0"\n', "", 1))


def _init_pin_repo(tmp_path: Path, *, with_initial_file: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-b", "main")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    if with_initial_file:
        shutil.copy2(REPOSITORY_ROOT / "release-dependencies.toml", repo / "release-dependencies.toml")
        select_harness_pin(repo, "a13n-harness-ui", "==0.43.1")
        select_harness_pin(repo, "a13n-service", "==0.43.1")
    git("add", ".")
    git("commit", "-m", "base")
    return repo


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        text=True,
    ).strip()


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ({}, ()),
        ({"a13n-harness-ui": "==0.43.2"}, ("a13n-harness-ui",)),
        ({"a13n-service": "==0.44.0"}, ("a13n-service",)),
        ({"a13n-harness-ui": "==1.0.0", "a13n-service": "==1.0.0"}, ("a13n-harness-ui", "a13n-service")),
        ({"a13n-harness-ui": "==0.44.0rc1", "a13n-service": "==0.44.0rc1"}, ("a13n-harness-ui", "a13n-service")),
    ],
)
def test_changed_harness_pin_consumers_supports_patch_minor_major_and_rc(
    tmp_path: Path,
    edits: dict[str, str],
    expected: tuple[str, ...],
) -> None:
    from scripts.release_version import changed_harness_pin_consumers

    repo = _init_pin_repo(tmp_path)
    base_sha = _git_output(repo, "rev-parse", "HEAD")
    _git_output(repo, "checkout", "-b", "bump")
    for consumer, pin in edits.items():
        select_harness_pin(repo, consumer, pin)
    if not edits:
        _replace_release_dependency(repo, "a13n-harness", "a13n-envd-client", ">=0.8.4,<0.9.0")
    _git_output(repo, "add", ".")
    _git_output(repo, "commit", "-m", "update release dependencies")

    assert changed_harness_pin_consumers(repo, event_name="push", before_push=base_sha) == expected
    assert changed_harness_pin_consumers(repo, event_name="workflow_dispatch") == ()

    _git_output(repo, "checkout", "main")
    _git_output(repo, "merge", "--no-ff", "bump", "-m", "merge bump")
    assert changed_harness_pin_consumers(repo, event_name="pull_request") == expected


def test_changed_harness_pin_consumers_handles_initial_file_and_invalid_pins(tmp_path: Path) -> None:
    from scripts.release_version import ReleaseVersionError, changed_harness_pin_consumers

    repo = _init_pin_repo(tmp_path, with_initial_file=False)
    base_sha = _git_output(repo, "rev-parse", "HEAD")
    shutil.copy2(REPOSITORY_ROOT / "release-dependencies.toml", repo / "release-dependencies.toml")
    _git_output(repo, "add", ".")
    _git_output(repo, "commit", "-m", "add release-dependencies.toml")

    assert changed_harness_pin_consumers(repo, event_name="push", before_push=base_sha) == (
        "a13n-harness-ui",
        "a13n-service",
    )

    second_base = _git_output(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
    _git_output(repo, "add", ".")
    _git_output(repo, "commit", "-m", "unrelated")
    assert changed_harness_pin_consumers(repo, event_name="push", before_push=second_base) == ()

    third_base = _git_output(repo, "rev-parse", "HEAD")
    _replace_release_dependency(repo, "a13n-service", "a13n-harness", "==9.9.9")
    _git_output(repo, "add", ".")
    _git_output(repo, "commit", "-m", "mismatched pin")
    with pytest.raises(ReleaseVersionError, match="same requirement"):
        changed_harness_pin_consumers(repo, event_name="push", before_push=third_base)
