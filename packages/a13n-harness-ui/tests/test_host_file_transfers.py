from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_file_transfers import (
    TRANSFER_TTL_SECONDS,
    FileTransferRequest,
    FileTransfers,
    byte_range,
    stream_response,
)
from a13n_harness_ui.host_files import FILE_CHUNK_BYTES, FileReadRequest, HostFiles
from starlette.requests import HTTPConnection
from starlette.types import Message

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("items=0-1", None),
        ("bytes=0-1,4-5", None),
        ("bytes=0-3", (0, 3)),
        ("bytes=8-99", (8, 9)),
        ("bytes=3-", (3, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=-99", (0, 9)),
    ],
)
def test_byte_ranges(header: str | None, expected: tuple[int, int] | None) -> None:
    assert byte_range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=-", "bytes=-0", "bytes=10-", "bytes=3-2", "bytes=bad"])
def test_unsatisfiable_ranges(header: str) -> None:
    with pytest.raises(ValueError):
        byte_range(header, 10)
    with pytest.raises(ValueError):
        byte_range("bytes=0-", 0)


def test_transfer_capabilities_are_signed_scoped_expiring_and_listener_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a13n_harness_ui.host_file_transfers.time.time", lambda: 1000)
    transfers = FileTransfers()
    selected = FileTransferRequest(path="/tmp/clip.mp4", expected_revision="reviewed", disposition="inline")
    access = transfers.issue(selected, Path(selected.path))
    token = parse_qs(urlsplit(access.url).query)["token"][0]
    claims = transfers.verify(token)
    assert claims.path == selected.path and claims.expected_revision == "reviewed"
    assert claims.disposition == "inline" and claims.media_type == "video/mp4"
    assert access.expires_at == 1000 + TRANSFER_TTL_SECONDS
    for invalid in ("", token + "x", "x" + token, token.replace(".", ".x")):
        with pytest.raises(HarnessUiError, match="expired or is invalid"):
            transfers.verify(invalid)
    with pytest.raises(HarnessUiError):
        FileTransfers().verify(token)
    monkeypatch.setattr("a13n_harness_ui.host_file_transfers.time.time", lambda: access.expires_at)
    with pytest.raises(HarnessUiError):
        transfers.verify(token)


def test_unknown_and_active_types_remain_downloadable_even_when_inline_is_requested() -> None:
    transfers = FileTransfers()
    for name in ("page.html", "graphic.svg", "document.pdf", "file.unknown-format", "image.png.gz"):
        for disposition in ("inline", "attachment"):
            access = transfers.issue(
                FileTransferRequest(path=f"/tmp/{name}", expected_revision="one", disposition=disposition), Path(name)
            )
            token = parse_qs(urlsplit(access.url).query)["token"][0]
            claims = transfers.verify(token)
            assert claims.disposition == "attachment"
            assert claims.media_type == "application/octet-stream"


def test_inline_access_uses_resolved_standard_library_mime_types() -> None:
    transfers = FileTransfers()
    selected = FileTransferRequest(path="/tmp/latest", expected_revision="one", disposition="inline")
    access = transfers.issue(selected, Path("/tmp/clip.MP4"))
    token = parse_qs(urlsplit(access.url).query)["token"][0]
    assert transfers.verify(token).media_type == "video/mp4"
    for extension, media_type in {
        "png": "image/png",
        "JPG": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "avif": "image/avif",
        "bmp": "image/bmp",
        "oga": "audio/ogg",
    }.items():
        selected = FileTransferRequest(path=f"/tmp/image.{extension}", expected_revision="one", disposition="inline")
        access = transfers.issue(selected, Path(selected.path))
        token = parse_qs(urlsplit(access.url).query)["token"][0]
        assert transfers.verify(token).media_type == media_type


async def test_file_info_observes_the_resolved_revision_without_reading_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness_ui.host_files as module

    target = tmp_path / "image.avif"
    target.write_bytes(b"not decoded by the server")
    link = tmp_path / "latest"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Native symlinks are unavailable")
    opened = []
    original = module._open_stream

    def track(request):
        handle = original(request)
        opened.append(handle)
        return handle

    def no_read(*args):
        raise AssertionError("File info must not read content")

    monkeypatch.setattr(module, "_open_stream", track)
    monkeypatch.setattr(module.FileStream, "read", no_read)
    monkeypatch.setattr(module, "_snapshot", no_read)
    files = HostFiles(enabled=True)
    info = await files.info(FileReadRequest(path=str(link)))
    assert info.resolved_path == str(target)
    assert info.entry.path == str(target)
    assert info.media_type == "image/avif"
    assert all(handle.stream.closed for handle in opened)
    with pytest.raises(HarnessUiError, match="changed"):
        await files.info(FileReadRequest(path=str(link), expected_revision="stale"))
    with pytest.raises(HarnessUiError):
        await HostFiles(enabled=False).info(FileReadRequest(path=str(link)))


def test_transfer_mime_resolution_is_not_an_extension_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_harness_ui.host_files.mimetypes.guess_file_type", lambda *args, **kwargs: ("video/new-format", None)
    )
    access = FileTransfers()
    selected = access.issue(
        FileTransferRequest(path="/tmp/unknown", expected_revision="one", disposition="inline"), Path("/tmp/unknown")
    )
    token = parse_qs(urlsplit(selected.url).query)["token"][0]
    assert access.verify(token).media_type == "video/new-format"
    assert access.verify(token).disposition == "inline"


def connection(method: str = "GET", **headers: str) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "method": method,
            "path": "/api/host/files/transfer",
            "asgi": {"spec_version": "2.4"},
            "headers": [(key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()],
        }
    )


async def receive() -> Message:
    return {"type": "http.disconnect"}


async def test_large_transfers_remain_chunk_bounded_and_close_on_success(tmp_path: Path) -> None:
    path = tmp_path / "large.mp4"
    size = 15_047_567
    with path.open("wb") as stream:
        stream.truncate(size)
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection()
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    total = 0

    async def send(message: Message) -> None:
        nonlocal total
        if message["type"] == "http.response.body":
            assert len(message["body"]) <= FILE_CHUNK_BYTES
            total += len(message["body"])

    await response(request.scope, receive, send)
    assert total == size
    assert opened.stream.closed
    assert response.headers["content-length"] == str(size)
    assert response.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("method", "header", "status", "content"),
    [
        ("GET", "bytes=2-4", 206, b"234"),
        ("GET", "bytes=-2", 206, b"89"),
        ("GET", "bytes=20-", 416, b""),
        ("HEAD", "bytes=2-4", 200, b""),
    ],
)
async def test_range_delivery_and_head_close_handles(
    tmp_path: Path, method: str, header: str, status: int, content: bytes
) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0123456789")
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection(method, range=header)
    response = await stream_response(
        request, opened, files.read_stream, filename=path.name, media_type="video/mp4", inline=True
    )
    chunks: list[bytes] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            chunks.append(message["body"])

    await response(request.scope, receive, send)
    assert response.status_code == status
    assert b"".join(chunks) == content
    assert opened.stream.closed
    if status == 416:
        assert response.headers["content-range"] == "bytes */10"


async def test_stream_stops_on_a_changed_revision_and_closes_on_disconnect(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"a" * (FILE_CHUNK_BYTES + 1))
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection()
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    sent: list[bytes] = []

    async def changed(message: Message) -> None:
        if message["type"] == "http.response.body" and message["body"]:
            sent.append(message["body"])
            path.write_bytes(b"b" * (FILE_CHUNK_BYTES + 1))

    with pytest.raises(HarnessUiError, match="File content changed"):
        await response(request.scope, receive, changed)
    assert sent == [b"a" * FILE_CHUNK_BYTES]
    assert opened.stream.closed
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    response = await stream_response(request, opened, files.read_stream, filename=path.name)

    async def disconnected(_message: Message) -> None:
        raise RuntimeError("Disconnected before the body iterator started")

    with pytest.raises(RuntimeError, match="Disconnected"):
        await response(request.scope, receive, disconnected)
    assert opened.stream.closed


async def test_stream_rejects_stale_open_and_first_read(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"old")
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    revision = opened.entry.revision
    path.write_bytes(b"new")
    with pytest.raises(HarnessUiError, match="File content changed"):
        await stream_response(connection(), opened, files.read_stream, filename=path.name)
    assert opened.stream.closed
    with pytest.raises(HarnessUiError, match="File content changed"):
        await files.open_stream(FileReadRequest(path=str(path), expected_revision=revision))
    with pytest.raises(HarnessUiError, match="share-computer"):
        await HostFiles().open_stream(FileReadRequest(path=str(path)))


@pytest.mark.parametrize("name", ["archive.zip", "data.bin", "large.txt", "clip.mp4"])
async def test_download_ranges_have_no_whole_file_limit(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    size = 5 * 1024**3 + 7
    with path.open("wb") as stream:
        stream.truncate(size)
        stream.seek(size - 4)
        stream.write(b"TAIL")
    files = HostFiles(enabled=True)
    reviewed = await files.metadata(str(path))
    selected = FileTransferRequest(path=str(path), expected_revision=reviewed.revision, disposition="attachment")
    transfers = FileTransfers()
    access = transfers.issue(selected, path)
    claims = transfers.verify(parse_qs(urlsplit(access.url).query)["token"][0])
    opened = await files.open_stream(FileReadRequest(path=claims.path, expected_revision=claims.expected_revision))
    request = connection(range="bytes=-4")
    response = await stream_response(request, opened, files.read_stream, filename=name, media_type=claims.media_type)
    content = bytearray()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            content.extend(message["body"])

    await response(request.scope, receive, send)
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {size - 4}-{size - 1}/{size}"
    assert response.headers["content-type"] == "application/octet-stream"
    assert content == b"TAIL"
    assert opened.stream.closed


@pytest.mark.parametrize("path", [Path("/proc/version"), Path("/sys/devices/system/cpu/online")])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_native_regular_files_with_unreliable_stat_sizes(path: Path, method: str) -> None:
    if not path.is_file():
        pytest.skip("Native pseudo-files are unavailable on this platform")
    expected = path.read_bytes()
    assert path.stat().st_size != len(expected)
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection(method, range="bytes=0-3")
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    content = bytearray()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            assert len(message["body"]) <= FILE_CHUNK_BYTES
            content.extend(message["body"])

    await response(request.scope, receive, send)
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "none"
    assert "content-length" not in response.headers
    assert "content-range" not in response.headers
    assert content == (expected if method == "GET" else b"")
    assert opened.stream.closed


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_empty_disk_files_are_complete_zero_byte_transfers(tmp_path: Path, method: str) -> None:
    path = tmp_path / "empty"
    path.touch()
    files = HostFiles(enabled=True)
    opened = await files.open_stream(FileReadRequest(path=str(path)))
    request = connection(method)
    response = await stream_response(request, opened, files.read_stream, filename=path.name)
    content = bytearray()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            content.extend(message["body"])

    await response(request.scope, receive, send)
    assert response.status_code == 200
    assert response.headers["content-length"] == "0"
    assert response.headers["accept-ranges"] == "bytes"
    assert not content and opened.stream.closed
