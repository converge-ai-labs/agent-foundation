"""Verified Host-native runtime resolution for Agent UI Environment providers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

import httpx2
from a13n_environment_provider import DirectLocalProviderRuntime, EnvironmentProviderRuntime
from anyio import CancelScope, Lock, fail_after, to_thread
from pydantic import JsonValue

from a13n_ui.configuration import EnvdRuntimeAsset, EnvdRuntimeManifest
from a13n_ui.configuration.runtime_manifest import load_envd_runtime_manifest
from a13n_ui.errors import RuntimeResolutionError
from a13n_ui.settings import EnvdRuntimeSettings
from a13n_ui.storage.layout import StorageLayout


@dataclass(frozen=True, slots=True)
class ResolvedEnvdExecutable:
    """One verified exact agent-envd executable selected for this process."""

    path: Path
    release: str
    target: str
    managed: bool
    isolation_report: dict[str, JsonValue]


class EnvdExecutableResolver:
    """Resolve, verify, probe, and cache one package-pinned agent-envd binary."""

    def __init__(
        self,
        *,
        layout: StorageLayout,
        settings: EnvdRuntimeSettings,
        executable_override: Path | None,
        manifest: EnvdRuntimeManifest | None = None,
    ) -> None:
        self._layout = layout
        self._settings = settings
        self._override = executable_override
        self._manifest = manifest
        self._lock = Lock()
        self._resolved: ResolvedEnvdExecutable | None = None

    async def resolve(self) -> ResolvedEnvdExecutable:
        async with self._lock:
            if self._resolved is not None:
                await self._verify_executable(self._resolved.path, self._selected_asset().executable_sha256)
                return self._resolved
            manifest = self._required_manifest()
            asset = self._asset_for_current_target(manifest)
            if self._override is not None:
                path = self._override
                await self._validate_override(path)
                managed = False
            else:
                path = (
                    self._layout.runtimes / "agent-envd" / manifest.envd_release / asset.target / asset.executable_name
                )
                try:
                    await self._verify_executable(path, asset.executable_sha256)
                except RuntimeResolutionError:
                    await self._acquire_managed(path, asset)
                managed = True
            await self._verify_version(path, manifest.envd_release)
            report = await self._probe_isolation(path)
            resolved = ResolvedEnvdExecutable(
                path=path.resolve(strict=True),
                release=manifest.envd_release,
                target=asset.target,
                managed=managed,
                isolation_report=report,
            )
            self._resolved = resolved
            return resolved

    def _required_manifest(self) -> EnvdRuntimeManifest:
        manifest = self._manifest if self._manifest is not None else load_envd_runtime_manifest()
        if manifest is None:
            raise RuntimeResolutionError(
                "This Agent UI build does not contain an agent-envd runtime manifest.",
                code="envd_runtime_manifest_missing",
            )
        return manifest

    def _selected_asset(self) -> EnvdRuntimeAsset:
        return self._asset_for_current_target(self._required_manifest())

    def _asset_for_current_target(self, manifest: EnvdRuntimeManifest) -> EnvdRuntimeAsset:
        target = _current_target()
        selected = next((asset for asset in manifest.assets if asset.target == target), None)
        if selected is None:
            raise RuntimeResolutionError(
                "The current Host target is absent from the agent-envd manifest.",
                code="envd_target_unsupported",
                details={"target": target},
            )
        return selected

    async def _validate_override(self, path: Path) -> None:
        def inspect() -> None:
            try:
                metadata = path.stat()
            except OSError as exc:
                raise RuntimeResolutionError(
                    "The explicit agent-envd executable override is unavailable.",
                    code="envd_override_unavailable",
                ) from exc
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeResolutionError(
                    "The explicit agent-envd executable override is not a regular file.",
                    code="envd_override_invalid",
                )
            if os.name != "nt" and not os.access(path, os.X_OK):
                raise RuntimeResolutionError(
                    "The explicit agent-envd executable override is not executable.",
                    code="envd_override_invalid",
                )

        await to_thread.run_sync(inspect)

    async def _verify_executable(self, path: Path, expected_sha256: str) -> None:
        def verify() -> None:
            try:
                metadata = path.stat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("not a regular file")
                digest = _sha256_file(path)
            except OSError as exc:
                raise RuntimeResolutionError(
                    "The managed agent-envd executable is absent or unreadable.",
                    code="envd_executable_unavailable",
                ) from exc
            if digest != expected_sha256:
                raise RuntimeResolutionError(
                    "The agent-envd executable hash does not match its package manifest.",
                    code="envd_executable_hash_mismatch",
                )

        await to_thread.run_sync(verify)

    async def _acquire_managed(self, target: Path, asset: EnvdRuntimeAsset) -> None:
        content = await self._download(asset)
        if hashlib.sha256(content).hexdigest() != asset.archive_sha256:
            raise RuntimeResolutionError(
                "The downloaded agent-envd archive hash does not match its package manifest.",
                code="envd_archive_hash_mismatch",
            )
        stage = self._layout.staging / f"envd-{uuid4().hex}"
        await to_thread.run_sync(self._publish_archive_executable, content, asset, stage, target)
        await self._verify_executable(target, asset.executable_sha256)

    async def _download(self, asset: EnvdRuntimeAsset) -> bytes:
        try:
            timeout = httpx2.Timeout(self._settings.download_timeout_seconds)
            async with httpx2.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", asset.archive_url) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._settings.max_archive_bytes:
                            raise RuntimeResolutionError(
                                "The agent-envd archive exceeds the configured size limit.",
                                code="envd_archive_too_large",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except RuntimeResolutionError:
            raise
        except httpx2.HTTPError as exc:
            raise RuntimeResolutionError(
                "The exact agent-envd release archive could not be downloaded.",
                code="envd_download_failed",
            ) from exc

    def _publish_archive_executable(
        self,
        content: bytes,
        asset: EnvdRuntimeAsset,
        stage: Path,
        target: Path,
    ) -> None:
        stage.mkdir(mode=0o700, parents=False, exist_ok=False)
        archive = stage / asset.archive_name
        executable = stage / asset.executable_name
        try:
            _write_file(archive, content, mode=0o600)
            extracted = _extract_one_executable(archive, asset.executable_name)
            _write_file(executable, extracted, mode=0o700)
            if _sha256_file(executable) != asset.executable_sha256:
                raise RuntimeResolutionError(
                    "The extracted agent-envd executable hash does not match its package manifest.",
                    code="envd_executable_hash_mismatch",
                )
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(executable, target)
            if os.name != "nt":
                target.chmod(0o700)
            _sync_directory(target.parent)
        finally:
            if stage.exists():
                for child in stage.iterdir():
                    child.unlink(missing_ok=True)
                stage.rmdir()

    async def _verify_version(self, path: Path, expected_release: str) -> None:
        stdout = await self._run_command(path, "--version")
        reported = stdout.decode("utf-8", errors="strict").strip()
        if reported not in {expected_release, f"agent-envd {expected_release}"}:
            raise RuntimeResolutionError(
                "agent-envd reported a release other than the package manifest selection.",
                code="envd_version_mismatch",
                details={"expected_release": expected_release},
            )

    async def _probe_isolation(self, path: Path) -> dict[str, JsonValue]:
        stdout = await self._run_command(path, "isolation", "probe", "--json")
        try:
            value = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeResolutionError(
                "agent-envd returned an invalid isolation probe report.",
                code="envd_isolation_probe_invalid",
            ) from exc
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise RuntimeResolutionError(
                "agent-envd returned an invalid isolation probe report.",
                code="envd_isolation_probe_invalid",
            )
        required = ("ready", "isolation", "filesystem_containment", "process_containment")
        if any(value.get(key) is not True for key in required):
            raise RuntimeResolutionError(
                "agent-envd native isolation is unavailable on this Host.",
                code="envd_isolation_unavailable",
            )
        return value

    async def _run_command(self, path: Path, *arguments: str) -> bytes:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(path),
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            with fail_after(self._settings.command_timeout_seconds):
                stdout, stderr = await process.communicate()
        except TimeoutError as exc:
            await _kill_process(process)
            raise RuntimeResolutionError(
                "agent-envd runtime validation timed out.",
                code="envd_command_timeout",
            ) from exc
        except asyncio.CancelledError:
            await _kill_process(process)
            raise
        except OSError as exc:
            raise RuntimeResolutionError(
                "agent-envd could not be executed.",
                code="envd_executable_unavailable",
            ) from exc
        assert process is not None
        if process.returncode != 0:
            raise RuntimeResolutionError(
                "agent-envd runtime validation failed.",
                code="envd_command_failed",
                details={"return_code": process.returncode, "stderr_bytes": min(len(stderr), 4096)},
            )
        return stdout


class ProviderRuntimeResolver:
    """Construct fresh provider-owned runtime collaborators without fallback."""

    async def resolve(self, provider_key: str) -> EnvironmentProviderRuntime:
        if provider_key == "a13n.direct-local":
            return DirectLocalProviderRuntime()
        if provider_key == "a13n.local-envd":
            raise RuntimeResolutionError(
                "The installed Environment Provider release does not implement a13n.local-envd.",
                code="local_envd_provider_unavailable",
            )
        raise RuntimeResolutionError(
            "Agent UI does not support this Environment provider runtime.",
            code="provider_runtime_unsupported",
            details={"provider_key": provider_key},
        )


async def _kill_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    with CancelScope(shield=True):
        process.kill()
        await process.wait()


def _current_target() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"linux": "linux", "darwin": "darwin", "windows": "windows"}.get(system)
    architecture = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }.get(machine)
    if os_name is None or architecture is None:
        raise RuntimeResolutionError(
            "The current Host target is unsupported for Local Sandbox.",
            code="envd_target_unsupported",
            details={"system": system, "machine": machine},
        )
    return f"{os_name}-{architecture}"


def _extract_one_executable(archive: Path, executable_name: str) -> bytes:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            matches = [item for item in bundle.infolist() if PurePosixPath(item.filename).name == executable_name]
            if len(matches) != 1 or matches[0].is_dir() or _zip_symlink(matches[0]):
                raise RuntimeResolutionError(
                    "The agent-envd archive does not contain one regular expected executable.",
                    code="envd_archive_invalid",
                )
            return bundle.read(matches[0])
    try:
        with tarfile.open(archive, mode="r:*") as bundle:
            matches = [item for item in bundle.getmembers() if PurePosixPath(item.name).name == executable_name]
            if len(matches) != 1 or not matches[0].isfile():
                raise RuntimeResolutionError(
                    "The agent-envd archive does not contain one regular expected executable.",
                    code="envd_archive_invalid",
                )
            stream = bundle.extractfile(matches[0])
            if stream is None:
                raise RuntimeResolutionError(
                    "The agent-envd archive executable could not be read.",
                    code="envd_archive_invalid",
                )
            return stream.read()
    except tarfile.TarError as exc:
        raise RuntimeResolutionError(
            "The downloaded agent-envd archive is malformed.",
            code="envd_archive_invalid",
        ) from exc


def _zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _write_file(path: Path, content: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "EnvdExecutableResolver",
    "ProviderRuntimeResolver",
    "ResolvedEnvdExecutable",
]
