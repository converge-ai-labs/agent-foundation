from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
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

DISTRIBUTION_STEM = "a13n_ui"
MANIFEST_NAME = "asset-manifest.json"
PACKAGE_PREFIX = PurePosixPath("a13n_ui/static")
RUNTIME_MANIFEST_PATH = PurePosixPath("a13n_ui/assets/agent-envd-release.json")
INTERNAL_PACKAGES = (
    "a13n-environment-provider",
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
        raise DistributionError(f"Agent UI shell contains an unsafe asset reference: {value}")
    return normalized.as_posix()


def _validate_assets(read: Callable[[str], bytes], names: set[str], prefix: PurePosixPath) -> None:
    manifest_path = str(prefix / MANIFEST_NAME)
    index_path = str(prefix / "index.html")
    if index_path not in names or manifest_path not in names:
        raise DistributionError(f"Agent UI artifact is missing {index_path} or {manifest_path}")

    try:
        manifest = json.loads(read(manifest_path))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DistributionError(f"Invalid Agent UI asset manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1":
        raise DistributionError("Unsupported Agent UI asset manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or "index.html" not in files:
        raise DistributionError("Agent UI asset manifest does not describe index.html")

    declared_assets: set[str] = set()
    for relative_name, expected_digest in files.items():
        if not isinstance(relative_name, str) or not isinstance(expected_digest, str):
            raise DistributionError("Agent UI asset manifest contains an invalid entry")
        relative_path = PurePosixPath(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise DistributionError(f"Agent UI asset manifest contains an unsafe path: {relative_name}")
        normalized_name = relative_path.as_posix()
        declared_assets.add(normalized_name)
        archive_path = str(prefix / relative_path)
        if archive_path not in names:
            raise DistributionError(f"Agent UI artifact is missing declared asset: {archive_path}")
        if _sha256(read(archive_path)) != expected_digest:
            raise DistributionError(f"Agent UI asset digest mismatch: {archive_path}")

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
            f"Agent UI asset manifest does not match packaged files; undeclared={undeclared}, missing={missing}"
        )

    try:
        index = read(index_path).decode("utf-8")
    except UnicodeDecodeError as error:
        raise DistributionError("Agent UI index.html must be UTF-8") from error
    parser = _AssetReferenceParser()
    parser.feed(index)
    for value in parser.references:
        reference = _local_asset_reference(value)
        if reference is not None and reference not in declared_assets:
            raise DistributionError(f"Agent UI shell references an undeclared or missing asset: {value}")


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
        _validate_assets(archive.read, names, PACKAGE_PREFIX)
        _validate_runtime_manifest(archive.read, names, RUNTIME_MANIFEST_PATH)
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise DistributionError(f"Expected one METADATA file in {path}, found {len(metadata_paths)}")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_paths[0]))
        requirements = metadata.get_all("Requires-Dist", [])
        if not all(isinstance(requirement, str) for requirement in requirements):
            raise DistributionError(f"Invalid Requires-Dist metadata in {path}")
        if require_exact_internal_version:
            return _validate_internal_requirements(requirements)
        return None


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

        _validate_assets(read, names, PurePosixPath(root) / PACKAGE_PREFIX)
        _validate_runtime_manifest(read, names, PurePosixPath(root) / RUNTIME_MANIFEST_PATH)
        if any("apps/harness-ui" in name for name in names):
            raise DistributionError("Agent UI sdist must not require the Harness UI source tree")
        pyproject_path = f"{root}/pyproject.toml"
        if pyproject_path not in names:
            raise DistributionError("Agent UI sdist is missing pyproject.toml")
        try:
            project = tomllib.loads(read(pyproject_path).decode("utf-8"))["project"]
            requirements = project["dependencies"]
        except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise DistributionError(f"Cannot read Agent UI sdist dependencies: {error}") from error
        if not isinstance(requirements, list) or not all(isinstance(value, str) for value in requirements):
            raise DistributionError("Agent UI sdist has invalid project.dependencies")
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
