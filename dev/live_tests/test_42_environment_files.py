"""File boundaries across native Local and all four real Envd-backed providers."""

import logging
import os
from contextlib import contextmanager

import pytest
from a13n_environment import EnvironmentError

from .file_backends import KINDS, FileBackend

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)
BASE = "/file-tests"


@pytest.fixture(params=KINDS)
def file_backend_kind(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real native file boundary tests")
    return request.param


@pytest.fixture
def read_only():
    return False


@pytest.fixture
async def file_backend(file_backend_kind, tmp_path, read_only):
    async with FileBackend(file_backend_kind, tmp_path, read_only=read_only).open() as backend:
        yield backend


@contextmanager
def failure(code):
    with pytest.raises(EnvironmentError) as error:
        yield error
    assert error.value.code == code
    logger.info("File conformance failure code=%s", code)


async def read_stream(files, path):
    return b"".join([chunk async for chunk in files.read_bytes_stream(path)])


READS = [
    ("read", lambda files, path: files.read_bytes(path)),
    ("stream", read_stream),
    ("text", lambda files, path: files.read_text(path)),
]
MISSING = [
    *READS,
    ("stat", lambda files, path: files.stat(path)),
    ("list", lambda files, path: files.list(path, max_results=10)),
    ("remove", lambda files, path: files.remove(path)),
    ("move", lambda files, path: files.move(path, BASE + "/moved")),
    ("copy", lambda files, path: files.copy(path, BASE + "/copied")),
    ("replace", lambda files, path: files.write_text(path, "CHANGED", mode="replace")),
    ("patch", lambda files, path: files.patch_text(path, "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n")),
]


async def test_file_traversal_rejects_source_and_destination_before_mutation(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    before = backend.snapshot()
    for path in ("../sentinel", "/../sentinel", BASE + "/../../sentinel", BASE + "/nul\x00name"):
        for _, operation in READS:
            with failure("environment_request_invalid"):
                await operation(files, path)
        for operation in (
            lambda path=path: files.stat(path),
            lambda path=path: files.write_text(path, "CHANGED", mode="upsert"),
            lambda path=path: files.copy(BASE + "/source", path),
            lambda path=path: files.move(BASE + "/source", path),
        ):
            with failure("environment_request_invalid"):
                await operation()
        assert backend.snapshot() == before
        assert await backend.outside_content() == "OUTSIDE_UNCHANGED"


async def test_file_symlink_escape_and_provider_link_replacement_semantics(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    base = backend.root / "file-tests"
    (base / "escape").symlink_to(backend.outside, target_is_directory=True)
    (base / "link").symlink_to(backend.outside + "/sentinel")
    before = backend.snapshot()
    for path in (BASE + "/escape/sentinel", BASE + "/link"):
        for _, operation in READS:
            with failure("environment_denied"):
                await operation(files, path)
        for operation in (
            lambda path=path: files.write_text(path, "CHANGED", mode="upsert"),
            lambda path=path: files.copy(BASE + "/source", path, replace=True),
        ):
            with failure("environment_denied"):
                await operation()
        assert backend.snapshot() == before
    with failure("environment_denied"):
        await files.move(BASE + "/source", BASE + "/escape/sentinel", replace=True)
    assert backend.snapshot() == before
    assert (await files.stat(BASE + "/link")).kind == "symlink"
    if backend.kind == "direct-local":
        with failure("environment_denied"):
            await files.move(BASE + "/source", BASE + "/link", replace=True)
        assert backend.snapshot() == before
    else:
        # EIP permits atomic replacement of the link entry, without following its target.
        await files.move(BASE + "/source", BASE + "/link", replace=True)
        assert not (base / "link").is_symlink()
        assert (base / "link").read_text() == "ORIGINAL\n"
        assert not (base / "source").exists()
    await files.move(BASE + "/escape", BASE + "/moved-link")
    await files.remove(BASE + "/moved-link")
    assert await backend.outside_content() == "OUTSIDE_UNCHANGED"


@pytest.mark.parametrize("read_only", [True])
async def test_file_read_only_rejects_all_mutations_before_consuming_upload(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    code = "environment_denied" if backend.kind == "direct-local" else "environment_unsupported"
    assert (await files.read_text(BASE + "/source")).text == "ORIGINAL\n"
    before = backend.snapshot()
    for mode in ("create", "replace", "upsert", "append"):
        with failure(code):
            await files.write_text(BASE + "/source", "CHANGED", mode=mode)
    for operation in (
        lambda: files.mkdir(BASE + "/new"),
        lambda: files.remove(BASE + "/source"),
        lambda: files.remove(BASE + "/directory", recursive=True),
        lambda: files.move(BASE + "/source", BASE + "/moved"),
        lambda: files.copy(BASE + "/source", BASE + "/copied"),
        lambda: files.patch_text(BASE + "/source", "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n"),
    ):
        with failure(code):
            await operation()

    async def forbidden_stream():
        raise AssertionError("Read-only upload consumed input")
        yield b"never"

    with failure(code):
        await files.write_bytes_stream(BASE + "/new", forbidden_stream(), mode="create")
    assert backend.snapshot() == before


@pytest.mark.parametrize("name,operation", MISSING, ids=[name for name, _ in MISSING])
async def test_missing_file_errors_preserve_directory_entries(file_backend, name, operation):
    before = file_backend.snapshot()
    with failure("environment_not_found"):
        await operation(file_backend.environment.operations.files, BASE + "/missing")
    assert file_backend.snapshot() == before


async def test_append_requires_existing_file_for_local_and_envd(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    before = backend.snapshot()
    # These providers require an existing file; E2B's create-on-append has its own test.
    with failure("environment_not_found"):
        await files.write_text(BASE + "/new", "suffix", mode="append")
    assert backend.snapshot() == before
    await files.write_text(BASE + "/source", "suffix", mode="append")
    assert (await files.read_text(BASE + "/source")).text == "ORIGINAL\nsuffix"


WRONG_TYPES = [
    *[(name, lambda files, operation=operation: operation(files, BASE + "/directory")) for name, operation in READS],
    ("list-file", lambda files: files.list(BASE + "/source", max_results=10)),
    ("replace-directory", lambda files: files.write_text(BASE + "/directory", "CHANGED", mode="replace")),
    ("copy-directory", lambda files: files.copy(BASE + "/directory", BASE + "/copy")),
    ("move-over-directory", lambda files: files.move(BASE + "/source", BASE + "/directory", replace=True)),
    ("mkdir-below-file", lambda files: files.mkdir(BASE + "/source/child")),
]


@pytest.mark.parametrize("name,operation", WRONG_TYPES, ids=[name for name, _ in WRONG_TYPES])
async def test_wrong_file_types_preserve_source_and_nonempty_destination(file_backend, name, operation):
    backend = file_backend
    (backend.root / "file-tests/directory/keep").write_text("MUST_KEEP")
    before = backend.snapshot()
    code = (
        "environment_request_invalid"
        if backend.kind == "direct-local"
        else ("environment_conflict" if name == "mkdir-below-file" else "environment_denied")
    )
    with failure(code):
        await operation(backend.environment.operations.files)
    assert backend.snapshot() == before


async def test_writable_provider_exposes_mkdir_copy_and_patch(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    await files.mkdir(BASE + "/new")
    await files.copy(BASE + "/source", BASE + "/new/copy")
    await files.patch_text(BASE + "/new/copy", "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n")
    assert (backend.root / "file-tests/new/copy").read_text() == "CHANGED\n"
    assert (backend.root / "file-tests/source").read_text() == "ORIGINAL\n"


async def test_os_permission_failures_do_not_disclose_or_modify_files(file_backend):
    if os.name == "nt":
        pytest.skip("POSIX permissions require a Unix host")
    backend, files = file_backend, file_backend.environment.operations.files
    base = backend.root / "file-tests"
    locked, unreadable = base / "locked", base / "unreadable"
    locked.mkdir()
    (locked / "secret").write_text("PRIVATE")
    unreadable.write_text("PRIVATE")
    before = backend.snapshot()
    try:
        locked.chmod(0)
        unreadable.chmod(0)
        if backend.kind == "docker":
            await backend.native_shell(
                'test "$(id -u)" != 0; test ! -r /workspace/file-tests/unreadable', user="sandbox"
            )
        else:
            assert os.geteuid() != 0, "POSIX permission tests must run as a non-root user"
            with pytest.raises(PermissionError):
                unreadable.read_bytes()
        for operation in (
            lambda: files.read_bytes(BASE + "/unreadable"),
            lambda: read_stream(files, BASE + "/unreadable"),
            lambda: files.read_text(BASE + "/unreadable"),
            lambda: files.read_bytes(BASE + "/locked/secret"),
            lambda: files.list(BASE + "/locked", max_results=10),
            lambda: files.mkdir(BASE + "/locked/new"),
            lambda: files.remove(BASE + "/locked/secret"),
            lambda: files.move(BASE + "/source", BASE + "/locked/moved"),
            lambda: files.write_text(BASE + "/locked/new", "CHANGED", mode="create"),
        ):
            with failure("environment_denied") as error:
                await operation()
            assert "PRIVATE" not in str(error.value) and str(backend.root) not in str(error.value)
    finally:
        locked.chmod(before["locked"][1])
        unreadable.chmod(before["unreadable"][1])
    assert backend.snapshot() == before


async def test_aborted_stream_preserves_destination_and_cleans_staging(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    before = backend.snapshot()

    async def interrupted():
        yield b"partial"
        raise RuntimeError("fixture upload interrupted")

    for mode in ("create", "replace", "append"):
        destination = BASE + ("/new" if mode == "create" else "/source")
        with pytest.raises((RuntimeError, EnvironmentError)):
            await files.write_bytes_stream(destination, interrupted(), mode=mode)
        assert backend.snapshot() == before
