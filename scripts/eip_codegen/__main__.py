from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict

from .artifacts import write_artifacts
from .compiler import compile_descriptor
from .model import OptionReader, build_index, data_frame_profile
from .python_renderer import GENERATED_HEADER, write_python_surface

DESCRIPTOR_PATH = Path("crates/a13n-envd/protocol/eip/v1/descriptor.pb")
PYTHON_PATH = Path("packages/a13n-envd-client/a13n_envd_client/eip/v1")
ARTIFACT_PATH = Path("proto/a13n-envd/eip/v1/artifacts")
MANIFEST_PATH = ARTIFACT_PATH / "generated-files.json"
ALLOWED_ROOTS = (DESCRIPTOR_PATH.parent, PYTHON_PATH, ARTIFACT_PATH)


class GeneratedManifest(TypedDict):
    generated: bool
    descriptor_sha256: str
    files: list[str]


def _format_python(path: Path) -> None:
    subprocess.run(["ruff", "check", "--fix", str(path)], check=True)
    subprocess.run(["ruff", "format", str(path)], check=True)


def _safe_generated_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe generated path: {value}")
    if not any(path == root or path.is_relative_to(root) for root in ALLOWED_ROOTS):
        raise ValueError(f"generated path is outside owned roots: {value}")
    return path


def _write_manifest(root: Path, generated: list[Path], descriptor_sha256: str) -> None:
    relative = sorted(str(path.relative_to(root)) for path in generated)
    relative.append(str(MANIFEST_PATH))
    manifest = {
        "generated": True,
        "descriptor_sha256": descriptor_sha256,
        "files": sorted(relative),
    }
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate_tree(root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="eip-compile-") as compile_tmp:
        descriptor_set, descriptor_bytes, option_module = compile_descriptor(Path(compile_tmp))
    index = build_index(descriptor_set)
    options = OptionReader(option_module)
    frame_profile = data_frame_profile(index, options)

    descriptor_target = root / DESCRIPTOR_PATH
    descriptor_target.parent.mkdir(parents=True, exist_ok=True)
    descriptor_target.write_bytes(descriptor_bytes)

    descriptor_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
    python_target = root / PYTHON_PATH
    python_paths = write_python_surface(
        python_target,
        index,
        options,
        frame_profile,
        descriptor_sha256,
    )
    _format_python(python_target)

    artifact_paths = write_artifacts(
        root / ARTIFACT_PATH,
        index,
        options,
        python_target / "models.py",
        frame_profile,
    )
    generated = [descriptor_target, *python_paths, *artifact_paths]
    _write_manifest(root, generated, descriptor_sha256)


def _read_manifest(root: Path) -> GeneratedManifest:
    path = root / MANIFEST_PATH
    if not path.is_file():
        raise FileNotFoundError(f"generated manifest is missing: {MANIFEST_PATH}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("generated") is not True:
        raise ValueError(f"invalid generated manifest: {MANIFEST_PATH}")
    descriptor_sha256 = value.get("descriptor_sha256")
    if not isinstance(descriptor_sha256, str) or len(descriptor_sha256) != 64:
        raise ValueError(f"invalid generated manifest: {MANIFEST_PATH}")
    descriptor_path = root / DESCRIPTOR_PATH
    if not descriptor_path.is_file() or hashlib.sha256(descriptor_path.read_bytes()).hexdigest() != descriptor_sha256:
        raise ValueError("generated manifest descriptor digest does not match descriptor.pb")
    files = value.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise ValueError(f"invalid generated manifest: {MANIFEST_PATH}")
    return {
        "generated": True,
        "descriptor_sha256": descriptor_sha256,
        "files": [item for item in files if isinstance(item, str)],
    }


def _manifest_paths(root: Path) -> set[Path]:
    manifest = _read_manifest(root)
    return {_safe_generated_path(value) for value in manifest["files"]}


def _discover_marked_files(root: Path) -> set[Path]:
    discovered: set[Path] = set()
    for owned_root in (PYTHON_PATH, ARTIFACT_PATH):
        directory = root / owned_root
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if path.suffix == ".py" and path.read_text(encoding="utf-8").startswith(GENERATED_HEADER):
                discovered.add(relative)
            elif path.suffix == ".json":
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("generated") is True:
                    discovered.add(relative)
    if (root / DESCRIPTOR_PATH).exists():
        discovered.add(DESCRIPTOR_PATH)
    return discovered


def install_generated(candidate_root: Path, repository_root: Path) -> None:
    candidate_paths = _manifest_paths(candidate_root)
    try:
        previous_paths = _manifest_paths(repository_root)
    except (FileNotFoundError, ValueError):
        previous_paths = _discover_marked_files(repository_root)
    for stale in sorted(previous_paths - candidate_paths):
        target = repository_root / stale
        if target.is_file():
            target.unlink()
    for relative in sorted(candidate_paths):
        source = candidate_root / relative
        target = repository_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def verify_generated(candidate_root: Path, repository_root: Path) -> None:
    expected = _manifest_paths(candidate_root)
    try:
        actual = _manifest_paths(repository_root)
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"EIP generated artifacts are stale: {error}\nRun: make eip-generate") from error

    problems: list[str] = []
    if expected != actual:
        for path in sorted(expected - actual):
            problems.append(f"missing generated file: {path}")
        for path in sorted(actual - expected):
            problems.append(f"stale generated file: {path}")
    marked = _discover_marked_files(repository_root)
    for path in sorted(marked - expected):
        problems.append(f"untracked generated file: {path}")
    for relative in sorted(expected & actual):
        candidate = candidate_root / relative
        checked = repository_root / relative
        if not checked.is_file() or candidate.read_bytes() != checked.read_bytes():
            problems.append(f"generated file differs: {relative}")
    if problems:
        raise SystemExit("EIP generated artifacts are stale:\n- " + "\n- ".join(problems) + "\nRun: make eip-generate")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and verify EIP protocol artifacts.")
    parser.add_argument("command", choices=("generate", "verify"))
    args = parser.parse_args()
    repository_root = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="eip-generated-") as tmp:
        candidate_root = Path(tmp)
        generate_tree(candidate_root)
        if args.command == "generate":
            install_generated(candidate_root, repository_root)
        else:
            verify_generated(candidate_root, repository_root)


if __name__ == "__main__":
    main()
