"""File semantics across Direct Local and the three real Envd carriers."""

import logging
import os
from contextlib import contextmanager

import pytest
from a13n_harness.providers.environment.models import EnvironmentError

from .file_backends import KINDS, FileBackend
from .file_contract import (
    MISSING,
    READS,
    WRONG_TYPES,
    assert_traversal_rejected,
    read_stream,
)

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture(params=KINDS)
def file_backend_kind(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real native file boundary tests")
    return request.param


@pytest.fixture
async def file_backend(file_backend_kind, tmp_path):
    async with FileBackend(file_backend_kind, tmp_path).open() as backend:
        yield backend


@contextmanager
def failure(code):
    with pytest.raises(EnvironmentError) as error:
        yield error
    assert error.value.code == code
    logger.info("File conformance failure code=%s", code)


async def test_file_traversal_rejects_source_and_destination_before_mutation(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    before = backend.snapshot()
    for path in (
        "../sentinel",
        "/../sentinel",
        file_backend.base + "/../../sentinel",
        file_backend.base + "/nul\x00name",
    ):
        await assert_traversal_rejected(files, path, failure, base=file_backend.base)
        assert backend.snapshot() == before
        assert await backend.outside_content() == "OUTSIDE_UNCHANGED"


async def test_file_symlink_escape_and_provider_link_replacement_semantics(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    base = backend.root / "file-tests"
    (base / "escape").symlink_to(backend.outside, target_is_directory=True)
    (base / "link").symlink_to(backend.outside + "/sentinel")
    before = backend.snapshot()
    if backend.kind != "direct_local":
        # Device paths, including resolved directory symlinks, are not confined
        # to a Session's cwd. The external fixture directory is Host-owned too.
        for _, operation in READS:
            result = await operation(files, file_backend.base + "/escape/sentinel")
            assert result is not None
        await files.write_text(file_backend.base + "/escape/new", "OUTSIDE_CWD", mode="create")
        await files.remove(file_backend.base + "/escape/new")
        assert (await files.stat(file_backend.base + "/link")).kind == "symlink"
        await files.move(file_backend.base + "/source", file_backend.base + "/link", replace=True)
        assert not (base / "link").is_symlink()
        assert (base / "link").read_text() == "ORIGINAL\n"
        assert await backend.outside_content() == "OUTSIDE_UNCHANGED"
        return
    for path in (file_backend.base + "/escape/sentinel", file_backend.base + "/link"):
        for _, operation in READS:
            with failure("environment_denied"):
                await operation(files, path)
        for operation in (
            lambda path=path: files.write_text(path, "CHANGED", mode="upsert"),
            lambda path=path: files.copy(file_backend.base + "/source", path, replace=True),
        ):
            with failure("environment_denied"):
                await operation()
        assert backend.snapshot() == before
    with failure("environment_denied"):
        await files.move(file_backend.base + "/source", file_backend.base + "/escape/sentinel", replace=True)
    assert backend.snapshot() == before
    assert (await files.stat(file_backend.base + "/link")).kind == "symlink"
    with failure("environment_denied"):
        await files.move(file_backend.base + "/source", file_backend.base + "/link", replace=True)
    assert backend.snapshot() == before
    await files.move(file_backend.base + "/escape", file_backend.base + "/moved-link")
    await files.remove(file_backend.base + "/moved-link")
    assert await backend.outside_content() == "OUTSIDE_UNCHANGED"


@pytest.mark.parametrize("name,operation", MISSING, ids=[name for name, _ in MISSING])
async def test_missing_file_errors_preserve_directory_entries(file_backend, name, operation):
    before = file_backend.snapshot()
    with failure("environment_not_found"):
        await operation(file_backend.environment.operations.files, file_backend.base + "/missing")
    assert file_backend.snapshot() == before


async def test_append_requires_existing_file_for_local_and_envd(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    before = backend.snapshot()
    # These providers require an existing file; E2B's create-on-append has its own test.
    with failure("environment_not_found"):
        await files.write_text(file_backend.base + "/new", "suffix", mode="append")
    assert backend.snapshot() == before
    await files.write_text(file_backend.base + "/source", "suffix", mode="append")
    assert (await files.read_text(file_backend.base + "/source")).text == "ORIGINAL\nsuffix"


@pytest.mark.parametrize("name,operation", WRONG_TYPES, ids=[name for name, _ in WRONG_TYPES])
async def test_wrong_file_types_preserve_source_and_nonempty_destination(file_backend, name, operation):
    backend = file_backend
    (backend.root / "file-tests/directory/keep").write_text("MUST_KEEP")
    before = backend.snapshot()
    code = "environment_request_invalid" if backend.kind == "direct_local" else "environment_denied"
    with failure(code):
        await operation(backend.environment.operations.files, backend.base)
    assert backend.snapshot() == before


async def test_writable_provider_exposes_mkdir_copy_and_patch(file_backend):
    backend, files = file_backend, file_backend.environment.operations.files
    await files.mkdir(file_backend.base + "/new")
    await files.copy(file_backend.base + "/source", file_backend.base + "/new/copy")
    await files.patch_text(file_backend.base + "/new/copy", "@@ -1 +1 @@\n-ORIGINAL\n+CHANGED\n")
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
            lambda: files.read_bytes(file_backend.base + "/unreadable"),
            lambda: read_stream(files, file_backend.base + "/unreadable"),
            lambda: files.read_text(file_backend.base + "/unreadable"),
            lambda: files.read_bytes(file_backend.base + "/locked/secret"),
            lambda: files.list(file_backend.base + "/locked", max_results=10),
            lambda: files.mkdir(file_backend.base + "/locked/new"),
            lambda: files.remove(file_backend.base + "/locked/secret"),
            lambda: files.move(file_backend.base + "/source", file_backend.base + "/locked/moved"),
            lambda: files.write_text(file_backend.base + "/locked/new", "CHANGED", mode="create"),
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
        destination = file_backend.base + ("/new" if mode == "create" else "/source")
        with pytest.raises((RuntimeError, EnvironmentError)):
            await files.write_bytes_stream(destination, interrupted(), mode=mode)
        assert backend.snapshot() == before
