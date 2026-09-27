from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from email.policy import default
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import ClassVar
from urllib.parse import unquote, urlsplit

from release_version import ReleaseVersionError, validate_dependency_range

DISTRIBUTION_STEM = "a13n_harness_ui"
MANIFEST_NAME = "asset-manifest.json"
PACKAGE_PREFIX = PurePosixPath("a13n_harness_ui/static")
SKILL_PREFIX = PurePosixPath("a13n_harness_ui/assets/builtin_skills/harness-ui-configuration")
TERMINAL_PACKAGE_PATHS = (
    PurePosixPath("a13n_harness_ui/__init__.py"),
    PurePosixPath("a13n_harness_ui/__main__.py"),
    PurePosixPath("a13n_harness_ui/cli.py"),
    PurePosixPath("a13n_harness_ui/webui.py"),
    PurePosixPath("a13n_harness_ui/terminal.py"),
    PurePosixPath("a13n_harness_ui/cli_runtime.py"),
    PurePosixPath("a13n_harness_ui/managed_runtime.py"),
    PurePosixPath("a13n_harness_ui/prompts.py"),
    PurePosixPath("a13n_harness_ui/assets/system_prompt.md"),
    PurePosixPath("a13n_harness_ui/interactive/shell.py"),
    PurePosixPath("a13n_harness_ui/interactive/backend.py"),
    PurePosixPath("a13n_harness_ui/interactive/commands.py"),
    PurePosixPath("a13n_harness_ui/interactive/rendering.py"),
    PurePosixPath("a13n_harness_ui/interactive/setup.py"),
    PurePosixPath("a13n_harness_ui/interactive/runtime.py"),
    PurePosixPath("a13n_harness_ui/interactive/transcript.py"),
    PurePosixPath("a13n_harness_ui/interactive/markdown.py"),
    PurePosixPath("a13n_harness_ui/interactive/attachments.py"),
    PurePosixPath("a13n_harness_ui/interactive/decisions.py"),
    PurePosixPath("a13n_harness_ui/interactive/theme.py"),
    PurePosixPath("a13n_harness_ui/subagents/__init__.py"),
    PurePosixPath("a13n_harness_ui/subagents/code-reviewer.md"),
    PurePosixPath("a13n_harness_ui/subagents/executor.md"),
    PurePosixPath("a13n_harness_ui/subagents/explorer.md"),
)
INTERNAL_PACKAGES = (
    "a13n-harness",
    "a13n-stream-protocol",
)


class DistributionError(ValueError):
    pass


class _AssetReferenceParser(HTMLParser):
    _ATTRIBUTES: ClassVar[dict[str, str]] = {
        "img": "src",
        "link": "href",
        "script": "src",
        "source": "src",
    }

    def __init__(self) -> None:
        super().__init__()
        self.references: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute_name = self._ATTRIBUTES.get(tag)
        if attribute_name is None:
            return
        attributes = dict(attrs)
        value = attributes.get(attribute_name)
        if value:
            self.references.add(value)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _local_asset_reference(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path).lstrip("/")
    if not path:
        return None
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise DistributionError(f"Harness UI shell contains an unsafe asset reference: {value}")
    return normalized.as_posix()


def _validate_assets(read: Callable[[str], bytes], names: set[str], prefix: PurePosixPath) -> None:
    manifest_path = str(prefix / MANIFEST_NAME)
    index_path = str(prefix / "index.html")
    if index_path not in names or manifest_path not in names:
        raise DistributionError(f"Harness UI artifact is missing {index_path} or {manifest_path}")

    try:
        manifest = json.loads(read(manifest_path))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DistributionError(f"Invalid Harness UI asset manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1":
        raise DistributionError("Unsupported Harness UI asset manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or "index.html" not in files:
        raise DistributionError("Harness UI asset manifest does not describe index.html")

    declared_assets: set[str] = set()
    for relative_name, expected_digest in files.items():
        if not isinstance(relative_name, str) or not isinstance(expected_digest, str):
            raise DistributionError("Harness UI asset manifest contains an invalid entry")
        relative_path = PurePosixPath(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise DistributionError(f"Harness UI asset manifest contains an unsafe path: {relative_name}")
        normalized_name = relative_path.as_posix()
        declared_assets.add(normalized_name)
        archive_path = str(prefix / relative_path)
        if archive_path not in names:
            raise DistributionError(f"Harness UI artifact is missing declared asset: {archive_path}")
        if _sha256(read(archive_path)) != expected_digest:
            raise DistributionError(f"Harness UI asset digest mismatch: {archive_path}")

    archive_prefix = f"{prefix.as_posix()}/"
    packaged_assets = {
        PurePosixPath(name).relative_to(prefix).as_posix()
        for name in names
        if name.startswith(archive_prefix) and not name.endswith("/") and name != manifest_path
    }
    if packaged_assets != declared_assets:
        undeclared = sorted(packaged_assets - declared_assets)
        missing = sorted(declared_assets - packaged_assets)
        raise DistributionError(
            f"Harness UI asset manifest does not match packaged files; undeclared={undeclared}, missing={missing}"
        )

    license_name = "assets/LICENSE.coss"
    license_source = Path(__file__).resolve().parents[1] / "frontend/packages/a13n-ui/LICENSE.coss"
    if license_name not in declared_assets or read(str(prefix / license_name)) != license_source.read_bytes():
        raise DistributionError("Harness UI artifact has a missing or altered Coss UI license")

    try:
        index = read(index_path).decode("utf-8")
    except UnicodeDecodeError as error:
        raise DistributionError("Harness UI index.html must be UTF-8") from error
    parser = _AssetReferenceParser()
    parser.feed(index)
    for value in parser.references:
        reference = _local_asset_reference(value)
        if reference is not None and reference not in declared_assets:
            raise DistributionError(f"Harness UI shell references an undeclared or missing asset: {value}")


def _validate_builtin_skill(read: Callable[[str], bytes], names: set[str], prefix: PurePosixPath) -> None:
    for relative in ("SKILL.md", "references/navigation.md"):
        if str(prefix / relative) not in names:
            raise DistributionError(f"Harness UI artifact is missing {prefix / relative}")
    skill = read(str(prefix / "SKILL.md")).decode("utf-8")
    documents = re.findall(r"\]\((docs/[^)]+)\)", skill)
    if not documents:
        raise DistributionError("Harness UI configuration Skill has no documentation map")
    for document in documents:
        if str(prefix / document) not in names:
            raise DistributionError(f"Harness UI artifact is missing bundled documentation: {document}")


def _validate_terminal_package(names: set[str], prefix: PurePosixPath | None = None) -> None:
    root = prefix or PurePosixPath()
    for path in TERMINAL_PACKAGE_PATHS:
        artifact_path = (root / path).as_posix()
        if artifact_path not in names:
            raise DistributionError(f"Harness UI artifact is missing {artifact_path}")


def _validate_wheel_imports(path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(path.resolve())
    with tempfile.TemporaryDirectory(prefix="a13n-harness-ui-wheel-import-") as directory:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                (
                    "from a13n_harness_ui.cli import main; "
                    "from a13n_harness_ui.interactive.shell import CliShell; "
                    "from a13n_harness_ui.webui import create_webui; "
                    "from a13n_harness_ui.subagents import builtin_subagent_sources; "
                    "from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT; "
                    "from importlib.resources import files; "
                    "assert DEFAULT_SYSTEM_PROMPT; "
                    "assert DEFAULT_SYSTEM_PROMPT == "
                    "files('a13n_harness_ui').joinpath('assets/system_prompt.md').read_text(encoding='utf-8').strip(); "
                    "assert callable(main) and CliShell and callable(create_webui); "
                    "assert len(builtin_subagent_sources()) == 3; "
                    "assert all(content for _, content in builtin_subagent_sources())"
                ),
            ],
            cwd=directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise DistributionError(f"Harness UI wheel cannot import its entrypoint and CLI:\n{result.stderr}")


def _validate_console_entrypoint(content: bytes) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content.decode("utf-8"))
    except (configparser.Error, UnicodeDecodeError) as error:
        raise DistributionError(f"Invalid Harness UI entry_points.txt: {error}") from error
    if parser.get("console_scripts", "a13n-harness-ui", fallback=None) != "a13n_harness_ui.cli:main":
        raise DistributionError("Harness UI artifact is missing the a13n-harness-ui console entrypoint")


def _validate_internal_requirements(requirements: list[str]) -> dict[str, str]:
    ranges: dict[str, str] = {}
    for package_name in (*INTERNAL_PACKAGES, "a13n-logging"):
        package_pattern = re.compile(rf"^{re.escape(package_name)}(?=$|\s|[<>=!~;@\[])")
        matches = [requirement for requirement in requirements if package_pattern.match(requirement)]
        if len(matches) != 1:
            raise DistributionError(f"Expected one {package_name} requirement in Harness UI metadata, found {matches}")
        try:
            ranges[package_name] = validate_dependency_range(matches[0][len(package_name) :].strip())
        except ReleaseVersionError as error:
            raise DistributionError(
                f"Harness UI metadata must use a compatible dependency range for {package_name}, found {matches[0]}: {error}"
            ) from error
    group_ranges = {ranges[package_name] for package_name in INTERNAL_PACKAGES}
    if len(group_ranges) != 1:
        raise DistributionError(f"Harness UI internal dependency ranges do not match: {sorted(group_ranges)}")
    return ranges


def validate_wheel(path: Path, *, require_compatible_dependencies: bool = False) -> dict[str, str] | None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _validate_assets(archive.read, names, PACKAGE_PREFIX)
        _validate_terminal_package(names)
        _validate_builtin_skill(archive.read, names, SKILL_PREFIX)
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            raise DistributionError("Harness UI wheel is missing the project license")
        entrypoint_paths = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(entrypoint_paths) != 1:
            raise DistributionError(f"Expected one entry_points.txt in {path}, found {len(entrypoint_paths)}")
        _validate_console_entrypoint(archive.read(entrypoint_paths[0]))
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise DistributionError(f"Expected one METADATA file in {path}, found {len(metadata_paths)}")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_paths[0]))
        requirements = metadata.get_all("Requires-Dist", [])
        if not all(isinstance(requirement, str) for requirement in requirements):
            raise DistributionError(f"Invalid Requires-Dist metadata in {path}")
        dependency_ranges = _validate_internal_requirements(requirements) if require_compatible_dependencies else None
    _validate_wheel_imports(path)
    return dependency_ranges


def validate_sdist(path: Path, *, require_compatible_dependencies: bool = False) -> tuple[str, dict[str, str] | None]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise DistributionError("Harness UI sdist must contain exactly one root directory")
        root = roots.pop()
        names = {member.name for member in members}

        def read(name: str) -> bytes:
            file = archive.extractfile(name)
            if file is None:
                raise DistributionError(f"Cannot read Harness UI sdist member: {name}")
            return file.read()

        _validate_assets(read, names, PurePosixPath(root) / PACKAGE_PREFIX)
        _validate_terminal_package(names, PurePosixPath(root))
        _validate_builtin_skill(read, names, PurePosixPath(root) / SKILL_PREFIX)
        if f"{root}/LICENSE" not in names:
            raise DistributionError("Harness UI sdist is missing the project license")
        if any("frontend/apps/a13n-harness-ui" in name for name in names):
            raise DistributionError("Harness UI sdist must not require the Harness UI WebUI source tree")
        pyproject_path = f"{root}/pyproject.toml"
        if pyproject_path not in names:
            raise DistributionError("Harness UI sdist is missing pyproject.toml")
        try:
            project = tomllib.loads(read(pyproject_path).decode("utf-8"))["project"]
            requirements = project["dependencies"]
            scripts = project["scripts"]
        except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise DistributionError(f"Cannot read Harness UI sdist dependencies: {error}") from error
        if not isinstance(requirements, list) or not all(isinstance(value, str) for value in requirements):
            raise DistributionError("Harness UI sdist has invalid project.dependencies")
        if not isinstance(scripts, dict) or scripts.get("a13n-harness-ui") != "a13n_harness_ui.cli:main":
            raise DistributionError("Harness UI sdist is missing the a13n-harness-ui console entrypoint")
        dependency_ranges = _validate_internal_requirements(requirements) if require_compatible_dependencies else None
        return root, dependency_ranges


def rebuild_wheel_from_sdist(sdist: Path, *, require_compatible_dependencies: bool = False) -> Path:
    _, sdist_ranges = validate_sdist(sdist, require_compatible_dependencies=require_compatible_dependencies)
    uv = shutil.which("uv")
    if uv is None:
        raise DistributionError("uv is required to rebuild the Harness UI wheel")

    with tempfile.TemporaryDirectory(prefix="a13n-harness-ui-sdist-") as directory:
        temporary = Path(directory)
        source = temporary / "source"
        output = temporary / "wheel"
        source.mkdir()
        with tarfile.open(sdist, mode="r:gz") as archive:
            archive.extractall(source, filter="data")
        roots = [path for path in source.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise DistributionError("Extracted Harness UI sdist must contain exactly one source directory")
        environment = os.environ.copy()
        environment["PATH"] = str(Path(uv).parent)
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(output), str(roots[0])],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode != 0:
            raise DistributionError(f"Cannot rebuild Harness UI wheel from sdist:\n{result.stdout}{result.stderr}")
        wheels = list(output.glob("*.whl"))
        if len(wheels) != 1:
            raise DistributionError(f"Expected one rebuilt Harness UI wheel, found {len(wheels)}")
        rebuilt = sdist.parent / "rebuilt-from-sdist" / wheels[0].name
        rebuilt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheels[0], rebuilt)
    rebuilt_ranges = validate_wheel(rebuilt, require_compatible_dependencies=require_compatible_dependencies)
    if rebuilt_ranges != sdist_ranges:
        raise DistributionError(
            f"Harness UI rebuilt wheel and sdist dependency ranges do not match: {rebuilt_ranges} != {sdist_ranges}"
        )
    return rebuilt


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a13n-harness-ui wheel and sdist contents.")
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--rebuild-wheel", action="store_true")
    parser.add_argument("--require-compatible-dependencies", action="store_true")
    args = parser.parse_args()

    wheels = sorted(args.dist_dir.glob(f"{DISTRIBUTION_STEM}-*.whl"))
    sdists = sorted(args.dist_dir.glob(f"{DISTRIBUTION_STEM}-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"Expected one a13n-harness-ui wheel and sdist in {args.dist_dir}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    try:
        wheel_ranges = validate_wheel(
            wheels[0],
            require_compatible_dependencies=args.require_compatible_dependencies,
        )
        _, sdist_ranges = validate_sdist(
            sdists[0],
            require_compatible_dependencies=args.require_compatible_dependencies,
        )
        if wheel_ranges != sdist_ranges:
            raise DistributionError(
                f"Harness UI wheel and sdist dependency ranges do not match: {wheel_ranges} != {sdist_ranges}"
            )
        rebuilt = (
            rebuild_wheel_from_sdist(
                sdists[0],
                require_compatible_dependencies=args.require_compatible_dependencies,
            )
            if args.rebuild_wheel
            else None
        )
    except (DistributionError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from error

    print(f"Validated {wheels[0].name} and {sdists[0].name}")
    if rebuilt is not None:
        print(f"Rebuilt and validated {rebuilt}")


if __name__ == "__main__":
    main()
