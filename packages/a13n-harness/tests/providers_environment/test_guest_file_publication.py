"""Run staged writes through GuestFiles and the shipped helper in real POSIX processes."""

import asyncio
import stat
import sys

import pytest
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

    configuration = CommandConfiguration(root=str(tmp_path), python=sys.executable)
    return GuestFiles(NativeCommands(execute, configuration, "generation-fixture", "mount-fixture"))


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
