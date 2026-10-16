"""Version-selected acquisition of the a13n-envd native runtime."""

from __future__ import annotations

import os
import platform
import re
import stat
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import IO, Literal
from uuid import uuid4

import httpx2
from a13n_harness.http import outbound_tls_verify
from anyio import CancelScope, Lock, open_file, to_thread
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import RuntimeResolutionError
from a13n_harness_ui.settings import EnvdRuntimeSettings

_RELEASE_VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
_VERSION_RECOVERY = (
    "Reinstall a13n-harness-ui with its matching a13n-envd-client dependency, "
    "or set HarnessUiSettings.envd_runtime.executable to an absolute path to a locally built a13n-envd."
)
_RELEASE_URL = "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v"
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


class EnvdRuntimeStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str
    source: Literal["managed", "custom"]
    required_version: str | None = None
    verified_version: str | None = None
    cached_versions: tuple[str, ...] = ()
    executable: str | None = None
    installed: bool = False
    running_devices: int = 0
    phase: Literal["idle", "checking", "downloading", "verifying"] = "idle"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    error: str | None = None


class ManagedEnvdRuntime:
    """Acquire and cache the package-selected executable by version and platform."""

    def __init__(
        self,
        *,
        cache_root: Path,
        staging_root: Path,
        settings: EnvdRuntimeSettings,
        version: str | None = None,
    ) -> None:
        self._cache_root = cache_root
        self._staging_root = staging_root
        self._settings = settings
        # Metadata is needed only for managed acquisition, not Native or an explicit executable override.
        self._version = None if version is None else _validate_version(version)
        self._lock = Lock()
        self._selected: Path | None = None
        self._phase: Literal["idle", "checking", "downloading", "verifying"] = "idle"
        self._downloaded_bytes = 0
        self._total_bytes: int | None = None
        self._error: str | None = None

    async def status(self) -> EnvdRuntimeStatus:
        """Inspect files and metadata only; never execute, download or launch envd."""
        version = None
        path = self._settings.executable
        error = self._error
        cached_versions: tuple[str, ...] = ()
        try:
            version = self._version or await to_thread.run_sync(load_envd_version)
            if path is None and version != "0.0.0":
                target = current_envd_target()
                cached_versions = await to_thread.run_sync(_cached_versions, self._cache_root, target)
                path = (
                    self._cache_root
                    / version
                    / target
                    / ("a13n-envd.exe" if target.endswith("windows-msvc") else "a13n-envd")
                )
            elif path is None:
                error = "Source build: choose a locally built envd executable in advanced settings."
        except RuntimeResolutionError as exc:
            error = str(exc)
        return EnvdRuntimeStatus(
            host=platform.node(),
            source="custom" if self._settings.executable else "managed",
            required_version=version,
            verified_version=version if self._selected is not None else None,
            cached_versions=cached_versions,
            executable=None if path is None else str(path),
            installed=path is not None and await to_thread.run_sync(path.is_file),
            phase=self._phase,
            downloaded_bytes=self._downloaded_bytes,
            total_bytes=self._total_bytes,
            error=error,
        )

    def _progress(self, downloaded: int, total: int | None) -> None:
        self._downloaded_bytes, self._total_bytes = downloaded, total

    async def resolve(self, *, reinstall: bool = False) -> Path:
        async with self._lock:
            self._error = None
            self._phase = "checking"
            self._progress(0, None)
            try:
                return await self._resolve(reinstall=reinstall)
            except Exception as exc:
                self._error = str(exc)
                raise
            finally:
                self._phase = "idle"

    async def _resolve(self, *, reinstall: bool = False) -> Path:
        """Return the selected current-target executable without PATH discovery."""

        if self._selected is not None and not reinstall:
            return self._selected
        if self._version is None:
            self._version = await to_thread.run_sync(load_envd_version)
        if self._version == "0.0.0":
            raise RuntimeResolutionError(
                "The installed a13n-envd-client is a source build (0.0.0) with no selected a13n-envd release. "
                "Set HarnessUiSettings.envd_runtime.executable to an absolute path to a locally built a13n-envd.",
                code="local_eip_release_unselected",
            )
        target = current_envd_target()
        windows = target.endswith("windows-msvc")
        executable = "a13n-envd.exe" if windows else "a13n-envd"
        extension = "zip" if windows else "tar.gz"
        archive = f"a13n-envd-{self._version}-{target}.{extension}"
        destination = self._cache_root / self._version / target / executable
        if not reinstall and await to_thread.run_sync(
            _matches_version, destination, self._version, self._settings.command_timeout_seconds
        ):
            self._selected = destination
            return destination

        await to_thread.run_sync(_prepare_private_directories, self._cache_root, self._staging_root)
        archive_path = self._staging_root / f"{archive}.{uuid4().hex}.download"
        candidate_path = self._staging_root / f"{executable}.{uuid4().hex}.candidate"
        try:
            self._phase = "downloading"
            await _download_archive(
                url=f"{_RELEASE_URL}{self._version}/{archive}",
                destination=archive_path,
                timeout_seconds=self._settings.download_timeout_seconds,
                max_bytes=self._settings.max_archive_bytes,
                progress=self._progress,
            )
            self._phase = "verifying"
            await to_thread.run_sync(
                _extract_executable,
                archive_path,
                candidate_path,
                executable,
                self._settings.max_archive_bytes,
            )
            if not await to_thread.run_sync(
                _matches_version, candidate_path, self._version, self._settings.command_timeout_seconds
            ):
                raise RuntimeResolutionError(
                    "The acquired a13n-envd executable does not report the selected version.",
                    code="local_eip_runtime_invalid",
                    details={"target": target, "release": self._version},
                )
            await to_thread.run_sync(_publish_executable, candidate_path, destination)
        except RuntimeResolutionError:
            raise
        except Exception as exc:
            raise RuntimeResolutionError(
                "The package-selected a13n-envd runtime could not be acquired.",
                code="local_eip_runtime_acquisition_failed",
                details={"target": target, "release": self._version},
            ) from exc
        finally:
            with CancelScope(shield=True):
                await to_thread.run_sync(_remove_if_present, archive_path)
                await to_thread.run_sync(_remove_if_present, candidate_path)
        self._selected = destination
        return destination


def _cached_versions(cache_root: Path, target: str) -> tuple[str, ...]:
    """List target-specific cached releases without executing any binary."""
    executable = "a13n-envd.exe" if target.endswith("windows-msvc") else "a13n-envd"
    try:
        releases = tuple(cache_root.iterdir())
    except FileNotFoundError:
        return ()
    return tuple(
        sorted(
            release.name
            for release in releases
            if re.fullmatch(rf"{_RELEASE_VERSION_PATTERN}(?:-rc\.[1-9][0-9]*)?", release.name)
            and (release / target / executable).is_file()
        )
    )


def _validate_version(version: str) -> str:
    if re.fullmatch(rf"{_RELEASE_VERSION_PATTERN}(?:-rc\.[1-9][0-9]*)?", version) is None:
        raise RuntimeResolutionError(
            "The selected a13n-envd version must use X.Y.Z or X.Y.Z-rc.N (N >= 1).",
            code="local_eip_version_invalid",
        )
    return version


def load_envd_version() -> str:
    """Select the native release co-released with the installed client distribution."""

    try:
        version = metadata.version("a13n-envd-client")
    except (metadata.PackageNotFoundError, OSError, UnicodeError) as exc:
        raise RuntimeResolutionError(
            f"The installed a13n-envd-client distribution metadata is missing or unreadable. {_VERSION_RECOVERY}",
            code="local_eip_version_invalid",
        ) from exc
    # Accept only canonical Python stable/RC release forms, not the wider PEP 440 grammar.
    match = (
        re.fullmatch(rf"(?P<release>{_RELEASE_VERSION_PATTERN})(?:rc(?P<rc>[1-9][0-9]*))?", version)
        if isinstance(version, str)
        else None
    )
    if match is None:
        raise RuntimeResolutionError(
            f"The installed a13n-envd-client version must use X.Y.Z or X.Y.ZrcN (N >= 1). {_VERSION_RECOVERY}",
            code="local_eip_version_invalid",
        )
    release = match.group("release")
    rc = match.group("rc")
    return release if rc is None else f"{release}-rc.{rc}"


def current_envd_target() -> str:
    """Map the current supported Host to one canonical a13n-envd release target."""

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
    timeout_seconds: float,
    max_bytes: int,
    progress: Callable[[int, int | None], None] | None = None,
) -> None:
    size = 0
    try:
        async with httpx2.AsyncClient(
            verify=outbound_tls_verify(),
            follow_redirects=True,
            timeout=httpx2.Timeout(timeout_seconds),
            headers={"User-Agent": "a13n-harness-ui-a13n-envd-runtime"},
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise RuntimeResolutionError(
                        "The selected a13n-envd archive exceeds the acquisition limit.",
                        code="local_eip_runtime_limit",
                    )
                async with await open_file(destination, "wb") as output:
                    async for chunk in response.aiter_bytes(_DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        if size > max_bytes:
                            raise RuntimeResolutionError(
                                "The a13n-envd download exceeded the acquisition limit.",
                                code="local_eip_runtime_limit",
                            )
                        await output.write(chunk)
                        if progress is not None:
                            progress(size, None if content_length is None else int(content_length))
    except RuntimeResolutionError:
        raise
    except (httpx2.HTTPError, OSError, ValueError) as exc:
        raise RuntimeResolutionError(
            "The selected a13n-envd archive could not be downloaded.",
            code="local_eip_runtime_download_failed",
        ) from exc


def _extract_executable(archive: Path, destination: Path, executable: str, max_bytes: int) -> None:
    if executable.endswith(".exe"):
        with zipfile.ZipFile(archive) as bundle:
            member = bundle.getinfo(executable)
            if member.is_dir() or not 0 < member.file_size <= max_bytes:
                raise RuntimeResolutionError(
                    "The a13n-envd archive executable is invalid or exceeds the acquisition limit.",
                    code="local_eip_runtime_invalid",
                )
            with bundle.open(member) as source, destination.open("wb") as output:
                _copy_bounded(source, output, max_bytes)
    else:
        with tarfile.open(archive, mode="r:gz") as bundle:
            member = bundle.getmember(executable)
            if not member.isfile() or not 0 < member.size <= max_bytes:
                raise RuntimeResolutionError(
                    "The a13n-envd archive executable is invalid or exceeds the acquisition limit.",
                    code="local_eip_runtime_invalid",
                )
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeResolutionError(
                    "The a13n-envd archive executable is unreadable.", code="local_eip_runtime_invalid"
                )
            with source, destination.open("wb") as output:
                _copy_bounded(source, output, max_bytes)
    if os.name != "nt":
        destination.chmod(0o700)


def _copy_bounded(source: IO[bytes], output: IO[bytes], max_bytes: int) -> None:
    size = 0
    while chunk := source.read(_DOWNLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise RuntimeResolutionError(
                "The a13n-envd executable exceeded the acquisition limit.", code="local_eip_runtime_limit"
            )
        output.write(chunk)


def _matches_version(path: Path, version: str, command_timeout_seconds: float) -> bool:
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            return False
        if os.name != "nt" and not os.access(path, os.X_OK):
            return False
        completed = subprocess.run(
            [path, "--version"], check=False, capture_output=True, timeout=command_timeout_seconds
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return (
        completed.returncode == 0 and completed.stdout == f"a13n-envd {version}\n".encode() and completed.stderr == b""
    )


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


__all__ = ["ManagedEnvdRuntime", "current_envd_target", "load_envd_version"]
