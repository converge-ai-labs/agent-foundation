from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

COMPONENTS = (
    "a13n-harness",
    "a13n-harness-ui",
    "a13n-logging",
    "a13n-service",
    "a13n-envd",
    "a13n-service-cli",
    "a13n-python",
    "a13n-go",
    "a13n-rust",
    "a13n-typescript",
)
ENVIRONMENT_PROVIDER_MANIFEST = Path("packages/a13n-environment/pyproject.toml")
ENVIRONMENT_PROVIDER_PACKAGE = "a13n-environment"
HARNESS_MANIFEST = Path("packages/a13n-harness/pyproject.toml")
HARNESS_PACKAGE = "a13n-harness"
STREAM_PROTOCOL_MANIFEST = Path("packages/a13n-stream-protocol/pyproject.toml")
HARNESS_MANIFESTS = (
    ENVIRONMENT_PROVIDER_MANIFEST,
    HARNESS_MANIFEST,
    STREAM_PROTOCOL_MANIFEST,
)
HARNESS_PACKAGES = (
    ENVIRONMENT_PROVIDER_PACKAGE,
    HARNESS_PACKAGE,
    "a13n-stream-protocol",
)
HARNESS_UI_MANIFEST = Path("packages/a13n-harness-ui/pyproject.toml")
HARNESS_UI_PACKAGE = "a13n-harness-ui"
HARNESS_UI_RELEASE_TOOL = "tool.a13n.harness-ui-release"
HARNESS_UI_ENVD_RELEASE_MANIFEST = Path("packages/a13n-harness-ui/a13n_harness_ui/assets/a13n-envd-release.json")
LOGGING_MANIFEST = Path("packages/a13n-logging/pyproject.toml")
LOGGING_PACKAGE = "a13n-logging"
A13N_SERVICE_MANIFESTS = (
    Path("pyproject.toml"),
    Path("packages/a13n-service/pyproject.toml"),
)
A13N_SERVICE_PACKAGES = (
    "a13n-workspace",
    "a13n-service",
)
ROOT_UV_LOCK = Path("uv.lock")
A13N_ENVD_WORKSPACE_MANIFEST = Path("Cargo.toml")
A13N_ENVD_MANIFEST = Path("crates/a13n-envd/Cargo.toml")
A13N_ENVD_LOCK = Path("Cargo.lock")
A13N_ENVD_CLIENT_MANIFEST = Path("packages/a13n-envd-client/pyproject.toml")
A13N_ENVD_CLIENT_PACKAGE = "a13n-envd-client"
SDK_PYTHON_MANIFEST = Path("sdk/python/pyproject.toml")
SDK_PYTHON_LOCK = Path("sdk/python/uv.lock")
SDK_RUST_MANIFEST = Path("sdk/rust/Cargo.toml")
SDK_RUST_LOCK = Path("sdk/rust/Cargo.lock")
A13N_SERVICE_CLI_MANIFEST = Path("sdk/rust/a13n-service-cli/Cargo.toml")
A13N_SERVICE_CLI_LOCK = Path("sdk/rust/a13n-service-cli/Cargo.lock")
A13N_SERVICE_CLI_PACKAGE = "a13n-service-cli"
SDK_TYPESCRIPT_MANIFEST = Path("sdk/typescript/package.json")
SDK_TYPESCRIPT_LOCK = Path("sdk/typescript/package-lock.json")
RELEASE_VERSION_PATTERN = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-rc\.(?P<rc>[1-9][0-9]*))?"
)
_VERSION_LINE_PATTERN = re.compile(r'^(\s*version\s*=\s*")[^"]*(".*?)(\r?\n)?$')
_DEPENDENCY_LINE_PATTERN = re.compile(r'^(?P<prefix>\s*")(?P<requirement>[^"]+)(?P<suffix>".*?)(?P<newline>\r?\n)?$')


class ReleaseVersionError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseVersion:
    major: int
    minor: int
    patch: int
    rc: int | None

    @property
    def canonical(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.rc is None:
            return base
        return f"{base}-rc.{self.rc}"

    @property
    def python_package(self) -> str:
        if self.rc is None:
            return self.canonical
        return f"{self.major}.{self.minor}.{self.patch}rc{self.rc}"

    @property
    def is_prerelease(self) -> bool:
        return self.rc is not None

    @property
    def precedence_key(self) -> tuple[int, int, int, int, int]:
        if self.rc is None:
            return self.major, self.minor, self.patch, 1, 0
        return self.major, self.minor, self.patch, 0, self.rc


def parse_release_version(version: str) -> ReleaseVersion:
    match = RELEASE_VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ReleaseVersionError(f"Release version must use X.Y.Z or X.Y.Z-rc.N syntax: {version}")
    rc = match.group("rc")
    return ReleaseVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        rc=int(rc) if rc is not None else None,
    )


def validate_version_syntax(version: str) -> None:
    parse_release_version(version)


def python_package_version(version: str) -> str:
    return parse_release_version(version).python_package


def _load_toml(root: Path, relative_path: Path) -> dict[str, object]:
    path = root / relative_path
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ReleaseVersionError(f"Cannot read TOML from {relative_path}: {error}") from error


def _load_json(root: Path, relative_path: Path) -> dict[str, object]:
    path = root / relative_path
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseVersionError(f"Cannot read JSON from {relative_path}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseVersionError(f"Expected a JSON object in {relative_path}")
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReleaseVersionError(f"Missing {label}")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ReleaseVersionError(f"Missing {label}")
    return value


def _project_version(root: Path, relative_path: Path) -> str:
    project = _mapping(_load_toml(root, relative_path).get("project"), f"project.version in {relative_path}")
    return _string(project.get("version"), f"project.version in {relative_path}")


def _project_dependency_requirement(root: Path, relative_path: Path, package_name: str) -> str:
    project = _mapping(_load_toml(root, relative_path).get("project"), f"project.dependencies in {relative_path}")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        raise ReleaseVersionError(f"Missing project.dependencies in {relative_path}")
    pattern = re.compile(rf"^{re.escape(package_name)}(?=$|\s|[<>=!~;@\[])")
    matches = [value for value in dependencies if isinstance(value, str) and pattern.match(value)]
    if len(matches) != 1:
        raise ReleaseVersionError(
            f"Expected exactly one {package_name} dependency in {relative_path}, found {len(matches)}"
        )
    return matches[0]


def _harness_ui_release_selection(root: Path, key: str, label: str) -> ReleaseVersion:
    data = _load_toml(root, HARNESS_UI_MANIFEST)
    tool = _mapping(data.get("tool"), f"{HARNESS_UI_RELEASE_TOOL}.{key} in {HARNESS_UI_MANIFEST}")
    a13n = _mapping(tool.get("a13n"), f"{HARNESS_UI_RELEASE_TOOL}.{key} in {HARNESS_UI_MANIFEST}")
    release = _mapping(
        a13n.get("harness-ui-release"),
        f"{HARNESS_UI_RELEASE_TOOL}.{key} in {HARNESS_UI_MANIFEST}",
    )
    version = _string(release.get(key), f"{HARNESS_UI_RELEASE_TOOL}.{key} in {HARNESS_UI_MANIFEST}")
    selected = parse_release_version(version)
    if selected.canonical == "0.0.0":
        raise ReleaseVersionError(
            f"Select a published {label} release in {HARNESS_UI_MANIFEST} before releasing Harness UI"
        )
    return selected


def _harness_ui_harness_release(root: Path) -> ReleaseVersion:
    return _harness_ui_release_selection(root, "harness-version", "Harness")


def _harness_ui_envd_release(root: Path) -> ReleaseVersion:
    selected = _harness_ui_release_selection(root, "envd-version", "a13n-envd")
    manifest = _load_json(root, HARNESS_UI_ENVD_RELEASE_MANIFEST)
    actual = _string(manifest.get("release"), f"release in {HARNESS_UI_ENVD_RELEASE_MANIFEST}")
    if actual != selected.canonical:
        raise ReleaseVersionError(
            f"Expected {HARNESS_UI_ENVD_RELEASE_MANIFEST} release {selected.canonical}, found {actual}"
        )
    return selected


def _cargo_package_version(root: Path, relative_path: Path) -> str:
    package = _mapping(_load_toml(root, relative_path).get("package"), f"package.version in {relative_path}")
    return _string(package.get("version"), f"package.version in {relative_path}")


def _workspace_package_version(root: Path) -> str:
    workspace = _mapping(
        _load_toml(root, A13N_ENVD_WORKSPACE_MANIFEST).get("workspace"),
        f"workspace.package.version in {A13N_ENVD_WORKSPACE_MANIFEST}",
    )
    package = _mapping(
        workspace.get("package"),
        f"workspace.package.version in {A13N_ENVD_WORKSPACE_MANIFEST}",
    )
    return _string(
        package.get("version"),
        f"workspace.package.version in {A13N_ENVD_WORKSPACE_MANIFEST}",
    )


def _validate_a13n_envd_inheritance(root: Path) -> None:
    package = _mapping(
        _load_toml(root, A13N_ENVD_MANIFEST).get("package"),
        f"package.version.workspace in {A13N_ENVD_MANIFEST}",
    )
    version = package.get("version")
    if not isinstance(version, dict) or version.get("workspace") is not True:
        raise ReleaseVersionError(f"Expected package.version.workspace = true in {A13N_ENVD_MANIFEST}")


def _lock_package_version(root: Path, relative_path: Path, package_name: str) -> str:
    packages = _load_toml(root, relative_path).get("package")
    if not isinstance(packages, list):
        raise ReleaseVersionError(f"Missing package {package_name} in {relative_path}")
    matches = [package for package in packages if isinstance(package, dict) and package.get("name") == package_name]
    if len(matches) != 1:
        raise ReleaseVersionError(
            f"Expected exactly one package {package_name} in {relative_path}, found {len(matches)}"
        )
    return _string(matches[0].get("version"), f"package {package_name} version in {relative_path}")


def _npm_version(root: Path, relative_path: Path) -> str:
    return _string(_load_json(root, relative_path).get("version"), f"version in {relative_path}")


def _npm_lock_root_version(root: Path) -> str:
    lock = _load_json(root, SDK_TYPESCRIPT_LOCK)
    packages = _mapping(lock.get("packages"), f'packages[""] version in {SDK_TYPESCRIPT_LOCK}')
    root_package = _mapping(
        packages.get(""),
        f'packages[""] version in {SDK_TYPESCRIPT_LOCK}',
    )
    return _string(
        root_package.get("version"),
        f'packages[""] version in {SDK_TYPESCRIPT_LOCK}',
    )


def component_versions(root: Path, component: str) -> dict[str, str]:
    if component not in COMPONENTS:
        raise ReleaseVersionError(f"Unknown release component: {component}")

    if component == "a13n-harness":
        versions = {str(path): _project_version(root, path) for path in HARNESS_MANIFESTS}
        versions.update(
            {
                f"{ROOT_UV_LOCK} package {package_name}": _lock_package_version(
                    root,
                    ROOT_UV_LOCK,
                    package_name,
                )
                for package_name in HARNESS_PACKAGES
            }
        )
        return versions
    if component == "a13n-harness-ui":
        return {
            str(HARNESS_UI_MANIFEST): _project_version(root, HARNESS_UI_MANIFEST),
            f"{ROOT_UV_LOCK} package {HARNESS_UI_PACKAGE}": _lock_package_version(
                root,
                ROOT_UV_LOCK,
                HARNESS_UI_PACKAGE,
            ),
        }
    if component == "a13n-logging":
        return {
            str(LOGGING_MANIFEST): _project_version(root, LOGGING_MANIFEST),
            f"{ROOT_UV_LOCK} package {LOGGING_PACKAGE}": _lock_package_version(
                root,
                ROOT_UV_LOCK,
                LOGGING_PACKAGE,
            ),
        }
    if component == "a13n-service":
        versions = {str(path): _project_version(root, path) for path in A13N_SERVICE_MANIFESTS}
        versions.update(
            {
                f"{ROOT_UV_LOCK} package {package_name}": _lock_package_version(
                    root,
                    ROOT_UV_LOCK,
                    package_name,
                )
                for package_name in A13N_SERVICE_PACKAGES
            }
        )
        return versions
    if component == "a13n-envd":
        _validate_a13n_envd_inheritance(root)
        return {
            f"{A13N_ENVD_WORKSPACE_MANIFEST} workspace package": _workspace_package_version(root),
            f"{A13N_ENVD_MANIFEST} inherited workspace package": _workspace_package_version(root),
            f"{A13N_ENVD_LOCK} package a13n-envd": _lock_package_version(
                root,
                A13N_ENVD_LOCK,
                "a13n-envd",
            ),
            str(A13N_ENVD_CLIENT_MANIFEST): _project_version(root, A13N_ENVD_CLIENT_MANIFEST),
            f"{ROOT_UV_LOCK} package {A13N_ENVD_CLIENT_PACKAGE}": _lock_package_version(
                root,
                ROOT_UV_LOCK,
                A13N_ENVD_CLIENT_PACKAGE,
            ),
        }
    if component == "a13n-python":
        return {
            str(SDK_PYTHON_MANIFEST): _project_version(root, SDK_PYTHON_MANIFEST),
            f"{SDK_PYTHON_LOCK} package a13n": _lock_package_version(
                root,
                SDK_PYTHON_LOCK,
                "a13n",
            ),
        }
    if component == "a13n-rust":
        return {
            str(SDK_RUST_MANIFEST): _cargo_package_version(root, SDK_RUST_MANIFEST),
            f"{SDK_RUST_LOCK} package a13n": _lock_package_version(
                root,
                SDK_RUST_LOCK,
                "a13n",
            ),
        }
    if component == "a13n-service-cli":
        return {
            str(A13N_SERVICE_CLI_MANIFEST): _cargo_package_version(root, A13N_SERVICE_CLI_MANIFEST),
            f"{A13N_SERVICE_CLI_LOCK} package {A13N_SERVICE_CLI_PACKAGE}": _lock_package_version(
                root,
                A13N_SERVICE_CLI_LOCK,
                A13N_SERVICE_CLI_PACKAGE,
            ),
        }
    if component == "a13n-typescript":
        return {
            str(SDK_TYPESCRIPT_MANIFEST): _npm_version(root, SDK_TYPESCRIPT_MANIFEST),
            str(SDK_TYPESCRIPT_LOCK): _npm_version(root, SDK_TYPESCRIPT_LOCK),
            f'{SDK_TYPESCRIPT_LOCK} packages[""]': _npm_lock_root_version(root),
        }
    return {}


def _expected_component_versions(
    component: str,
    release_version: ReleaseVersion,
    labels: tuple[str, ...],
) -> dict[str, str]:
    if component in {"a13n-harness", "a13n-harness-ui", "a13n-logging", "a13n-service", "a13n-python"}:
        return {label: release_version.python_package for label in labels}
    if component == "a13n-envd":
        python_labels = {
            str(A13N_ENVD_CLIENT_MANIFEST),
            f"{ROOT_UV_LOCK} package {A13N_ENVD_CLIENT_PACKAGE}",
        }
        return {
            label: release_version.python_package if label in python_labels else release_version.canonical
            for label in labels
        }
    return {label: release_version.canonical for label in labels}


def validate_component_version(root: Path, component: str, version: str) -> None:
    release_version = parse_release_version(version)
    versions = component_versions(root, component)
    expected_versions = _expected_component_versions(component, release_version, tuple(versions))
    mismatches = {
        label: (actual, expected_versions[label])
        for label, actual in versions.items()
        if actual != expected_versions[label]
    }
    if mismatches:
        details = "\n".join(
            f"- {label}: {actual} (expected {expected})" for label, (actual, expected) in mismatches.items()
        )
        raise ReleaseVersionError(f"Expected {component} version {version}:\n{details}")

    if release_version.canonical == "0.0.0":
        return
    if component == "a13n-harness":
        for manifest, package_name in (
            (HARNESS_MANIFEST, ENVIRONMENT_PROVIDER_PACKAGE),
            (STREAM_PROTOCOL_MANIFEST, HARNESS_PACKAGE),
        ):
            expected = f"{package_name}=={release_version.python_package}"
            actual = _project_dependency_requirement(root, manifest, package_name)
            if actual != expected:
                raise ReleaseVersionError(f"Expected {manifest} dependency {expected}, found {actual}")
    elif component == "a13n-harness-ui":
        selected = _harness_ui_harness_release(root).python_package
        _harness_ui_envd_release(root)
        for package_name in HARNESS_PACKAGES:
            expected = f"{package_name}=={selected}"
            actual = _project_dependency_requirement(root, HARNESS_UI_MANIFEST, package_name)
            if actual != expected:
                raise ReleaseVersionError(f"Expected {HARNESS_UI_MANIFEST} dependency {expected}, found {actual}")


def _replace_table_version(content: str, table_name: str, version: str, path: Path) -> str:
    lines = content.splitlines(keepends=True)
    active_table: str | None = None
    replacements = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            active_table = stripped[1:-1]
            continue
        if active_table != table_name:
            continue
        match = _VERSION_LINE_PATTERN.fullmatch(line)
        if match is None:
            continue
        newline = match.group(3) or ""
        lines[index] = f"{match.group(1)}{version}{match.group(2)}{newline}"
        replacements += 1
    if replacements != 1:
        raise ReleaseVersionError(
            f"Expected exactly one {table_name}.version assignment in {path}, found {replacements}"
        )
    return "".join(lines)


def _replace_project_dependency(
    content: str,
    package_name: str,
    requirement: str,
    path: Path,
) -> str:
    package_pattern = re.compile(rf"^{re.escape(package_name)}(?=$|\s|[<>=!~;@\[])")
    lines = content.splitlines(keepends=True)
    matching_indexes: list[int] = []
    for index, line in enumerate(lines):
        match = _DEPENDENCY_LINE_PATTERN.fullmatch(line)
        if match is not None and package_pattern.match(match.group("requirement")):
            matching_indexes.append(index)
    if len(matching_indexes) != 1:
        raise ReleaseVersionError(
            f"Expected exactly one {package_name} dependency in {path}, found {len(matching_indexes)}"
        )
    index = matching_indexes[0]
    match = _DEPENDENCY_LINE_PATTERN.fullmatch(lines[index])
    if match is None:
        raise AssertionError("dependency line disappeared")
    lines[index] = f"{match.group('prefix')}{requirement}{match.group('suffix')}{match.group('newline') or ''}"
    return "".join(lines)


def _replace_lock_package_version(
    content: str,
    package_name: str,
    version: str,
    path: Path,
) -> str:
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.strip() == "[[package]]"]
    starts.append(len(lines))
    matching_blocks: list[tuple[int, int]] = []
    name_pattern = re.compile(rf'^\s*name\s*=\s*"{re.escape(package_name)}"\s*(?:#.*)?(?:\r?\n)?$')
    for block_index in range(len(starts) - 1):
        start = starts[block_index]
        end = starts[block_index + 1]
        if any(name_pattern.fullmatch(line) is not None for line in lines[start:end]):
            matching_blocks.append((start, end))
    if len(matching_blocks) != 1:
        raise ReleaseVersionError(
            f"Expected exactly one package {package_name} in {path}, found {len(matching_blocks)}"
        )

    start, end = matching_blocks[0]
    version_indexes = [
        index for index in range(start, end) if _VERSION_LINE_PATTERN.fullmatch(lines[index]) is not None
    ]
    if len(version_indexes) != 1:
        raise ReleaseVersionError(
            f"Expected exactly one version for package {package_name} in {path}, found {len(version_indexes)}"
        )
    index = version_indexes[0]
    match = _VERSION_LINE_PATTERN.fullmatch(lines[index])
    if match is None:
        raise AssertionError("version line disappeared")
    newline = match.group(3) or ""
    lines[index] = f"{match.group(1)}{version}{match.group(2)}{newline}"
    return "".join(lines)


def _replace_json_versions(
    content: str,
    version: str,
    path: Path,
    *,
    include_lock_root: bool,
) -> str:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ReleaseVersionError(f"Cannot read JSON from {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("version"), str):
        raise ReleaseVersionError(f"Missing version in {path}")

    changed = value["version"] != version
    value["version"] = version
    if include_lock_root:
        packages = _mapping(value.get("packages"), f'packages[""] version in {path}')
        root_package = _mapping(packages.get(""), f'packages[""] version in {path}')
        if not isinstance(root_package.get("version"), str):
            raise ReleaseVersionError(f'Missing packages[""] version in {path}')
        changed = changed or root_package["version"] != version
        root_package["version"] = version

    if not changed:
        return content
    return f"{json.dumps(value, indent=2, ensure_ascii=False)}\n"


def _read_text(root: Path, relative_path: Path) -> str:
    try:
        return (root / relative_path).read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseVersionError(f"Cannot read {relative_path}: {error}") from error


def _atomic_write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def prepare_component_version(root: Path, component: str, version: str) -> tuple[Path, ...]:
    release_version = parse_release_version(version)
    canonical_version = release_version.canonical
    python_version = release_version.python_package
    component_versions(root, component)
    planned: dict[Path, str] = {}

    if component == "a13n-harness":
        for path in HARNESS_MANIFESTS:
            planned[path] = _replace_table_version(
                _read_text(root, path),
                "project",
                python_version,
                path,
            )
        for manifest, package_name in (
            (HARNESS_MANIFEST, ENVIRONMENT_PROVIDER_PACKAGE),
            (STREAM_PROTOCOL_MANIFEST, HARNESS_PACKAGE),
        ):
            planned[manifest] = _replace_project_dependency(
                planned[manifest],
                package_name,
                f"{package_name}=={python_version}",
                manifest,
            )
        lock_content = _read_text(root, ROOT_UV_LOCK)
        for package_name in HARNESS_PACKAGES:
            lock_content = _replace_lock_package_version(
                lock_content,
                package_name,
                python_version,
                ROOT_UV_LOCK,
            )
        planned[ROOT_UV_LOCK] = lock_content
    elif component == "a13n-harness-ui":
        selected_harness_version = _harness_ui_harness_release(root).python_package
        _harness_ui_envd_release(root)
        ui_content = _replace_table_version(
            _read_text(root, HARNESS_UI_MANIFEST),
            "project",
            python_version,
            HARNESS_UI_MANIFEST,
        )
        for package_name in HARNESS_PACKAGES:
            ui_content = _replace_project_dependency(
                ui_content,
                package_name,
                f"{package_name}=={selected_harness_version}",
                HARNESS_UI_MANIFEST,
            )
        planned[HARNESS_UI_MANIFEST] = ui_content
        planned[ROOT_UV_LOCK] = _replace_lock_package_version(
            _read_text(root, ROOT_UV_LOCK),
            HARNESS_UI_PACKAGE,
            python_version,
            ROOT_UV_LOCK,
        )
    elif component == "a13n-logging":
        planned[LOGGING_MANIFEST] = _replace_table_version(
            _read_text(root, LOGGING_MANIFEST),
            "project",
            python_version,
            LOGGING_MANIFEST,
        )
        planned[ROOT_UV_LOCK] = _replace_lock_package_version(
            _read_text(root, ROOT_UV_LOCK),
            LOGGING_PACKAGE,
            python_version,
            ROOT_UV_LOCK,
        )
    elif component == "a13n-service":
        for path in A13N_SERVICE_MANIFESTS:
            planned[path] = _replace_table_version(
                _read_text(root, path),
                "project",
                python_version,
                path,
            )
        lock_content = _read_text(root, ROOT_UV_LOCK)
        for package_name in A13N_SERVICE_PACKAGES:
            lock_content = _replace_lock_package_version(
                lock_content,
                package_name,
                python_version,
                ROOT_UV_LOCK,
            )
        planned[ROOT_UV_LOCK] = lock_content
    elif component == "a13n-envd":
        planned[A13N_ENVD_WORKSPACE_MANIFEST] = _replace_table_version(
            _read_text(root, A13N_ENVD_WORKSPACE_MANIFEST),
            "workspace.package",
            canonical_version,
            A13N_ENVD_WORKSPACE_MANIFEST,
        )
        planned[A13N_ENVD_LOCK] = _replace_lock_package_version(
            _read_text(root, A13N_ENVD_LOCK),
            "a13n-envd",
            canonical_version,
            A13N_ENVD_LOCK,
        )
        planned[A13N_ENVD_CLIENT_MANIFEST] = _replace_table_version(
            _read_text(root, A13N_ENVD_CLIENT_MANIFEST),
            "project",
            python_version,
            A13N_ENVD_CLIENT_MANIFEST,
        )
        planned[ROOT_UV_LOCK] = _replace_lock_package_version(
            _read_text(root, ROOT_UV_LOCK),
            A13N_ENVD_CLIENT_PACKAGE,
            python_version,
            ROOT_UV_LOCK,
        )
    elif component == "a13n-python":
        planned[SDK_PYTHON_MANIFEST] = _replace_table_version(
            _read_text(root, SDK_PYTHON_MANIFEST),
            "project",
            python_version,
            SDK_PYTHON_MANIFEST,
        )
        planned[SDK_PYTHON_LOCK] = _replace_lock_package_version(
            _read_text(root, SDK_PYTHON_LOCK),
            "a13n",
            python_version,
            SDK_PYTHON_LOCK,
        )
    elif component == "a13n-rust":
        planned[SDK_RUST_MANIFEST] = _replace_table_version(
            _read_text(root, SDK_RUST_MANIFEST),
            "package",
            canonical_version,
            SDK_RUST_MANIFEST,
        )
        planned[SDK_RUST_LOCK] = _replace_lock_package_version(
            _read_text(root, SDK_RUST_LOCK),
            "a13n",
            canonical_version,
            SDK_RUST_LOCK,
        )
    elif component == "a13n-service-cli":
        planned[A13N_SERVICE_CLI_MANIFEST] = _replace_table_version(
            _read_text(root, A13N_SERVICE_CLI_MANIFEST),
            "package",
            canonical_version,
            A13N_SERVICE_CLI_MANIFEST,
        )
        planned[A13N_SERVICE_CLI_LOCK] = _replace_lock_package_version(
            _read_text(root, A13N_SERVICE_CLI_LOCK),
            A13N_SERVICE_CLI_PACKAGE,
            canonical_version,
            A13N_SERVICE_CLI_LOCK,
        )
    elif component == "a13n-typescript":
        planned[SDK_TYPESCRIPT_MANIFEST] = _replace_json_versions(
            _read_text(root, SDK_TYPESCRIPT_MANIFEST),
            canonical_version,
            SDK_TYPESCRIPT_MANIFEST,
            include_lock_root=False,
        )
        planned[SDK_TYPESCRIPT_LOCK] = _replace_json_versions(
            _read_text(root, SDK_TYPESCRIPT_LOCK),
            canonical_version,
            SDK_TYPESCRIPT_LOCK,
            include_lock_root=True,
        )

    changed = tuple(path for path in sorted(planned) if planned[path] != _read_text(root, path))
    for relative_path in changed:
        _atomic_write(root / relative_path, planned[relative_path])

    validate_component_version(root, component, version)
    return changed
