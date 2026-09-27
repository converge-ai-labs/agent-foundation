from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import check_a13n_harness_ui_distribution as checker  # noqa: E402
from check_a13n_harness_ui_distribution import (  # noqa: E402
    INTERNAL_PACKAGES,
    TERMINAL_PACKAGE_PATHS,
    DistributionError,
    validate_sdist,
    validate_wheel,
)

type Artifact = tuple[Path, Callable[..., None]]
COSS_LICENSE = (SCRIPTS_DIRECTORY.parent / "frontend/packages/a13n-ui/LICENSE.coss").read_bytes()

HARNESS_RANGE = ">=0.0.5,<0.1.0"
LOGGING_RANGE = ">=0.2.3,<0.3.0"
RELEASE_REQUIREMENTS = [
    *(f"{name}{HARNESS_RANGE}" for name in INTERNAL_PACKAGES),
    f"a13n-logging{LOGGING_RANGE}",
]


def _write_wheel(
    path: Path,
    *,
    index: bytes = b'<script src="/assets/main.js"></script>',
    requirements: list[str] | None = None,
    include_terminal_shell: bool = True,
    include_license: bool = True,
    coss_license: bytes | None = COSS_LICENSE,
    omitted_package_file: str | None = None,
    include_entrypoint: bool = True,
    cli_content: bytes = b"def main(): pass\n",
    extra_packaged_files: dict[str, bytes] | None = None,
    omitted_skill_file: str | None = None,
) -> None:
    files = {
        "index.html": index,
        "assets/main.css": b"body { color: black; }\n",
        "assets/main.js": b"console.log('a13n-harness-ui')\n",
    }
    if coss_license is not None:
        files["assets/LICENSE.coss"] = coss_license
    manifest = {
        "schema_version": "1",
        "source": "frontend/apps/a13n-harness-ui",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, content in files.items():
            archive.writestr(f"a13n_harness_ui/static/{name}", content)
        archive.writestr(
            "a13n_harness_ui/static/asset-manifest.json",
            json.dumps(manifest),
        )
        if include_license:
            archive.writestr(
                "a13n_harness_ui-9.8.7.dist-info/licenses/LICENSE",
                (SCRIPTS_DIRECTORY.parent / "packages/a13n-harness-ui/LICENSE").read_bytes(),
            )
        for module in TERMINAL_PACKAGE_PATHS:
            if module.as_posix() == "a13n_harness_ui/interactive/shell.py" and not include_terminal_shell:
                continue
            if module.name == omitted_package_file:
                continue
            content = b"\n"
            if module.as_posix().startswith("a13n_harness_ui/subagents/") or module.as_posix() in {
                "a13n_harness_ui/prompts.py",
                "a13n_harness_ui/assets/system_prompt.md",
            }:
                content = (SCRIPTS_DIRECTORY.parent / "packages/a13n-harness-ui" / module).read_bytes()
            elif module.as_posix() == "a13n_harness_ui/interactive/shell.py":
                content = b"class CliShell: pass\n"
            elif module.as_posix() == "a13n_harness_ui/webui.py":
                content = b"def create_webui(): pass\n"
            elif module.as_posix() == "a13n_harness_ui/cli.py":
                content = cli_content
            archive.writestr(module.as_posix(), content)
        skill_files = {
            "SKILL.md": b"---\nname: harness-ui-configuration\ndescription: Configure Harness UI.\n---\n\n"
            b"## Documentation map\n\n- [Configuration](docs/configuration.md)\n",
            "references/navigation.md": b"# Navigation\n\nFile: `docs/configuration.md`\n",
            "docs/configuration.md": b"# Configuration\n",
        }
        for relative, content in skill_files.items():
            if relative != omitted_skill_file:
                archive.writestr(str(checker.SKILL_PREFIX / relative), content)
        archive.writestr("a13n_harness_ui/interactive/__init__.py", b"\n")
        if include_entrypoint:
            archive.writestr(
                "a13n_harness_ui-9.8.7.dist-info/entry_points.txt",
                "[console_scripts]\na13n-harness-ui = a13n_harness_ui.cli:main\n",
            )
        archive.writestr(
            "a13n_harness_ui-9.8.7.dist-info/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.4",
                    "Name: a13n-harness-ui",
                    "Version: 9.8.7",
                    *(
                        f"Requires-Dist: {requirement}"
                        for requirement in (RELEASE_REQUIREMENTS if requirements is None else requirements)
                    ),
                    "",
                )
            ),
        )
        for name, content in (extra_packaged_files or {}).items():
            archive.writestr(f"a13n_harness_ui/static/{name}", content)


def test_validates_cli_distribution(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
    )

    assert validate_wheel(wheel, require_compatible_dependencies=True) == {
        **dict.fromkeys(INTERNAL_PACKAGES, HARNESS_RANGE),
        "a13n-logging": LOGGING_RANGE,
    }


def test_rejects_missing_project_license(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, include_license=False)

    with pytest.raises(DistributionError, match="missing the project license"):
        validate_wheel(wheel)


def test_development_validation_allows_unversioned_workspace_dependencies(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        requirements=[*INTERNAL_PACKAGES, "a13n-logging"],
    )

    validate_wheel(wheel)


def test_rejects_missing_managed_runtime(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        omitted_package_file="managed_runtime.py",
    )

    with pytest.raises(DistributionError, match=r"missing a13n_harness_ui/managed_runtime\.py"):
        validate_wheel(wheel)


def test_rejects_missing_console_entrypoint(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        include_entrypoint=False,
    )

    with pytest.raises(DistributionError, match=r"Expected one entry_points\.txt"):
        validate_wheel(wheel)


def test_rejects_unimportable_entrypoint(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        cli_content=b"raise RuntimeError('broken wheel')\n",
    )

    with pytest.raises(DistributionError, match="cannot import its entrypoint and CLI"):
        validate_wheel(wheel)


def test_rejects_missing_terminal_shell(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        include_terminal_shell=False,
    )

    with pytest.raises(DistributionError, match=r"missing a13n_harness_ui/interactive/shell\.py"):
        validate_wheel(wheel)


@pytest.mark.parametrize("name", ["code-reviewer.md", "executor.md", "explorer.md", "system_prompt.md"])
def test_rejects_missing_packaged_prompt(tmp_path: Path, name: str) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, omitted_package_file=name)
    with pytest.raises(DistributionError, match="missing a13n_harness_ui/"):
        validate_wheel(wheel)


def test_rejects_undeclared_shell_reference(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, index=b'<script src="/assets/missing.js"></script>')

    with pytest.raises(DistributionError, match="shell references an undeclared or missing asset"):
        validate_wheel(wheel, require_compatible_dependencies=True)


def test_rejects_packaged_file_missing_from_manifest(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        extra_packaged_files={"assets/undeclared.js": b"unexpected\n"},
    )

    with pytest.raises(DistributionError, match="manifest does not match packaged files"):
        validate_wheel(wheel, require_compatible_dependencies=True)


def _write_sdist(
    path: Path,
    *,
    requirements: list[str] | None = None,
    omitted_skill_file: str | None = None,
    coss_license: bytes | None = COSS_LICENSE,
) -> None:
    wheel = path.with_suffix(".whl")
    _write_wheel(wheel, requirements=requirements, omitted_skill_file=omitted_skill_file, coss_license=coss_license)
    root = "a13n_harness_ui-9.8.7"
    dependencies = RELEASE_REQUIREMENTS if requirements is None else requirements
    pyproject = (
        '[project]\nname = "a13n-harness-ui"\nversion = "9.8.7"\n'
        f"dependencies = {json.dumps(dependencies)}\n"
        '[project.scripts]\na13n-harness-ui = "a13n_harness_ui.cli:main"\n'
    )
    with zipfile.ZipFile(wheel) as source, tarfile.open(path, "w:gz") as archive:
        files = {name: source.read(name) for name in source.namelist() if name.startswith("a13n_harness_ui/")}
        files["pyproject.toml"] = pyproject.encode()
        files["LICENSE"] = source.read("a13n_harness_ui-9.8.7.dist-info/licenses/LICENSE")
        for name, content in files.items():
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


@pytest.fixture(params=["wheel", "sdist"])
def artifact(request: pytest.FixtureRequest, tmp_path: Path) -> Artifact:
    if request.param == "wheel":
        return tmp_path / "a13n_harness_ui-9.8.7-py3-none-any.whl", _write_wheel
    return tmp_path / "a13n_harness_ui-9.8.7.tar.gz", _write_sdist


@pytest.mark.parametrize("missing", ["SKILL.md", "references/navigation.md", "docs/configuration.md"])
def test_rejects_incomplete_configuration_skill(artifact: Artifact, missing: str) -> None:
    path, write = artifact
    write(path, omitted_skill_file=missing)
    with pytest.raises(DistributionError, match="missing"):
        _validate_release_artifact(path)


@pytest.mark.parametrize("license_text", [None, b"MIT License\n"])
def test_rejects_missing_or_altered_coss_license(artifact: Artifact, license_text: bytes | None) -> None:
    path, write = artifact
    write(path, coss_license=license_text)
    with pytest.raises(DistributionError, match="missing or altered Coss UI license"):
        _validate_release_artifact(path)


def _validate_release_artifact(path: Path) -> dict[str, str] | None:
    if path.suffix == ".whl":
        return validate_wheel(path, require_compatible_dependencies=True)
    return validate_sdist(path, require_compatible_dependencies=True)[1]


@pytest.mark.parametrize("dependency_range", [HARNESS_RANGE, ">=0.0.5rc1,<0.1.0"])
def test_normalizes_compatible_ranges_and_allows_independent_logging_range(
    artifact: Artifact, dependency_range: str
) -> None:
    path, write = artifact
    lower, upper = dependency_range.split(",")
    write(
        path,
        requirements=[
            f"a13n-harness{upper},{lower}",
            f"a13n-stream-protocol{dependency_range}",
            "a13n-logging<0.3.0,>=0.2.3",
        ],
    )

    assert _validate_release_artifact(path) == {
        **dict.fromkeys(INTERNAL_PACKAGES, dependency_range),
        "a13n-logging": LOGGING_RANGE,
    }


@pytest.mark.parametrize("package", INTERNAL_PACKAGES)
@pytest.mark.parametrize("dependency_range", [">=0.0.6,<0.1.0", ">=0.0.5,<0.2.0"])
def test_rejects_mismatched_internal_dependency_ranges(artifact: Artifact, package: str, dependency_range: str) -> None:
    path, write = artifact
    write(
        path,
        requirements=[
            f"{package}{dependency_range}" if value == f"{package}{HARNESS_RANGE}" else value
            for value in RELEASE_REQUIREMENTS
        ],
    )

    with pytest.raises(DistributionError, match="internal dependency ranges do not match"):
        _validate_release_artifact(path)


@pytest.mark.parametrize("package", [*INTERNAL_PACKAGES, "a13n-logging"])
@pytest.mark.parametrize(
    "dependency_range",
    ["", "==0.0.5", ">=0.0.5", "<0.1.0", ">=0.0.0,<0.1.0", ">=0.1.0,<0.1.0", ">=0.2.0,<0.1.0"],
)
def test_rejects_invalid_dependency_ranges(artifact: Artifact, package: str, dependency_range: str) -> None:
    path, write = artifact
    write(
        path,
        requirements=[value for value in RELEASE_REQUIREMENTS if not value.startswith(f"{package}>")]
        + [f"{package}{dependency_range}"],
    )

    with pytest.raises(DistributionError, match=f"compatible dependency range for {package}"):
        _validate_release_artifact(path)


@pytest.mark.parametrize("package", [*INTERNAL_PACKAGES, "a13n-logging"])
@pytest.mark.parametrize("count", [0, 2])
def test_rejects_missing_or_duplicate_dependencies(artifact: Artifact, package: str, count: int) -> None:
    path, write = artifact
    write(
        path,
        requirements=[value for value in RELEASE_REQUIREMENTS if not value.startswith(f"{package}>")]
        + [f"{package}{HARNESS_RANGE}"] * count,
    )

    with pytest.raises(DistributionError, match=f"Expected one {package} requirement"):
        _validate_release_artifact(path)


def test_sdist_development_validation_allows_unversioned_dependencies(tmp_path: Path) -> None:
    sdist = tmp_path / "a13n_harness_ui-9.8.7.tar.gz"
    _write_sdist(sdist, requirements=[*INTERNAL_PACKAGES, "a13n-logging"])

    assert validate_sdist(sdist) == ("a13n_harness_ui-9.8.7", None)


@pytest.mark.parametrize("mismatched", [None, "harness", "logging"])
def test_main_compares_normalized_wheel_and_sdist_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatched: str | None
) -> None:
    sdist = tmp_path / "a13n_harness_ui-9.8.7.tar.gz"
    _write_sdist(sdist)
    wheel = tmp_path / "a13n_harness_ui-9.8.7-py3-none-any.whl"
    dependency_range = "<0.2.0,>=0.0.5" if mismatched == "harness" else "<0.1.0,>=0.0.5"
    logging_range = "<0.4.0,>=0.2.3" if mismatched == "logging" else "<0.3.0,>=0.2.3"
    _write_wheel(
        wheel,
        requirements=[*(f"{name}{dependency_range}" for name in INTERNAL_PACKAGES), f"a13n-logging{logging_range}"],
    )
    # The sdist fixture's source wheel is not a release artifact.
    sdist.with_suffix(".whl").unlink()
    monkeypatch.setattr(sys, "argv", ["checker", str(tmp_path), "--require-compatible-dependencies"])

    if mismatched:
        with pytest.raises(SystemExit, match="wheel and sdist dependency ranges do not match"):
            checker.main()
    else:
        checker.main()


@pytest.mark.parametrize("mismatched", [None, "harness", "logging"])
def test_rebuild_compares_normalized_dependency_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatched: str | None
) -> None:
    sdist = tmp_path / "a13n_harness_ui-9.8.7.tar.gz"
    _write_sdist(sdist)
    monkeypatch.setattr(checker.shutil, "which", lambda name: "/tools/uv")
    monkeypatch.setattr(checker, "_validate_wheel_imports", lambda path: None)
    dependency_range = "<0.2.0,>=0.0.5" if mismatched == "harness" else "<0.1.0,>=0.0.5"
    logging_range = "<0.4.0,>=0.2.3" if mismatched == "logging" else "<0.3.0,>=0.2.3"

    def build(command: list[str], **kwargs: object) -> SimpleNamespace:
        output = Path(command[command.index("--out-dir") + 1])
        output.mkdir()
        _write_wheel(
            output / "a13n_harness_ui-9.8.7-py3-none-any.whl",
            requirements=[
                *(f"{name}{dependency_range}" for name in INTERNAL_PACKAGES),
                f"a13n-logging{logging_range}",
            ],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checker.subprocess, "run", build)
    if mismatched:
        with pytest.raises(DistributionError, match="rebuilt wheel and sdist dependency ranges do not match"):
            checker.rebuild_wheel_from_sdist(sdist, require_compatible_dependencies=True)
    else:
        rebuilt = checker.rebuild_wheel_from_sdist(sdist, require_compatible_dependencies=True)
        assert rebuilt.is_file()
