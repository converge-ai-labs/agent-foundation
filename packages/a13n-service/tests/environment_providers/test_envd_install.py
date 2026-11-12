"""Real POSIX job recovery without a cloud account or guest Python."""

import asyncio
import hashlib

import pytest
from a13n_service.providers.environment.envd_install import dispatch

pytestmark = pytest.mark.anyio


async def run(script, expected, attempt, root):
    process = await asyncio.create_subprocess_exec(
        "sh", "-c", dispatch(script, expected, attempt, root=str(root)), stdout=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    assert process.returncode == 0
    return output.decode().strip()


async def test_frozen_install_rejoins_across_deployments_and_explicit_retry(tmp_path):
    root = tmp_path / "job"
    script = "while [ ! -f finish ]; do sleep .01; done; printf original >> evidence; exit 1"
    expected = hashlib.sha256(script.encode()).hexdigest()
    assert await run(script, expected, "first", root) == "running"
    try:
        assert await run("exit 0", expected, "first", root) == "running"
        (root / "finish").touch()
        # The runner writes status before releasing its lock; observe completion through dispatch.
        async with asyncio.timeout(5):
            while (state := await run("exit 0", expected, "first", root)) == "running":
                await asyncio.sleep(0.01)
        assert state == "failed"
        (root / "finish").unlink()
        assert await run("exit 0", expected, "retry", root) == "running"
        (root / "finish").touch()
        async with asyncio.timeout(5):
            while (state := await run("exit 0", expected, "retry", root)) == "running":
                await asyncio.sleep(0.01)
        assert state == "failed"
        assert (root / "evidence").read_text() == "originaloriginal"
        assert (root / "install.sh").read_text() == script
        assert await run("exit 0", "b" * 64, "different", root) == "conflict"
        assert root.stat().st_mode & 0o777 == 0o700
    finally:
        (root / "finish").touch()


async def test_unstarted_old_revision_cannot_install_new_deployment_script(tmp_path):
    root = tmp_path / "job"
    assert await run("exit 0", "b" * 64, "first", root) == "incompatible"
    assert not (root / "revision").exists()
    assert not (root / "install.sh").exists()


async def test_dead_child_is_terminal_for_same_operation(tmp_path):
    root = tmp_path / "job"
    root.mkdir()
    script = "exit 0"
    expected = hashlib.sha256(script.encode()).hexdigest()
    (root / "revision").write_text(expected)
    (root / "attempt").write_text("first")
    (root / "status").write_text("running")
    assert await run(script, expected, "first", root) == "failed"
    assert (root / "status").read_text() == "failed"
