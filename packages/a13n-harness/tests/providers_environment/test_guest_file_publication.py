"""Run staged writes through GuestFiles and the shipped helper in real POSIX processes."""

import asyncio
import stat
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest
from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.files import (
    FileMkdirOperation,
    FileRemoveOperation,
    FileStatOperation,
    FileWriteTextOperation,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.native.configuration import CommandConfiguration

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="Guest filesystem helpers execute on POSIX sandboxes"),
]


@pytest.fixture
def guest_files(tmp_path):
    async def execute(argv, timeout):
        # Set a deterministic umask only in the child, preserving the test runner's process-wide mask.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import os,sys; os.umask(0o027); os.execv(sys.argv[1], sys.argv[1:])",
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        assert process.returncode == 0, stderr.decode()
        return stdout.decode()

    class LocalTransfer:
        async def read_stream(self, path):
            with Path(path).open("rb") as source:
                while chunk := source.read(65536):
                    yield chunk

        async def write_stream(self, path, source, *, mode):
            with Path(path).open("r+b") as destination:
                destination.truncate(0)
                while chunk := source.read(65536):
                    destination.write(chunk)

    configuration = CommandConfiguration(root=str(tmp_path), python=sys.executable)
    return GuestFiles(
        NativeCommands(execute, configuration, "generation-fixture", "mount-fixture", transfer=LocalTransfer())
    )


@pytest.mark.parametrize("operation", ["replace", "upsert", "append", "patch"])
@pytest.mark.parametrize("original_mode", [0o755, 0o600, 0o640])
async def test_guest_publication_preserves_existing_file_permissions(tmp_path, guest_files, operation, original_mode):
    target = tmp_path / "target"
    target.write_text("old\n")
    target.chmod(original_mode)
    if operation == "patch":
        await guest_files.patch_text("/target", "@@ -1 +1 @@\n-old\n+new\n")
    else:
        await guest_files.write_text("/target", "new\n", mode=operation)
    assert target.read_text() == ("old\nnew\n" if operation == "append" else "new\n")
    assert stat.S_IMODE(target.stat().st_mode) == original_mode
    assert not list(tmp_path.glob(".a13n-write-*"))


@pytest.mark.parametrize("mode", ["create", "upsert", "append"])
async def test_guest_new_file_permissions_retain_guest_umask(tmp_path, guest_files, mode):
    await guest_files.write_text("/new", "new\n", mode=mode)
    target = tmp_path / "new"
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".a13n-write-*"))


async def test_guest_replacement_preserves_symlink_and_target_permissions(tmp_path, guest_files):
    target = tmp_path / "target"
    target.write_text("old\n")
    target.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(target.name)
    await guest_files.write_text("/link", "new\n", mode="replace")
    assert link.is_symlink()
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


async def test_guest_replacement_drops_special_permission_bits(tmp_path, guest_files):
    target = tmp_path / "target"
    target.write_text("old\n")
    target.chmod(0o6755)
    await guest_files.write_text("/target", "new\n", mode="replace")
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


async def test_guest_replace_missing_file_does_not_publish_and_cleans_stage(tmp_path, guest_files):
    with pytest.raises(EnvironmentError) as caught:
        await guest_files.write_text("/missing", "new\n", mode="replace")
    assert caught.value.code == "environment_not_found"
    assert not list(tmp_path.iterdir())


async def test_guest_replaces_complete_nonempty_directory(tmp_path, guest_files):
    for name in ("source", "destination"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(name)
    (tmp_path / "destination" / "obsolete").write_text("old")
    await guest_files.move("/source", "/destination", replace=True)
    assert (tmp_path / "destination" / "SKILL.md").read_text() == "source"
    assert not (tmp_path / "destination" / "obsolete").exists()
    assert not (tmp_path / "source").exists()


async def test_guest_directory_no_replace_preserves_winner(tmp_path, guest_files):
    for name in ("source", "destination"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(name)
    with pytest.raises(EnvironmentError) as error:
        await guest_files.move("/source", "/destination")
    assert error.value.code == "environment_conflict"
    assert (tmp_path / "destination" / "SKILL.md").read_text() == "destination"


def archive(files, *, root=""):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        for name, content in files.items():
            package.writestr(root + name, content)
        package.writestr("__MACOSX/ignored", b"ignored")
    return FileArchive(buffer.getvalue(), tuple((name, len(data)) for name, data in files.items()), root)


async def test_guest_archive_uploads_once_and_extracts_only_manifest(tmp_path, guest_files, monkeypatch):
    content = {"SKILL.md": b"skill", "references/nested/binary": b"\x00\xff" * 500, "empty": b""}
    calls, uploads = [], []
    command, upload = guest_files.commands.files, guest_files.commands.write_stream

    async def record(action, arguments, **kwargs):
        calls.append(action)
        return await command(action, arguments, **kwargs)

    async def transfer(path, source):
        uploads.append(path)
        await upload(path, source)

    monkeypatch.setattr(guest_files.commands, "files", record)
    monkeypatch.setattr(guest_files.commands, "write_stream", transfer)
    result = await guest_files.write_archive("/staging/skill", archive(content, root="uploaded/"))
    assert result.path == "/staging/skill"
    assert calls == ["stage", "archive"]
    assert len(uploads) == 1
    assert {
        str(p.relative_to(tmp_path / "staging/skill")): p.read_bytes()
        for p in (tmp_path / "staging/skill").rglob("*")
        if p.is_file()
    } == content
    assert not list(tmp_path.rglob(".a13n-archive-*"))
    assert stat.S_IMODE((tmp_path / "staging/skill/SKILL.md").stat().st_mode) == 0o640


@pytest.mark.parametrize("existing", ["directory", "file", "symlink"])
async def test_guest_archive_never_overwrites_existing_destination(tmp_path, guest_files, existing):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_bytes(b"keep")
    destination = tmp_path / "candidate"
    if existing == "directory":
        destination.mkdir()
    elif existing == "file":
        destination.write_bytes(b"keep")
    else:
        destination.symlink_to(target, target_is_directory=True)
    with pytest.raises(EnvironmentError) as error:
        await guest_files.write_archive("/candidate", archive({"SKILL.md": b"new"}))
    assert error.value.code == "environment_conflict"
    assert (target / "keep").read_bytes() == b"keep"
    assert not (target / "SKILL.md").exists()
    assert not list(tmp_path.glob(".a13n-archive-*"))


@pytest.mark.parametrize("invalid", ["zip", "missing", "size", "symlink", "duplicate"])
async def test_guest_rejects_invalid_archive_before_creating_destination(tmp_path, guest_files, invalid):
    value = archive({"SKILL.md": b"ok"})
    if invalid == "zip":
        value = FileArchive(b"invalid ZIP", value.entries)
    elif invalid == "missing":
        value = FileArchive(value.content, (("missing", 2),))
    elif invalid == "size":
        value = FileArchive(value.content, (("SKILL.md", 1),))
    elif invalid == "duplicate":
        value = FileArchive(value.content, (("SKILL.md", 2), ("skill.md", 2)))
    else:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as package:
            member = ZipInfo("SKILL.md")
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(member, b"ok")
        value = FileArchive(buffer.getvalue(), value.entries)
    with pytest.raises(EnvironmentError) as error:
        await guest_files.write_archive("/candidate", value)
    assert error.value.code == "environment_request_invalid"
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".a13n-archive-*"))


async def test_guest_detects_corrupt_upload_and_retry_succeeds(tmp_path, guest_files, monkeypatch):
    upload = guest_files.commands.write_stream

    async def corrupt(path, source):
        await upload(path, BytesIO(b"corrupt transfer"))

    monkeypatch.setattr(guest_files.commands, "write_stream", corrupt)
    with pytest.raises(EnvironmentError) as error:
        await guest_files.write_archive("/candidate", archive({"SKILL.md": b"ok"}))
    assert error.value.code == "environment_request_invalid"
    assert not (tmp_path / "candidate").exists()
    monkeypatch.setattr(guest_files.commands, "write_stream", upload)
    await guest_files.write_archive("/candidate", archive({"SKILL.md": b"ok"}))
    assert (tmp_path / "candidate/SKILL.md").read_bytes() == b"ok"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../escape", "a\\escape", "C:/escape", "a//b"])
def test_archive_manifest_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        FileArchive(b"", ((path, 0),))


@pytest.mark.parametrize("value", [FileArchive(b"x" * 129, ()), FileArchive(b"x", (("large", 129),))])
async def test_guest_archive_enforces_transfer_and_member_limits_before_dispatch(tmp_path, guest_files, value):
    guest_files.commands.configuration = guest_files.commands.configuration.model_copy(update={"max_file_bytes": 128})
    with pytest.raises(EnvironmentError) as error:
        await guest_files.write_archive("/candidate", value)
    assert error.value.code == "environment_too_large"
    assert not list(tmp_path.iterdir())


async def test_guest_batch_runs_ordered_operations_with_one_command(tmp_path, guest_files, monkeypatch):
    calls = []
    original = guest_files.commands.files

    async def record(action, arguments, **kwargs):
        calls.append(action)
        return await original(action, arguments, **kwargs)

    monkeypatch.setattr(guest_files.commands, "files", record)
    results = await guest_files.batch(
        [
            FileMkdirOperation(path="/cache"),
            FileWriteTextOperation(path="/cache/pin", text="first", mode="create"),
            FileWriteTextOperation(path="/cache/pin", text="first second", mode="upsert"),
            FileStatOperation(path="/cache/pin"),
            FileStatOperation(path="/cache/absent", missing_ok=True),
            FileRemoveOperation(path="/cache/absent", missing_ok=True),
        ]
    )
    assert calls == ["batch"]
    assert results[1].bytes_written == 5
    assert results[2].bytes_written == 12
    assert results[3].size == 12
    assert results[4:] == (None, None)
    assert (tmp_path / "cache/pin").read_text() == "first second"
    assert stat.S_IMODE((tmp_path / "cache/pin").stat().st_mode) == 0o640


async def test_guest_batch_stops_on_failure_without_replaying_prefix(tmp_path, guest_files):
    with pytest.raises(EnvironmentError) as error:
        await guest_files.batch(
            [
                FileWriteTextOperation(path="/pin", text="once", mode="create"),
                FileWriteTextOperation(path="/pin", text="conflict", mode="create"),
                FileWriteTextOperation(path="/later", text="never", mode="create"),
            ]
        )
    assert error.value.code == "environment_conflict"
    assert (tmp_path / "pin").read_text() == "once"
    assert not (tmp_path / "later").exists()
    assert not list(tmp_path.glob(".a13n-write-*"))


async def test_guest_batch_rejects_oversize_before_dispatch(guest_files, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Oversized batch must not dispatch")

    monkeypatch.setattr(guest_files.commands, "files", forbidden)
    with pytest.raises(EnvironmentError) as error:
        await guest_files.batch([FileWriteTextOperation(path="/large", text="x" * 65536, mode="create")])
    assert error.value.code == "environment_too_large"


async def test_guest_batch_rejects_nul_text_without_writing(tmp_path, guest_files):
    with pytest.raises(EnvironmentError) as error:
        await guest_files.batch([FileWriteTextOperation(path="/target", text="new\x00", mode="create")])
    assert error.value.code == "environment_unsupported"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("damage", ["truncate", "corrupt"])
async def test_guest_binary_publication_verifies_size_and_checksum_before_replace(
    tmp_path, guest_files, monkeypatch, damage
):
    target = tmp_path / "target"
    target.write_bytes(b"winner")
    upload = guest_files.commands.write_stream

    async def damaged(path, source):
        await upload(path, BytesIO(b"new" if damage == "truncate" else b"broken"))

    monkeypatch.setattr(guest_files.commands, "write_stream", damaged)
    with pytest.raises(EnvironmentError) as caught:
        await guest_files.write_text("/target", "change", mode="replace")
    assert caught.value.code == "environment_request_invalid"
    assert target.read_bytes() == b"winner"
    assert not list(tmp_path.glob(".a13n-*"))


@pytest.mark.parametrize("failure", ["cancel", "transport"])
async def test_guest_archive_failed_upload_cleans_candidate(tmp_path, guest_files, monkeypatch, failure):
    async def fail(path, source):
        assert Path(path).exists()
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise EnvironmentError("Uncertain transfer", code="environment_unknown_outcome")

    monkeypatch.setattr(guest_files.commands, "write_stream", fail)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else EnvironmentError):
        await guest_files.write_archive("/candidate", archive({"SKILL.md": b"ok"}))
    assert not list(tmp_path.glob(".a13n-*"))
    assert not (tmp_path / "candidate").exists()


async def test_guest_append_does_not_publish_after_existing_prefix_read_fails(tmp_path, guest_files, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(b"old-prefix-and-suffix")
    failure = EnvironmentError("Existing file disappeared during read", code="environment_not_found")

    async def interrupted(path, **kwargs):
        yield b"old-prefix"
        raise failure

    async def source():
        yield b"new"

    monkeypatch.setattr(guest_files, "read_bytes_stream", interrupted)
    with pytest.raises(EnvironmentError) as caught:
        await guest_files.write_bytes_stream("/target", source(), mode="append")
    assert caught.value is failure
    assert target.read_bytes() == b"old-prefix-and-suffix"
    assert not list(tmp_path.glob(".a13n-*"))
