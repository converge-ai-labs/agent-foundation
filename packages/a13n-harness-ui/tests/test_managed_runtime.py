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
from a13n_harness_ui.environment_runtime import EnvironmentSnapshotReconstructor
from a13n_harness_ui.errors import RuntimeResolutionError
from a13n_harness_ui.managed_runtime import ManagedEnvdRuntime, current_envd_target, load_envd_version
from a13n_harness_ui.settings import EnvdRuntimeSettings

pytestmark = [pytest.mark.anyio, pytest.mark.xdist_group("infrastructure")]


def _runtime(tmp_path: Path, version: str | None = "9.8.7") -> ManagedEnvdRuntime:
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


async def test_cancelled_download_removes_staging_and_returns_to_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = anyio.Event()
    runtime = _runtime(tmp_path)

    async def download(*, destination: Path, **kwargs) -> None:
        destination.write_bytes(b"partial archive")
        started.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(runtime.resolve)
        await started.wait()
        assert (await runtime.status()).phase == "downloading"
        tasks.cancel_scope.cancel()
    assert list((tmp_path / "staging").iterdir()) == []
    status = await runtime.status()
    assert status.phase == "idle"
    assert not status.installed
    assert status.verified_version is None


async def test_unselected_release_fails_before_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: "0.0.0")
    runtime = _runtime(tmp_path, None)
    with pytest.raises(RuntimeResolutionError, match="no selected") as captured:
        await runtime.resolve()
    assert captured.value.code == "local_eip_release_unselected"
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize(
    "version,expected", [("0.0.0", "0.0.0"), ("0.0.5", "0.0.5"), ("0.3.0", "0.3.0"), ("12.34.56rc78", "12.34.56-rc.78")]
)
def test_installed_client_selects_native_version(monkeypatch: pytest.MonkeyPatch, version: str, expected: str) -> None:
    def installed_version(name: str) -> str:
        assert name == "a13n-envd-client"
        return version

    monkeypatch.setattr(managed_runtime.metadata, "version", installed_version)
    assert load_envd_version() == expected


@pytest.mark.parametrize(
    "version",
    [
        None,
        "",
        "../0.0.5",
        "v0.0.5",
        "01.0.5",
        "0.00.5",
        "0.0.05",
        "0.5",
        "0.0.5.0",
        "0.0.5rc0",
        "0.0.5rc01",
        "0.0.5-rc.1",
        "0.0.5RC1",
        "0.0.5c1",
        "0.0.5a1",
        "0.0.5b1",
        "0.0.5.dev1",
        "0.0.5.post1",
        "0.0.5+local",
        "0.0.5rc1.dev1",
        "0.0.5rc1.post1",
        "0.0.5rc1+local",
        "0!0.0.5",
        "1!0.0.5",
        " 0.0.5",
        "0.0.5\n",
    ],
)
async def test_invalid_client_metadata_fails_lazily_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str | None
) -> None:
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: version)
    runtime = _runtime(tmp_path, None)
    with pytest.raises(RuntimeResolutionError, match="a13n-envd-client version must use") as captured:
        await runtime.resolve()
    assert captured.value.code == "local_eip_version_invalid"
    assert "Reinstall a13n-harness-ui" in str(captured.value)
    assert "envd_runtime.executable" in str(captured.value)
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("version", ["", "../0.0.4", "v0.0.4", "01.0.4", "0.0.4rc1", "0.0.4-rc.0"])
def test_invalid_version_is_rejected(tmp_path: Path, version: str) -> None:
    with pytest.raises(RuntimeResolutionError) as captured:
        _runtime(tmp_path, version)
    assert captured.value.code == "local_eip_version_invalid"


@pytest.mark.parametrize("error", [managed_runtime.metadata.PackageNotFoundError, OSError, UnicodeError])
async def test_unavailable_client_metadata_fails_lazily_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def unavailable(name: str) -> str:
        raise error(name)

    monkeypatch.setattr(managed_runtime.metadata, "version", unavailable)
    runtime = _runtime(tmp_path, None)
    with pytest.raises(RuntimeResolutionError, match="metadata is missing or unreadable") as captured:
        await runtime.resolve()
    assert captured.value.code == "local_eip_version_invalid"
    assert "Reinstall a13n-harness-ui" in str(captured.value)
    assert "envd_runtime.executable" in str(captured.value)
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("version", [None, "0.0.0", "invalid"])
async def test_executable_override_does_not_need_client_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str | None
) -> None:
    metadata_reads = []

    def installed_version(name: str) -> str:
        metadata_reads.append(name)
        if version is None:
            raise managed_runtime.metadata.PackageNotFoundError(name)
        return version

    monkeypatch.setattr(managed_runtime.metadata, "version", installed_version)
    executable = tmp_path / ("a13n-envd.exe" if os.name == "nt" else "a13n-envd")
    executable.write_bytes(_executable("0.0.0"))
    executable.chmod(0o700)
    reconstructor = EnvironmentSnapshotReconstructor(
        envd_settings=EnvdRuntimeSettings(executable=executable),
        local_runtime_parent=tmp_path / "runtime",
    )
    assert await reconstructor.resolve_sandbox_executable() == executable
    assert metadata_reads == []
    assert not (tmp_path / "runtime").exists()


async def test_native_runtime_does_not_load_client_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_metadata(name: str) -> str:
        pytest.fail("Native must not read envd release metadata")

    monkeypatch.setattr(managed_runtime.metadata, "version", unexpected_metadata)
    # This collaborator is constructed for every App, including Native-only use.
    EnvironmentSnapshotReconstructor(local_runtime_parent=tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


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
@pytest.mark.parametrize("client_version,version", [("0.0.4", "0.0.4"), ("0.0.5rc1", "0.0.5-rc.1")])
async def test_archive_url_is_derived_from_version_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, client_version: str, version: str
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

    def matches_version(path: Path, expected: str, timeout: float) -> bool:
        assert expected == version
        return path.is_file()

    monkeypatch.setattr(managed_runtime, "_matches_version", matches_version)
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: client_version)
    path = await _runtime(tmp_path, None).resolve()
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


@pytest.mark.skipif(os.name == "nt", reason="fixture executable is a POSIX script")
async def test_ui_upgrade_acquires_new_client_release_and_preserves_previous_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda _: "9.8.6")
    content = _archive(tmp_path / "archive", _executable("9.8.6"))
    urls = []

    async def download(*, url, destination, **kwargs):
        urls.append(url)
        destination.write_bytes(content)

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    previous = await _runtime(tmp_path, None).resolve()
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda _: "9.8.7")
    runtime = _runtime(tmp_path, None)
    status = await runtime.status()
    assert status.required_version == "9.8.7"
    assert status.cached_versions == ("9.8.6",)
    assert not status.installed
    assert status.verified_version is None
    assert len(urls) == 1  # Status neither downloads nor reuses the previous release.
    with pytest.raises(RuntimeResolutionError, match="selected version"):
        await runtime.resolve()  # A stale release response cannot satisfy the new client.
    assert previous.read_bytes() == _executable("9.8.6")
    assert not (await runtime.status()).installed
    content = _archive(tmp_path / "archive-new", _executable("9.8.7"))
    selected = await runtime.resolve()
    assert selected != previous
    assert selected.read_bytes() == _executable("9.8.7")
    assert previous.read_bytes() == _executable("9.8.6")
    status = await runtime.status()
    assert status.cached_versions == ("9.8.6", "9.8.7")
    assert status.installed and status.verified_version == "9.8.7"
    assert "a13n-envd-v9.8.7/" in urls[-1]
    assert await _runtime(tmp_path, None).resolve() == selected
    assert len(urls) == 3


async def test_cached_release_status_is_target_specific_and_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_runtime, "current_envd_target", lambda: "aarch64-apple-darwin")
    for version, target, name in [
        ("9.8.6", "aarch64-apple-darwin", "a13n-envd"),
        ("9.8.5", "x86_64-unknown-linux-gnu", "a13n-envd"),
        ("9.8.4", "aarch64-apple-darwin", "partial.download"),
        ("invalid", "aarch64-apple-darwin", "a13n-envd"),
    ]:
        path = tmp_path / "cache" / version / target / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not an executable")
    status = await _runtime(tmp_path).status()
    assert status.cached_versions == ("9.8.6",)
    assert not status.installed
    assert status.verified_version is None


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


@pytest.mark.skipif(os.name == "nt", reason="fixture executable is a POSIX script")
async def test_explicit_reinstall_preserves_verified_cache_on_failure_and_replaces_on_success(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    original = _executable("9.8.7")
    archive = _archive(tmp_path / "archive", original)
    downloads = []

    async def download(*, destination, **kwargs):
        downloads.append(destination)
        destination.write_bytes(archive)

    monkeypatch.setattr(managed_runtime, "_download_archive", download)
    path = await runtime.resolve()
    archive = _archive(tmp_path / "wrong", _executable("9.8.6"))
    with pytest.raises(RuntimeResolutionError, match="selected version"):
        await runtime.resolve(reinstall=True)
    assert path.read_bytes() == original
    assert (await runtime.status()).phase == "idle"
    assert not list((tmp_path / "staging").iterdir())
    replacement = original + b"# replacement\n"
    archive = _archive(tmp_path / "replacement", replacement)
    assert await runtime.resolve(reinstall=True) == path
    assert path.read_bytes() == replacement
    assert len(downloads) == 3
    assert await runtime.resolve() == path
    assert len(downloads) == 3


@pytest.mark.skipif(os.name == "nt", reason="Unix executable fixtures")
@pytest.mark.parametrize("reinstall", [False, True])
async def test_native_updater_installs_exact_version_without_changing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reinstall: bool
) -> None:
    runtime = _runtime(tmp_path)
    target = current_envd_target()
    source = tmp_path / "cache" / "9.8.6" / target / "a13n-envd"
    source.parent.mkdir(parents=True)
    source.write_bytes(_executable("9.8.6"))
    source.chmod(0o700)
    destination = tmp_path / "cache" / "9.8.7" / target / "a13n-envd"
    calls = []

    async def supports(path):
        assert path == source
        return True

    async def native(path, **kwargs):
        calls.append((path, kwargs))
        assert (destination.parent / ".a13n-envd-version").read_text() == "9.8.7\n"
        destination.write_bytes(_executable("9.8.7"))
        destination.chmod(0o700)
        return "Installed.\n"

    async def unexpected(**kwargs):
        pytest.fail("Native updates must not use the bootstrap downloader")

    monkeypatch.setattr(managed_runtime, "supports_native_update", supports)
    monkeypatch.setattr(managed_runtime, "run_native_update", native)
    monkeypatch.setattr(managed_runtime, "_download_archive", unexpected)
    assert await runtime.resolve(reinstall=reinstall) == destination
    assert calls == [(source, {"version": "9.8.7", "directory": destination.parent, "force": reinstall})]
    assert source.read_bytes() == _executable("9.8.6")
    assert (await runtime.status()).verified_version == "9.8.7"


@pytest.mark.parametrize("failure", ["exit", "wrong-version", "cancel"])
async def test_native_failure_never_falls_back_and_clears_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    runtime = _runtime(tmp_path)
    source = tmp_path / "updater"
    calls = []

    async def find(target):
        return source

    async def native(path, **kwargs):
        calls.append(path)
        if failure == "exit":
            raise RuntimeResolutionError("checksum mismatch", code="envd_update_failed")
        if failure == "cancel":
            raise anyio.get_cancelled_exc_class()()
        return "Installed.\n"

    async def unexpected(**kwargs):
        pytest.fail("Failed native updates must never fall back")

    monkeypatch.setattr(runtime, "_find_updater", find)
    monkeypatch.setattr(managed_runtime, "run_native_update", native)
    monkeypatch.setattr(managed_runtime, "_download_archive", unexpected)
    expected = anyio.get_cancelled_exc_class() if failure == "cancel" else RuntimeResolutionError
    with pytest.raises(expected):
        await runtime.resolve()
    assert calls == [source]
    status = await runtime.status()
    assert status.phase == "idle"
    assert status.verified_version is None


@pytest.mark.parametrize("returncode", [0, 7, -15])
async def test_native_command_arguments_and_error_propagation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    import subprocess

    executable = tmp_path / "a13n-envd"
    calls = []

    async def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, returncode, b"native output\n", b"native diagnostic\n")

    monkeypatch.setattr(managed_runtime, "run_process", run)
    if returncode:
        with pytest.raises(RuntimeResolutionError, match="native diagnostic") as captured:
            await managed_runtime.run_native_update(executable, version="1.2.3", directory=tmp_path, force=True)
        assert captured.value.details["returncode"] == returncode
    else:
        assert "native output" in await managed_runtime.run_native_update(
            executable, version="1.2.3", directory=tmp_path, force=True
        )
    assert len(calls) == 1
    arguments, kwargs = calls[0]
    assert arguments[:-2] == [
        str(executable),
        "update",
        "--version",
        "1.2.3",
        "--install-dir",
        str(tmp_path),
        "--force",
    ]
    assert arguments[-2] == "--staging-dir"
    assert not Path(arguments[-1]).exists()
    assert kwargs == {"check": False}


def test_cache_pin_rejects_mismatched_version(tmp_path: Path) -> None:
    executable = tmp_path / "a13n-envd"
    managed_runtime._pin_installation(executable, "1.2.3")
    managed_runtime._pin_installation(executable, "1.2.3")
    with pytest.raises(RuntimeResolutionError, match="pin does not match"):
        managed_runtime._pin_installation(executable, "1.2.4")
    assert (tmp_path / ".a13n-envd-version").read_text() == "1.2.3\n"


@pytest.mark.skipif(os.name == "nt", reason="Unix executable fixture")
async def test_cancelled_native_process_is_joined_before_owned_scratch_cleanup(tmp_path: Path) -> None:
    import sys

    marker = tmp_path / "started"
    executable = tmp_path / "a13n-envd"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        "scratch = pathlib.Path(sys.argv[sys.argv.index('--staging-dir') + 1])\n"
        "(scratch / 'partial').write_bytes(b'partial archive')\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(scratch))\n"
        "time.sleep(60)\n"
    )
    executable.chmod(0o700)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(managed_runtime.run_native_update, executable)
        with anyio.fail_after(5):
            while not marker.exists():
                await anyio.sleep(0.01)
        scratch = Path(marker.read_text())
        assert (scratch / "partial").exists()
        tasks.cancel_scope.cancel()
    assert not scratch.exists()
    assert executable.exists()
