"""Native E2B filesystem boundaries, OS errors and failed-publication effects."""

import json
import logging
import shlex
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from a13n_environment import EnvironmentError, EnvironmentProviderError

from .file_contract import (
    BASE,
    MISSING,
    READS,
    WRONG_TYPES,
    assert_traversal_rejected,
    read_stream,
)

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


class FileSandbox:
    def __init__(self, pool, environment):
        self.pool, self.environment = pool, environment
        self.files = environment.operations.files
        self.native = environment._commands.sandbox
        self.root = environment._configuration.root
        self.outside = "/tmp/" + environment.environment_id

    async def python(self, script, *, root=False):
        prefix = (
            "import errno, hashlib, json, os, stat, subprocess\nfrom pathlib import Path\n"
            f"root = Path({self.root!r})\nbase = root / 'file-tests'\noutside = Path({self.outside!r})\n"
        )
        result = await self.native.commands.run(
            shlex.join(["python3", "-I", "-c", prefix + script]),
            user="root" if root else self.environment._configuration.user,
            timeout=30,
            request_timeout=30,
        )
        assert result.exit_code == 0
        return json.loads(result.stdout) if result.stdout.strip() else None

    async def snapshot(self):
        # Independent native evidence includes hidden staging files and never follows symlinks.
        return await self.python(
            """
result = {}
for parent in (base, outside):
    for path in [parent, *sorted(parent.rglob('*'))]:
        mode = path.lstat().st_mode
        value = [stat.S_IFMT(mode), stat.S_IMODE(mode)]
        if stat.S_ISLNK(mode):
            value.append(os.readlink(path))
        elif stat.S_ISREG(mode):
            with path.open('rb') as source:
                value.append(hashlib.file_digest(source, 'sha256').hexdigest())
        result[str(path)] = value
print(json.dumps(result))
""",
            root=True,
        )


@pytest.fixture
async def file_sandbox(e2b_sandboxes):
    sandbox = FileSandbox(e2b_sandboxes, await e2b_sandboxes.prepare())
    await sandbox.python(
        """
base.mkdir()
(base / 'source').write_text('ORIGINAL' + chr(10))
(base / 'directory').mkdir()
outside.mkdir()
(outside / 'sentinel').write_text('OUTSIDE_UNCHANGED')
"""
    )
    return sandbox


@contextmanager
def failure(code):
    with pytest.raises((EnvironmentError, EnvironmentProviderError)) as caught:
        yield caught
    assert caught.value.code == code
    assert "OUTSIDE_UNCHANGED" not in str(caught.value)
    logger.info("E2B file failure code=%s", caught.value.code)


async def test_e2b_file_traversal_rejects_source_and_destination_paths(file_sandbox):
    sandbox, files = file_sandbox, file_sandbox.files
    before = await sandbox.snapshot()
    for path in ("../sentinel", "/../sentinel", BASE + "/../../sentinel", BASE + "/nul\x00name"):
        await assert_traversal_rejected(files, path, failure)
        assert await sandbox.snapshot() == before


async def test_e2b_file_symlink_escape_and_link_entry_operations(file_sandbox):
    sandbox, files = file_sandbox, file_sandbox.files
    await sandbox.python(
        "(base / 'escape').symlink_to(outside, target_is_directory=True)\n"
        "(base / 'link').symlink_to(outside / 'sentinel')\n"
    )
    before = await sandbox.snapshot()
    for path in (BASE + "/escape/sentinel", BASE + "/link"):
        for _, operation in READS:
            with failure("environment_denied"):
                await operation(files, path)
        for operation in (
            lambda path=path: files.write_text(path, "CHANGED", mode="upsert"),
            lambda path=path: files.copy(BASE + "/source", path),
            lambda path=path: files.move(BASE + "/source", path, replace=True),
        ):
            with failure("environment_denied"):
                await operation()
        assert await sandbox.snapshot() == before
    assert (await files.stat(BASE + "/link")).kind == "symlink"
    # Moving/removing the link itself is legal; it must not move/remove its outside target.
    await files.move(BASE + "/link", BASE + "/moved-link")
    await files.remove(BASE + "/moved-link")
    await files.remove(BASE + "/escape")
    assert await sandbox.python("print(json.dumps((outside / 'sentinel').read_text()))") == "OUTSIDE_UNCHANGED"


@pytest.mark.parametrize("name,operation", MISSING, ids=[name for name, _ in MISSING])
async def test_e2b_missing_files_return_not_found_without_mutations(file_sandbox, name, operation):
    before = await file_sandbox.snapshot()
    with failure("environment_not_found"):
        await operation(file_sandbox.files, BASE + "/missing")
    assert await file_sandbox.snapshot() == before


async def test_e2b_append_creates_missing_file(file_sandbox):
    files = file_sandbox.files
    await files.write_text(BASE + "/new", "first", mode="append")
    await files.write_text(BASE + "/new", "second", mode="append")
    assert (await files.read_text(BASE + "/new")).text == "firstsecond"


@pytest.mark.parametrize("name,operation", WRONG_TYPES, ids=[name for name, _ in WRONG_TYPES])
async def test_e2b_file_type_errors_preserve_entries(file_sandbox, name, operation):
    before = await file_sandbox.snapshot()
    with failure("environment_request_invalid"):
        await operation(file_sandbox.files)
    assert await file_sandbox.snapshot() == before


async def test_e2b_os_permissions_deny_guest_file_operations(file_sandbox):
    sandbox, files = file_sandbox, file_sandbox.files
    await sandbox.python(
        """
(base / 'locked').mkdir()
(base / 'locked' / 'secret').write_text('PRIVATE')
(base / 'locked').chmod(0o700)
(base / 'unreadable').write_text('PRIVATE')
(base / 'unreadable').chmod(0o600)
""",
        root=True,
    )
    uid = await sandbox.python("print(json.dumps(os.geteuid()))")
    assert uid != 0, "Permission checks require a non-root configured E2B user"
    before = await sandbox.snapshot()
    for operation in (
        lambda: files.read_bytes(BASE + "/unreadable"),
        lambda: files.read_bytes(BASE + "/locked/secret"),
        lambda: files.list(BASE + "/locked", max_results=10),
        lambda: files.mkdir(BASE + "/locked/new"),
        lambda: files.remove(BASE + "/locked/secret"),
        lambda: files.move(BASE + "/source", BASE + "/locked/moved"),
    ):
        with failure("environment_denied"):
            await operation()
        assert await sandbox.snapshot() == before


@pytest.mark.parametrize("transfer", ["download", "upload"])
async def test_e2b_guest_permissions_gate_sdk_file_transfers(file_sandbox, monkeypatch, transfer):
    sandbox, files = file_sandbox, file_sandbox.files
    await sandbox.python(
        """
(base / 'unreadable').write_text('PRIVATE')
(base / 'unreadable').chmod(0o600)
(base / 'locked').mkdir(mode=0o555)
""",
        root=True,
    )
    denied = await sandbox.python(
        """
assert os.geteuid() != 0
result = []
for path, mode in [(base / 'unreadable', 'rb'), (base / 'locked' / 'new', 'wb')]:
    try:
        with path.open(mode):
            pass
    except PermissionError as error:
        result.append(error.errno)
print(json.dumps(result))
"""
    )
    assert denied == [13, 13]
    before = await sandbox.snapshot()
    read = AsyncMock(wraps=sandbox.native.files.read)
    write = AsyncMock(wraps=sandbox.native.files.write)
    monkeypatch.setattr(sandbox.native.files, "read", read)
    monkeypatch.setattr(sandbox.native.files, "write", write)
    with failure("environment_denied") as error:
        if transfer == "download":
            await read_stream(files, BASE + "/unreadable")
        else:
            await files.write_text(BASE + "/locked/new", "CHANGED", mode="create")
    read.assert_not_awaited()
    write.assert_not_awaited()
    assert "PRIVATE" not in str(error.value) and sandbox.root not in str(error.value)

    assert await sandbox.snapshot() == before


async def test_e2b_full_filesystem_preserves_destination_and_removes_partial_upload(file_sandbox):
    sandbox, files = file_sandbox, file_sandbox.files
    await sandbox.python(
        """
volume = base / 'limited'
volume.mkdir()
subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=1m,mode=0777', 'tmpfs', str(volume)], check=True)
assert os.path.ismount(volume)
assert os.statvfs(volume).f_blocks * os.statvfs(volume).f_frsize <= 1024 * 1024
(volume / 'destination').write_text('ORIGINAL')
# Leave 64 KiB free, less than the requested 512 KiB upload.
free = os.statvfs(volume).f_bavail * os.statvfs(volume).f_frsize
(volume / 'filler').write_bytes(b'x' * (free - 65536))
print(json.dumps({'capacity': os.statvfs(volume).f_blocks * os.statvfs(volume).f_frsize}))
""",
        root=True,
    )
    try:
        before = await sandbox.snapshot()

        async def payload():
            for _ in range(8):
                yield b"y" * 65536

        for mode in ("create", "replace", "append"):
            path = BASE + ("/limited/new" if mode == "create" else "/limited/destination")
            with failure("provider_unknown_outcome") as error:
                await files.write_bytes_stream(path, payload(), mode=mode)
            assert type(error.value.__context__).__name__ == "NotEnoughSpaceException"
            assert await sandbox.snapshot() == before
        # Independently verify the kernel's failure, rather than accepting an arbitrary SDK exception.
        errno_value = await sandbox.python(
            """
try:
    with (base / 'limited' / 'probe').open('wb') as output:
        output.write(b'x' * (2 * 1024 * 1024))
except OSError as error:
    print(json.dumps(error.errno))
finally:
    (base / 'limited' / 'probe').unlink(missing_ok=True)
"""
        )
        assert errno_value == 28
        assert await sandbox.snapshot() == before
        await sandbox.python("(base / 'limited' / 'filler').unlink()", root=True)
        await files.write_text(BASE + "/limited/destination", "RECOVERED", mode="replace")
        assert (await files.read_text(BASE + "/limited/destination")).text == "RECOVERED"
        logger.info("E2B ENOSPC verified on isolated 1 MiB tmpfs; failed upload left destination unchanged")
    finally:
        await sandbox.python("subprocess.run(['umount', str(base / 'limited')], check=True)", root=True)
