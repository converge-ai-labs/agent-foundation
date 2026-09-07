from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest
from a13n_harness_ui.errors import RuntimeResolutionError
from a13n_harness_ui.managed_runtime import (
    EnvdReleaseAsset,
    EnvdReleaseManifest,
    ManagedEnvdRuntime,
    current_envd_target,
    load_envd_release_manifest,
)
from a13n_harness_ui.settings import EnvdRuntimeSettings

pytestmark = [pytest.mark.anyio, pytest.mark.xdist_group("infrastructure")]


def test_packaged_manifest_covers_every_supported_target() -> None:
    manifest = load_envd_release_manifest()

    assert set(manifest.targets) == {
        "aarch64-apple-darwin",
        "aarch64-pc-windows-msvc",
        "aarch64-unknown-linux-gnu",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    }
    assert current_envd_target() in manifest.targets


@pytest.mark.skipif(os.name == "nt", reason="the fixture executable is a POSIX script")
async def test_managed_runtime_acquires_verifies_and_reuses_exact_cached_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = "9.8.7"
    executable = b"#!/bin/sh\nprintf 'a13n-envd 9.8.7\\n'\n"
    archive, archive_format, executable_name = _archive_for_current_target(tmp_path, executable)
    asset = EnvdReleaseAsset(
        archive=archive.name,
        archive_format=archive_format,
        sha256=_digest(archive.read_bytes()),
        size=archive.stat().st_size,
        executable=executable_name,
        executable_sha256=_digest(executable),
        executable_size=len(executable),
    )
    manifest = EnvdReleaseManifest(
        schema_version="1",
        release=release,
        base_url="https://github.com/example/project/releases/download/release/a13n-envd-v9.8.7",
        targets={current_envd_target(): asset},
    )
    downloads = 0

    async def copy_archive(**kwargs) -> None:
        nonlocal downloads
        downloads += 1
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr("a13n_harness_ui.managed_runtime._download_archive", copy_archive)
    settings = EnvdRuntimeSettings(command_timeout_seconds=2)
    resolver = ManagedEnvdRuntime(
        cache_root=tmp_path / "cache",
        staging_root=tmp_path / "staging",
        settings=settings,
        manifest=manifest,
    )

    first = await resolver.resolve()
    second = await resolver.resolve()
    reopened = await ManagedEnvdRuntime(
        cache_root=tmp_path / "cache",
        staging_root=tmp_path / "staging-2",
        settings=settings,
        manifest=manifest,
    ).resolve()

    assert first == second == reopened
    assert first.read_bytes() == executable
    assert os.access(first, os.X_OK)
    assert downloads == 1


@pytest.mark.skipif(os.name == "nt", reason="the fixture executable is a POSIX script")
async def test_managed_runtime_rejects_executable_not_matching_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = b"#!/bin/sh\nprintf 'a13n-envd 9.8.7\\n'\n"
    archive, archive_format, executable_name = _archive_for_current_target(tmp_path, executable)
    asset = EnvdReleaseAsset(
        archive=archive.name,
        archive_format=archive_format,
        sha256=_digest(archive.read_bytes()),
        size=archive.stat().st_size,
        executable=executable_name,
        executable_sha256="0" * 64,
        executable_size=len(executable),
    )
    manifest = EnvdReleaseManifest(
        schema_version="1",
        release="9.8.7",
        base_url="https://github.com/example/project/releases/download/release/a13n-envd-v9.8.7",
        targets={current_envd_target(): asset},
    )

    async def copy_archive(**kwargs) -> None:
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr("a13n_harness_ui.managed_runtime._download_archive", copy_archive)
    resolver = ManagedEnvdRuntime(
        cache_root=tmp_path / "cache",
        staging_root=tmp_path / "staging",
        settings=EnvdRuntimeSettings(command_timeout_seconds=2),
        manifest=manifest,
    )

    with pytest.raises(RuntimeResolutionError) as invalid:
        await resolver.resolve()

    assert invalid.value.code == "local_eip_runtime_invalid"
    assert list((tmp_path / "cache").rglob("a13n-envd")) == []


def _archive_for_current_target(tmp_path: Path, executable: bytes) -> tuple[Path, str, str]:
    target = current_envd_target()
    if target.endswith("windows-msvc"):
        path = tmp_path / f"a13n-envd-9.8.7-{target}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a13n-envd.exe", executable)
        return path, "zip", "a13n-envd.exe"

    path = tmp_path / f"a13n-envd-9.8.7-{target}.tar.gz"
    metadata = tarfile.TarInfo("a13n-envd")
    metadata.mode = 0o700
    metadata.size = len(executable)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(metadata, io.BytesIO(executable))
    return path, "tar.gz", "a13n-envd"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
