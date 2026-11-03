"""Hard-crash coverage for Native's pre-identity launch gate."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from a13n_service.providers.browser import runtime
from a13n_service.providers.browser.definition import BrowserProviderError
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Recipe

pytestmark = pytest.mark.anyio

# The launcher is a real process. Its child is held immediately before executing
# the actual runtime, allowing the test to resume it after launcher death.
LAUNCHER = r"""
import asyncio, os, sys
from pathlib import Path
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Allocation, Recipe
root, phase, event, release = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
original_save = Allocation.save
original_spawn = asyncio.create_subprocess_exec
original_write = os.write
async def save(self):
    await original_save(self)
    if phase == 'marker' and isinstance(self.record.get('launch_unresolved'), dict):
        original_write(event, b'M'); os.read(release, 1)
Allocation.save = save
async def spawn(*args, **kw):
    gate = kw['pass_fds'][0]
    wrapper = "import os,sys; os.write(int(sys.argv[1]), (str(os.getpid())+'\\n').encode()); os.read(int(sys.argv[2]),1); os.execv(sys.argv[3],sys.argv[3:])"
    kw['pass_fds'] = (gate, event, release)
    child = await original_spawn(sys.executable, '-c', wrapper, str(event), str(release), *args, **kw)
    if phase == 'spawn':
        await asyncio.Event().wait()
    return child
asyncio.create_subprocess_exec = spawn
def write(fd, data):
    if data == b'1' and phase == 'saved':
        original_write(event, b'S\n'); os.read(release, 1)
    result = original_write(fd, data)
    if data == b'1' and phase == 'token':
        original_write(event, b'T\n'); os.read(release, 1)
    return result
os.write = write
async def main():
    p = Native(root, 'chromium')
    async with p.allocation('browser_gate') as a:
        a.record = {'backend':'native', 'generation':1, 'recipe':Recipe().model_dump()}
        await p.provision(a)
        await p.launch(a)
asyncio.run(main())
"""


async def read_exact(fd, size=1):
    return await asyncio.wait_for(asyncio.to_thread(os.read, fd, size), 10)


@pytest.mark.parametrize("phase", ["marker", "spawn", "saved", "token"])
async def test_native_gate_launcher_crash_windows(tmp_path, monkeypatch, phase):
    event_r, event_w = os.pipe()
    release_r, release_w = os.pipe()
    launcher = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        LAUNCHER,
        str(tmp_path),
        phase,
        str(event_w),
        str(release_r),
        pass_fds=(event_w, release_r),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    os.close(event_w)
    os.close(release_r)
    child_pid = None
    new_child = None
    new_task = None
    try:
        if phase == "marker":
            assert await read_exact(event_r) == b"M"
        else:
            messages = []
            for _ in range(2 if phase in {"saved", "token"} else 1):
                line = bytearray()
                while (byte := await read_exact(event_r)) != b"\n":
                    line += byte
                messages.append(bytes(line))
            child_pid = int(next(message for message in messages if message.isdigit()))
            if phase in {"saved", "token"}:
                assert (b"S" if phase == "saved" else b"T") in messages
        record_path = tmp_path / "browser_gate" / "allocation.json"
        record = json.loads(record_path.read_text())
        if phase in {"marker", "spawn"}:
            assert isinstance(record["launch_unresolved"], dict)
        else:
            assert "launch_unresolved" not in record
            assert record["pid"] == child_pid and record["started"]
        launcher.kill()
        await asyncio.wait_for(launcher.wait(), 10)
        provider = Native(tmp_path, "chromium")
        if phase in {"marker", "spawn"}:
            live = await provider.inspect("browser_gate", None)
            assert live.status == "stopped"
            assert "launch_unresolved" not in json.loads(record_path.read_text())
            # A new actual gated launch holds the profile generation while the
            # delayed old supervisor is resumed. No fake success is returned.
            original_spawn = asyncio.create_subprocess_exec
            entered = tmp_path / "new-generation"

            async def spawn_new(*args, **kwargs):
                nonlocal new_child
                fd = kwargs["pass_fds"][0]
                code = "import os,sys,time; assert os.read(int(sys.argv[1]),1)==b'1'; open(sys.argv[2],'w').close(); time.sleep(60)"
                new_child = await original_spawn(sys.executable, "-c", code, str(fd), str(entered), **kwargs)
                return new_child

            monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_new)
            new_task = asyncio.create_task(provider.start("browser_gate", {}, generation=2))
            async with asyncio.timeout(10):
                while not entered.exists():
                    await asyncio.sleep(0.01)
            assert json.loads(record_path.read_text())["generation"] == 2
            if child_pid:
                os.write(release_w, b"x")
                async with asyncio.timeout(10):
                    while runtime.process_identity(child_pid) is not None:
                        await asyncio.sleep(0.01)
            assert not (tmp_path / "browser_gate/data/runtime.json").exists()
            assert not (tmp_path / "browser_gate/data/profile").exists()
        else:
            with pytest.raises(BrowserProviderError) as error:
                await provider.inspect("browser_gate", None)
            assert error.value.code == "native_start_pending"
            # Before token, the delayed runtime receives EOF. After token it is
            # already recorded and the existing identity path remains fenced.
            if phase == "saved":
                os.write(release_w, b"x")
                async with asyncio.timeout(10):
                    while runtime.process_identity(child_pid) is not None:
                        await asyncio.sleep(0.01)
                assert not (tmp_path / "browser_gate/data/runtime.json").exists()
    finally:
        if launcher.returncode is None:
            launcher.kill()
            await launcher.wait()
        if child_pid and runtime.process_identity(child_pid) is not None:
            os.kill(child_pid, signal.SIGKILL)
        if new_task:
            new_task.cancel()
            try:
                await new_task
            except asyncio.CancelledError:
                pass
        if new_child and new_child.returncode is None:
            new_child.kill()
            await new_child.wait()
        os.close(event_r)
        os.close(release_w)


@pytest.mark.parametrize("marker", [True, {}, {"version": 1, "pid": 0, "started": "1"}])
async def test_native_legacy_or_invalid_marker_stays_fenced(tmp_path, marker):
    provider = Native(tmp_path, "chromium")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    async with provider.allocation("browser_legacy") as allocation:
        allocation.record = {"backend": "native", "boot": boot, "launch_unresolved": marker}
        await provider.provision(allocation)
    with pytest.raises(BrowserProviderError) as error:
        await provider.inspect("browser_legacy", None)
    assert error.value.code == "native_writer_unconfirmed"
    with pytest.raises(BrowserProviderError):
        await provider.delete("browser_legacy", None)
    record = json.loads((tmp_path / "browser_legacy/allocation.json").read_text())
    assert record["deleted"] and record["launch_unresolved"] == marker


async def test_native_live_and_indeterminate_launcher_remains_fenced(tmp_path, monkeypatch):
    provider = Native(tmp_path, "chromium")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    marker = {"version": 1, "pid": os.getpid(), "started": runtime.process_identity(os.getpid())}
    async with provider.allocation("browser_alive") as allocation:
        allocation.record = {"backend": "native", "boot": boot, "launch_unresolved": marker}
        await provider.provision(allocation)
    with pytest.raises(BrowserProviderError):
        await provider.inspect("browser_alive", None)
    monkeypatch.setattr(runtime, "process_identity", lambda pid: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(BrowserProviderError):
        await provider.delete("browser_alive", None)
    assert json.loads((tmp_path / "browser_alive/allocation.json").read_text())["launch_unresolved"] == marker


async def test_native_dead_launcher_delete_retry_preserves_old_identity(tmp_path, monkeypatch):
    provider = Native(tmp_path, "chromium")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    marker = {"version": 1, "pid": os.getpid(), "started": "old-launcher"}
    async with provider.allocation("browser_deleted") as allocation:
        allocation.record = {
            "backend": "native",
            "boot": boot,
            "launch_unresolved": marker,
            "pid": 12345,
            "started": "old",
        }
        await provider.provision(allocation)
    monkeypatch.setattr(runtime, "group_alive", lambda pid: pid == 12345)
    monkeypatch.setattr("a13n_service.providers.browser.native._CLEANUP_TIMEOUT", 0.01)
    # halt's group wait has its own timeout: simulate a live old runtime via observe.
    with pytest.raises(BrowserProviderError) as error:
        await provider.inspect("browser_deleted", None)
    assert error.value.code == "native_start_pending"
    record = json.loads((tmp_path / "browser_deleted/allocation.json").read_text())
    assert "launch_unresolved" not in record and record["pid"] == 12345
    monkeypatch.setattr(runtime, "group_alive", lambda pid: False)
    await provider.delete("browser_deleted", None)
    record = json.loads((tmp_path / "browser_deleted/allocation.json").read_text())
    assert record["deleted"] and not (tmp_path / "browser_deleted/data").exists()
    with pytest.raises(BrowserProviderError):
        await provider.start("browser_deleted", {}, generation=2)


async def test_native_identity_save_error_after_replace_retains_identity(tmp_path, monkeypatch):
    from a13n_service.providers.browser.selfhost import Allocation

    provider = Native(tmp_path, "chromium")
    async with provider.allocation("browser_published") as allocation:
        allocation.record = {"backend": "native", "generation": 1, "recipe": Recipe().model_dump()}
        await provider.provision(allocation)
    original_save = Allocation.save
    original_spawn = asyncio.create_subprocess_exec
    child = None

    async def save(allocation):
        await original_save(allocation)
        if "pid" in allocation.record and "launch_unresolved" not in allocation.record:
            raise OSError("after replace")

    async def spawn(*args, **kwargs):
        nonlocal child
        fd = kwargs["pass_fds"][0]
        code = "import os,sys; assert os.read(int(sys.argv[1]),1)==b''"
        child = await original_spawn(sys.executable, "-c", code, str(fd), **kwargs)
        return child

    monkeypatch.setattr(Allocation, "save", save)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(OSError, match="after replace"):
        async with provider.allocation("browser_published") as allocation:
            await provider.launch(allocation)
    record = json.loads((tmp_path / "browser_published/allocation.json").read_text())
    assert record["pid"] == child.pid and record["started"]
    assert "launch_unresolved" not in record
    assert child.returncode == 0


async def test_native_old_runtime_info_still_fences_recovered_marker(tmp_path, monkeypatch):
    provider = Native(tmp_path, "chromium")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    async with provider.allocation("browser_old_info") as allocation:
        allocation.record = {
            "backend": "native",
            "boot": boot,
            "pid": 12345,
            "started": "old",
            "launch_unresolved": {"version": 1, "pid": os.getpid(), "started": "previous-launcher"},
        }
        await provider.provision(allocation)
        (allocation.root / "data/runtime.json").write_text(
            json.dumps({"pid": 67890, "started": "old", "runtime_id": "old", "ready": False, "boot": boot})
        )
    monkeypatch.setattr(runtime, "group_alive", lambda pid: pid == 67890)
    with pytest.raises(BrowserProviderError) as error:
        await provider.inspect("browser_old_info", None)
    assert error.value.code == "native_writer_unconfirmed"
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await provider.delete("browser_old_info", None)
    record = json.loads((tmp_path / "browser_old_info/allocation.json").read_text())
    assert record["deleted"] and (tmp_path / "browser_old_info/data").exists()


@pytest.mark.parametrize("failure", ["pipe", "marker_before_replace", "marker_after_replace"])
async def test_native_gate_setup_failure_does_not_strand_launcher(tmp_path, monkeypatch, failure):
    from a13n_service.providers.browser.selfhost import Allocation

    provider = Native(tmp_path, "chromium")
    async with provider.allocation("browser_setup") as allocation:
        allocation.record = {"backend": "native", "generation": 1, "recipe": Recipe().model_dump()}
        await provider.provision(allocation)
    original_save = Allocation.save
    original_pipe = os.pipe
    failed = False

    async def save(allocation):
        nonlocal failed
        is_marker = isinstance(allocation.record.get("launch_unresolved"), dict)
        if is_marker and not failed:
            failed = True
            if failure == "marker_after_replace":
                await original_save(allocation)
            if failure != "pipe":
                raise OSError("marker failure")
        await original_save(allocation)

    def pipe():
        if failure == "pipe":
            raise OSError("pipe failure")
        return original_pipe()

    with monkeypatch.context() as patch:
        patch.setattr(Allocation, "save", save)
        patch.setattr(os, "pipe", pipe)
        with pytest.raises(OSError):
            async with provider.allocation("browser_setup") as allocation:
                await provider.launch(allocation)
    record = json.loads((tmp_path / "browser_setup/allocation.json").read_text())
    assert "launch_unresolved" not in record
    assert (await provider.inspect("browser_setup", None)).status == "stopped"
    await provider.delete("browser_setup", None)
    assert json.loads((tmp_path / "browser_setup/allocation.json").read_text())["deleted"]
