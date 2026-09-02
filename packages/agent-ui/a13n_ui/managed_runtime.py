"""Package-selected acquisition of the exact agent-envd native runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import tarfile
import zipfile
from importlib.resources import files
from pathlib import Path
from typing import IO
from uuid import uuid4

import httpx2
from anyio import Lock, open_file, to_thread
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from a13n_ui.errors import RuntimeResolutionError
from a13n_ui.settings import EnvdRuntimeSettings

_MANIFEST_RESOURCE = "assets/agent-envd-release.json"
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


class EnvdReleaseAsset(BaseModel):
    """One immutable native archive and its extracted executable identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    archive: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._-]+$")
    archive_format: str = Field(pattern=r"^(tar\.gz|zip)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    executable: str = Field(pattern=r"^agent-envd(?:\.exe)?$")
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    executable_size: int = Field(gt=0)


class EnvdReleaseManifest(BaseModel):
    """Trusted package metadata selecting one exact agent-envd release."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: str = Field(pattern=r"^1$")
    release: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[1-9][0-9]*)?$")
    base_url: str = Field(min_length=1, max_length=2048, pattern=r"^https://github\.com/")
    targets: dict[str, EnvdReleaseAsset] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _coherent_assets(self) -> EnvdReleaseManifest:
        for target, asset in self.targets.items():
            suffix = ".zip" if target.endswith("windows-msvc") else ".tar.gz"
            if not asset.archive.endswith(suffix):
                raise ValueError("agent-envd archive format does not match its target")
            if target.endswith("windows-msvc") != asset.executable.endswith(".exe"):
                raise ValueError("agent-envd executable name does not match its target")
            if f"-{self.release}-{target}" not in asset.archive:
                raise ValueError("agent-envd archive identity does not match its release target")
        return self


class ManagedEnvdRuntime:
    """Resolve, verify, and lazily cache the package-selected Host executable."""

    def __init__(
        self,
        *,
        cache_root: Path,
        staging_root: Path,
        settings: EnvdRuntimeSettings,
        manifest: EnvdReleaseManifest | None = None,
    ) -> None:
        self._cache_root = cache_root
        self._staging_root = staging_root
        self._settings = settings
        self._manifest = manifest or load_envd_release_manifest()
        self._lock = Lock()
        self._selected: Path | None = None

    async def resolve(self) -> Path:
        """Return the exact verified current-target executable without PATH discovery."""

        if self._selected is not None:
            return self._selected
        target = current_envd_target()
        asset = self._manifest.targets.get(target)
        if asset is None:
            raise RuntimeResolutionError(
                "This Agent UI release has no agent-envd asset for the current Host target.",
                code="local_eip_target_unsupported",
                details={"target": target},
            )
        destination = self._cache_root / self._manifest.release / target / asset.executable
        async with self._lock:
            if self._selected is not None:
                return self._selected
            if await to_thread.run_sync(
                _is_verified_executable,
                destination,
                asset,
                self._manifest.release,
                self._settings.command_timeout_seconds,
            ):
                self._selected = destination
                return destination

            await to_thread.run_sync(_prepare_private_directories, self._cache_root, self._staging_root)
            archive_path = self._staging_root / f"{asset.archive}.{uuid4().hex}.download"
            candidate_path = self._staging_root / f"{asset.executable}.{uuid4().hex}.candidate"
            try:
                await _download_archive(
                    url=f"{self._manifest.base_url.rstrip('/')}/{asset.archive}",
                    destination=archive_path,
                    asset=asset,
                    timeout_seconds=self._settings.download_timeout_seconds,
                    max_archive_bytes=self._settings.max_archive_bytes,
                )
                await to_thread.run_sync(_extract_executable, archive_path, candidate_path, asset)
                if not await to_thread.run_sync(
                    _is_verified_executable,
                    candidate_path,
                    asset,
                    self._manifest.release,
                    self._settings.command_timeout_seconds,
                ):
                    raise RuntimeResolutionError(
                        "The acquired agent-envd executable failed identity verification.",
                        code="local_eip_runtime_invalid",
                        details={"target": target, "release": self._manifest.release},
                    )
                await to_thread.run_sync(_publish_executable, candidate_path, destination)
            except RuntimeResolutionError:
                raise
            except Exception as exc:
                raise RuntimeResolutionError(
                    "The package-selected agent-envd runtime could not be acquired.",
                    code="local_eip_runtime_acquisition_failed",
                    details={"target": target, "release": self._manifest.release},
                ) from exc
            finally:
                await to_thread.run_sync(_remove_if_present, archive_path)
                await to_thread.run_sync(_remove_if_present, candidate_path)
            self._selected = destination
            return destination


def load_envd_release_manifest() -> EnvdReleaseManifest:
    """Load and strictly validate the release manifest shipped in this package."""

    try:
        raw = files("a13n_ui").joinpath(_MANIFEST_RESOURCE).read_text(encoding="utf-8")
        value = json.loads(raw)
        return EnvdReleaseManifest.model_validate(value, strict=True)
    except (OSError, ValueError, ValidationError) as exc:
        raise RuntimeResolutionError(
            "The packaged agent-envd release manifest is missing or invalid.",
            code="local_eip_manifest_invalid",
        ) from exc


def current_envd_target() -> str:
    """Map the current supported Host to one canonical agent-envd release target."""

    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine)
    operating_system = {
        "darwin": "apple-darwin",
        "linux": "unknown-linux-gnu",
        "windows": "pc-windows-msvc",
    }.get(system)
    if architecture is None or operating_system is None:
        raise RuntimeResolutionError(
            "Local EIP is unavailable on this Host target.",
            code="local_eip_target_unsupported",
            details={"system": system, "machine": machine},
        )
    return f"{architecture}-{operating_system}"


async def _download_archive(
    *,
    url: str,
    destination: Path,
    asset: EnvdReleaseAsset,
    timeout_seconds: float,
    max_archive_bytes: int,
) -> None:
    if asset.size > max_archive_bytes:
        raise RuntimeResolutionError(
            "The selected agent-envd archive exceeds the configured acquisition limit.",
            code="local_eip_runtime_limit",
        )
    digest = hashlib.sha256()
    size = 0
    try:
        async with httpx2.AsyncClient(
            follow_redirects=True,
            timeout=httpx2.Timeout(timeout_seconds),
            headers={"User-Agent": "a13n-ui-agent-envd-runtime"},
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > max_archive_bytes:
                    raise RuntimeResolutionError(
                        "The selected agent-envd archive exceeds the configured acquisition limit.",
                        code="local_eip_runtime_limit",
                    )
                async with await open_file(destination, "wb") as output:
                    async for chunk in response.aiter_bytes(_DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        if size > max_archive_bytes or size > asset.size:
                            raise RuntimeResolutionError(
                                "The agent-envd download exceeded its pinned size.",
                                code="local_eip_runtime_integrity",
                            )
                        digest.update(chunk)
                        await output.write(chunk)
    except RuntimeResolutionError:
        raise
    except (httpx2.HTTPError, OSError, ValueError) as exc:
        raise RuntimeResolutionError(
            "The selected agent-envd archive could not be downloaded.",
            code="local_eip_runtime_download_failed",
        ) from exc
    if size != asset.size or digest.hexdigest() != asset.sha256:
        raise RuntimeResolutionError(
            "The downloaded agent-envd archive does not match the packaged manifest.",
            code="local_eip_runtime_integrity",
        )


def _extract_executable(archive: Path, destination: Path, asset: EnvdReleaseAsset) -> None:
    if asset.archive_format == "zip":
        with zipfile.ZipFile(archive) as bundle:
            try:
                member = bundle.getinfo(asset.executable)
            except KeyError as exc:
                raise RuntimeResolutionError(
                    "The agent-envd archive has no expected executable.",
                    code="local_eip_runtime_integrity",
                ) from exc
            if member.is_dir() or member.file_size != asset.executable_size:
                raise RuntimeResolutionError(
                    "The agent-envd archive executable has invalid metadata.",
                    code="local_eip_runtime_integrity",
                )
            with bundle.open(member) as source, destination.open("wb") as output:
                _copy_bounded(source, output, asset.executable_size)
    else:
        with tarfile.open(archive, mode="r:gz") as bundle:
            try:
                member = bundle.getmember(asset.executable)
            except KeyError as exc:
                raise RuntimeResolutionError(
                    "The agent-envd archive has no expected executable.",
                    code="local_eip_runtime_integrity",
                ) from exc
            if not member.isfile() or member.size != asset.executable_size:
                raise RuntimeResolutionError(
                    "The agent-envd archive executable has invalid metadata.",
                    code="local_eip_runtime_integrity",
                )
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeResolutionError(
                    "The agent-envd archive executable is unreadable.",
                    code="local_eip_runtime_integrity",
                )
            with source, destination.open("wb") as output:
                _copy_bounded(source, output, asset.executable_size)
    if os.name != "nt":
        destination.chmod(0o700)


def _copy_bounded(source: IO[bytes], output: IO[bytes], expected_size: int) -> None:
    size = 0
    while True:
        chunk = source.read(_DOWNLOAD_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > expected_size:
            raise RuntimeResolutionError(
                "The agent-envd archive executable exceeded its pinned size.",
                code="local_eip_runtime_integrity",
            )
        output.write(chunk)
    if size != expected_size:
        raise RuntimeResolutionError(
            "The agent-envd archive executable did not match its pinned size.",
            code="local_eip_runtime_integrity",
        )


def _is_verified_executable(
    path: Path,
    asset: EnvdReleaseAsset,
    release: str,
    command_timeout_seconds: float,
) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != asset.executable_size:
            return False
        if _sha256_file(path) != asset.executable_sha256:
            return False
        if os.name != "nt" and not os.access(path, os.X_OK):
            return False
        completed = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            timeout=command_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return (
        completed.returncode == 0 and completed.stdout == f"agent-envd {release}\n".encode() and completed.stderr == b""
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_private_directories(cache_root: Path, staging_root: Path) -> None:
    for directory in (cache_root, staging_root):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)


def _publish_executable(candidate: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        destination.parent.chmod(0o700)
        candidate.chmod(0o700)
    os.replace(candidate, destination)


def _remove_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


__all__ = [
    "EnvdReleaseAsset",
    "EnvdReleaseManifest",
    "ManagedEnvdRuntime",
    "current_envd_target",
    "load_envd_release_manifest",
]
