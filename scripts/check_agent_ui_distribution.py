from __future__ import annotations

import argparse
import configparser
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
from pathlib import Path, PurePosixPath

DISTRIBUTION_STEM = "a13n_ui"
RUNTIME_MANIFEST_PATH = PurePosixPath("a13n_ui/assets/agent-envd-release.json")
TERMINAL_PACKAGE_PATHS = (
    PurePosixPath("a13n_ui/__init__.py"),
    PurePosixPath("a13n_ui/__main__.py"),
    PurePosixPath("a13n_ui/cli.py"),
    PurePosixPath("a13n_ui/terminal.py"),
    PurePosixPath("a13n_ui/cli_runtime.py"),
    PurePosixPath("a13n_ui/interactive/shell.py"),
    PurePosixPath("a13n_ui/interactive/backend.py"),
    PurePosixPath("a13n_ui/interactive/commands.py"),
    PurePosixPath("a13n_ui/interactive/rendering.py"),
    PurePosixPath("a13n_ui/interactive/setup.py"),
    PurePosixPath("a13n_ui/interactive/runtime.py"),
)
INTERNAL_PACKAGES = (
    "a13n-environment-provider",
    "a13n-harness",
    "a13n-stream-protocol",
)


class DistributionError(ValueError):
    pass


def _validate_terminal_package(names: set[str], prefix: PurePosixPath | None = None) -> None:
    root = prefix or PurePosixPath()
    obsolete = tuple((root / path).as_posix() for path in ("a13n_ui/static/", "a13n_ui/tui/", "a13n_ui/webui.py"))
    if any(name.startswith(obsolete) for name in names):
        raise DistributionError("Agent UI artifact contains an obsolete workstation payload")
    for path in TERMINAL_PACKAGE_PATHS:
        artifact_path = (root / path).as_posix()
        if artifact_path not in names:
            raise DistributionError(f"Agent UI artifact is missing {artifact_path}")


def _validate_wheel_imports(path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(path.resolve())
    with tempfile.TemporaryDirectory(prefix="agent-ui-wheel-import-") as directory:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                (
                    "from a13n_ui.cli import main; "
                    "from a13n_ui.interactive.shell import InlineShell; "
                    "assert callable(main) and InlineShell"
                ),
            ],
            cwd=directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise DistributionError(f"Agent UI wheel cannot import its entrypoint and inline CLI:\n{result.stderr}")


def _validate_console_entrypoint(content: bytes) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content.decode("utf-8"))
    except (configparser.Error, UnicodeDecodeError) as error:
        raise DistributionError(f"Invalid Agent UI entry_points.txt: {error}") from error
    if parser.get("console_scripts", "a13n-ui", fallback=None) != "a13n_ui.cli:main":
        raise DistributionError("Agent UI artifact is missing the a13n-ui console entrypoint")


def _validate_runtime_manifest(read: Callable[[str], bytes], names: set[str], path: PurePosixPath) -> None:
    manifest_path = path.as_posix()
    if manifest_path not in names:
        raise DistributionError(f"Agent UI artifact is missing {manifest_path}")
    try:
        manifest = json.loads(read(manifest_path))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DistributionError(f"Invalid agent-envd release manifest: {error}") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "1"
        or not isinstance(manifest.get("release"), str)
        or not isinstance(manifest.get("base_url"), str)
        or not isinstance(manifest.get("targets"), dict)
        or not manifest["targets"]
    ):
        raise DistributionError("Unsupported agent-envd release manifest")


def _validate_internal_requirements(requirements: list[str]) -> str:
    versions: set[str] = set()
    for package_name in INTERNAL_PACKAGES:
        package_pattern = re.compile(rf"^{re.escape(package_name)}(?=$|\s|[<>=!~;@\[])")
        matches = [requirement for requirement in requirements if package_pattern.match(requirement)]
        if len(matches) != 1:
            raise DistributionError(
                f"Expected one exact {package_name} requirement in Agent UI metadata, found {matches}"
            )
        exact = re.fullmatch(rf"{re.escape(package_name)}\s*==\s*([^\s;]+)", matches[0])
        if exact is None:
            raise DistributionError(f"Agent UI metadata must pin {package_name} exactly, found {matches[0]}")
        versions.add(exact.group(1))
    if len(versions) != 1:
        raise DistributionError(f"Agent UI internal dependency versions do not match: {sorted(versions)}")
    return versions.pop()


def validate_wheel(path: Path, *, require_exact_internal_version: bool = False) -> str | None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        _validate_runtime_manifest(archive.read, names, RUNTIME_MANIFEST_PATH)
        _validate_terminal_package(names)
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
        pin = _validate_internal_requirements(requirements) if require_exact_internal_version else None
    _validate_wheel_imports(path)
    return pin


def validate_sdist(path: Path, *, require_exact_internal_version: bool = False) -> tuple[str, str | None]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise DistributionError("Agent UI sdist must contain exactly one root directory")
        root = roots.pop()
        names = {member.name for member in members}

        def read(name: str) -> bytes:
            file = archive.extractfile(name)
            if file is None:
                raise DistributionError(f"Cannot read Agent UI sdist member: {name}")
            return file.read()

        _validate_runtime_manifest(read, names, PurePosixPath(root) / RUNTIME_MANIFEST_PATH)
        _validate_terminal_package(names, PurePosixPath(root))
        if any("apps/harness-ui" in name for name in names):
            raise DistributionError("Agent UI sdist must not bundle obsolete browser source")
        pyproject_path = f"{root}/pyproject.toml"
        if pyproject_path not in names:
            raise DistributionError("Agent UI sdist is missing pyproject.toml")
        try:
            project = tomllib.loads(read(pyproject_path).decode("utf-8"))["project"]
            requirements = project["dependencies"]
            scripts = project["scripts"]
        except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise DistributionError(f"Cannot read Agent UI sdist dependencies: {error}") from error
        if not isinstance(requirements, list) or not all(isinstance(value, str) for value in requirements):
            raise DistributionError("Agent UI sdist has invalid project.dependencies")
        if not isinstance(scripts, dict) or scripts.get("a13n-ui") != "a13n_ui.cli:main":
            raise DistributionError("Agent UI sdist is missing the a13n-ui console entrypoint")
        pin = _validate_internal_requirements(requirements) if require_exact_internal_version else None
        return root, pin


def rebuild_wheel_from_sdist(sdist: Path, *, require_exact_internal_version: bool = False) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise DistributionError("uv is required to rebuild the Agent UI wheel")

    with tempfile.TemporaryDirectory(prefix="agent-ui-sdist-") as directory:
        temporary = Path(directory)
        source = temporary / "source"
        output = temporary / "wheel"
        source.mkdir()
        with tarfile.open(sdist, mode="r:gz") as archive:
            archive.extractall(source, filter="data")
        roots = [path for path in source.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise DistributionError("Extracted Agent UI sdist must contain exactly one source directory")
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
            raise DistributionError(f"Cannot rebuild Agent UI wheel from sdist:\n{result.stdout}{result.stderr}")
        wheels = list(output.glob("*.whl"))
        if len(wheels) != 1:
            raise DistributionError(f"Expected one rebuilt Agent UI wheel, found {len(wheels)}")
        rebuilt = sdist.parent / "rebuilt-from-sdist" / wheels[0].name
        rebuilt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheels[0], rebuilt)
    validate_wheel(rebuilt, require_exact_internal_version=require_exact_internal_version)
    return rebuilt


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a13n-ui wheel and sdist contents.")
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--rebuild-wheel", action="store_true")
    parser.add_argument("--require-exact-internal-version", action="store_true")
    args = parser.parse_args()

    wheels = sorted(args.dist_dir.glob(f"{DISTRIBUTION_STEM}-*.whl"))
    sdists = sorted(args.dist_dir.glob(f"{DISTRIBUTION_STEM}-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"Expected one a13n-ui wheel and sdist in {args.dist_dir}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    try:
        wheel_pin = validate_wheel(
            wheels[0],
            require_exact_internal_version=args.require_exact_internal_version,
        )
        _, sdist_pin = validate_sdist(
            sdists[0],
            require_exact_internal_version=args.require_exact_internal_version,
        )
        if wheel_pin != sdist_pin:
            raise DistributionError(
                f"Agent UI wheel and sdist dependency pins do not match: {wheel_pin} != {sdist_pin}"
            )
        rebuilt = (
            rebuild_wheel_from_sdist(
                sdists[0],
                require_exact_internal_version=args.require_exact_internal_version,
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
