from __future__ import annotations

import io
import os
import tarfile
import zipfile
from pathlib import Path

import anyio
import httpx2
import pytest
from a13n_harness_ui import managed_runtime
from a13n_harness_ui.errors import RuntimeResolutionError
from a13n_harness_ui.managed_runtime import ManagedEnvdRuntime, current_envd_target, load_envd_version
from a13n_harness_ui.settings import EnvdRuntimeSettings

pytestmark = [pytest.mark.anyio, pytest.mark.xdist_group("infrastructure")]


def _runtime(tmp_path: Path, version: str = "9.8.7") -> ManagedEnvdRuntime:
    return ManagedEnvdRuntime(
        cache_root=tmp_path / "cache",
        staging_root=tmp_path / "staging",
        settings=EnvdRuntimeSettings(command_timeout_seconds=2),
        version=version,
    )


def _archive(path: Path, content: bytes, executable: str = "a13n-envd") -> bytes:
    if executable.endswith(".exe"):
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr(executable, content)
    else:
        with tarfile.open(path, "w:gz") as bundle:
            member = tarfile.TarInfo(executable)
            member.size = len(content)
            member.mode = 0o700
            bundle.addfile(member, io.BytesIO(content))
    return path.read_bytes()


def _executable(version: str) -> bytes:
    return f"#!/bin/sh\nprintf 'a13n-envd {version}\\n'\n".encode()


async def test_unselected_release_fails_before_download(tmp_path: Path) -> None:
    with pytest.raises(RuntimeResolutionError, match="no selected") as captured:
        await _runtime(tmp_path, "0.0.0").resolve()
    assert captured.value.code == "local_eip_release_unselected"
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "staging").exists()


def test_packaged_version_has_one_source() -> None:
    path = Path(__file__).parents[1] / "a13n_harness_ui/assets/a13n-envd-version.txt"
    assert load_envd_version() == path.read_text().strip()


@pytest.mark.parametrize("version", ["", "../0.0.4", "v0.0.4", "01.0.4", "0.0.4rc1", "0.0.4-rc.0"])
def test_invalid_version_is_rejected(tmp_path: Path, version: str) -> None:
    with pytest.raises(RuntimeResolutionError) as captured:
        _runtime(tmp_path, version)
    assert captured.value.code == "local_eip_version_invalid"


def test_missing_packaged_version_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managed_runtime, "files", lambda package: tmp_path)
    with pytest.raises(RuntimeResolutionError) as captured:
        load_envd_version()
    assert captured.value.code == "local_eip_version_invalid"


@pytest.mark.parametrize(
    "system,suffix", [("Darwin", "apple-darwin"), ("Linux", "unknown-linux-gnu"), ("Windows", "pc-windows-msvc")]
)
@pytest.mark.parametrize(
    "machine,arch", [("amd64", "x86_64"), ("x86_64", "x86_64"), ("arm64", "aarch64"), ("aarch64", "aarch64")]
)
def test_supported_targets(monkeypatch: pytest.MonkeyPatch, system: str, suffix: str, machine: str, arch: str) -> None:
    monkeypatch.setattr(managed_runtime.platform, "system", lambda: system)
    monkeypatch.setattr(managed_runtime.platform, "machine", lambda: machine)
    assert current_envd_target() == f"{arch}-{suffix}"


@pytest.mark.parametrize("system,machine", [("FreeBSD", "amd64"), ("Linux", "riscv64")])
def test_unsupported_target_is_rejected(monkeypatch: pytest.MonkeyPatch, system: str, machine: str) -> None:
    monkeypatch.setattr(managed_runtime.platform, "system", lambda: system)
    monkeypatch.setattr(managed_runtime.platform, "machine", lambda: machine)
    with pytest.raises(RuntimeResolutionError) as captured:
        current_envd_target()
    assert captured.value.code == "local_eip_target_unsupported"


@pytest.mark.parametrize("target", ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu", "aarch64-pc-windows-msvc"])
@pytest.mark.parametrize("version", ["0.0.4", "0.0.5-rc.1"])
async def test_archive_url_is_derived_from_version_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, version: str
) -> None:
    windows = target.endswith("windows-msvc")
    executable = "a13n-envd.exe" if windows else "a13n-envd"
    extension = "zip" if windows else "tar.gz"
    content = _archive(tmp_path / "archive", b"fixture", executable)
    urls = []

    async def download(*, url: str, destination: Path, **kwargs) -> None:
        urls.append(url)
        destination.write_bytes(content)

    monkeypatch.setattr(managed_runtime, "current_envd_target", lambda: target)
    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    monkeypatch.setattr(managed_runtime, "_matches_version", lambda path, *args: path.is_file())
    path = await _runtime(tmp_path, version).resolve()
    assert path == tmp_path / "cache" / version / target / executable
    assert urls == [
        f"https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v{version}/a13n-envd-{version}-{target}.{extension}"
    ]
    assert path.read_bytes() == b"fixture"


@pytest.mark.skipif(os.name == "nt", reason="fixture executable is a POSIX script")
async def test_acquisition_is_shared_and_cache_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = _archive(tmp_path / "archive", _executable("9.8.7"))
    downloads = 0

    async def download(*, destination: Path, **kwargs) -> None:
        nonlocal downloads
        downloads += 1
        await anyio.sleep(0)
        destination.write_bytes(content)

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    runtime = _runtime(tmp_path)
    paths = []

    async def resolve() -> None:
        paths.append(await runtime.resolve())

    async with anyio.create_task_group() as group:
        group.start_soon(resolve)
        group.start_soon(resolve)
    reopened = await _runtime(tmp_path).resolve()
    assert paths == [reopened, reopened]
    assert downloads == 1
    assert not list((tmp_path / "staging").iterdir())


@pytest.mark.skipif(os.name == "nt", reason="fixture executable is a POSIX script")
async def test_wrong_version_preserves_cache_and_allows_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = tmp_path / "cache/9.8.7" / current_envd_target() / "a13n-envd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_executable("9.8.6"))
    cached.chmod(0o700)
    bad_content = _archive(tmp_path / "bad", _executable("9.8.6"))
    good_content = _archive(tmp_path / "good", _executable("9.8.7"))
    downloads = 0

    async def download(*, destination: Path, **kwargs) -> None:
        nonlocal downloads
        downloads += 1
        destination.write_bytes(bad_content if downloads == 1 else good_content)

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    runtime = _runtime(tmp_path)
    with pytest.raises(RuntimeResolutionError) as captured:
        await runtime.resolve()
    assert captured.value.code == "local_eip_runtime_invalid"
    assert cached.read_bytes() == _executable("9.8.6")
    assert not list((tmp_path / "staging").iterdir())
    assert await runtime.resolve() == cached
    assert cached.read_bytes() == _executable("9.8.7")
    assert downloads == 2


async def test_failed_download_is_cleaned_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def download(*, destination: Path, **kwargs) -> None:
        destination.write_bytes(b"partial")
        raise RuntimeResolutionError("download failed", code="local_eip_runtime_download_failed")

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    with pytest.raises(RuntimeResolutionError) as captured:
        await _runtime(tmp_path).resolve()
    assert captured.value.code == "local_eip_runtime_download_failed"
    assert not list((tmp_path / "staging").iterdir())
    assert not list((tmp_path / "cache").rglob("a13n-envd"))


@pytest.mark.parametrize("executable", ["a13n-envd", "a13n-envd.exe"])
def test_extraction_is_bounded(tmp_path: Path, executable: str) -> None:
    path = tmp_path / "archive"
    _archive(path, b"x" * 2048, executable)
    destination = tmp_path / "candidate"
    with pytest.raises(RuntimeResolutionError):
        managed_runtime._extract_executable(path, destination, executable, 1024)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["link", "directory", "traversal"])
def test_tar_extracts_only_regular_named_executable(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "archive"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../outside" if kind == "traversal" else "a13n-envd")
        member.type = tarfile.SYMTYPE if kind == "link" else tarfile.DIRTYPE
        member.linkname = "../outside"
        bundle.addfile(member)
    with pytest.raises((RuntimeResolutionError, KeyError)):
        managed_runtime._extract_executable(archive, tmp_path / "candidate", "a13n-envd", 1024)
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("advertise_size", [False, True])
async def test_download_limit_with_and_without_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, advertise_size: bool
) -> None:
    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b"1234"
            yield b"5678"

    def respond(request):
        headers = {"content-length": "8"} if advertise_size else {}
        return httpx2.Response(200, headers=headers, stream=Body())

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        managed_runtime.httpx2,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx2.MockTransport(respond), **kwargs),
    )
    with pytest.raises(RuntimeResolutionError) as captured:
        await managed_runtime._download_archive(
            url="https://example.test/asset", destination=tmp_path / "download", timeout_seconds=1, max_bytes=6
        )
    assert captured.value.code == "local_eip_runtime_limit"
