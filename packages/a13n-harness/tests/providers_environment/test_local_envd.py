from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.local_envd import _daemon as daemon_module
from a13n_harness.providers.environment.local_envd import provider as provider_module
from a13n_harness.providers.environment.local_envd import runtime as runtime_module
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
)
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD, LocalEnvdEnvironment
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


class BoundEIP:
    def __init__(self, directory, generation):
        self.descriptor = EnvironmentDescriptor(
            generation=generation,
            working_directory=directory,
            operation_families=frozenset(),
            permissions=EnvironmentPermissionSet(),
        )
        self.operations = EnvironmentOperations()
        self.availability = EnvironmentAvailability(status="available")
        self.closed = False

    def bind_mount(self, mount_id):
        pass

    async def ensure_ready(self, operations):
        pass


def runtime(tmp_path, *, discovery=True):
    return LocalEnvdProviderRuntime(
        executable=tmp_path / "a13n-envd",
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path, directory_discovery=discovery),
    )


def environment(owner, directory):
    if isinstance(directory, Path):
        value = directory.as_posix()
        directory = "/UNC/" + value[2:] if value.startswith("//") else "/" + value if directory.drive else value
    return LocalEnvdEnvironment(
        LocalEnvdEnvironmentConfiguration(working_directory=directory), owner, environment_id="local-test"
    )


@pytest.fixture
def owners(monkeypatch):
    owners = []

    class Daemon:
        def __init__(self):
            self.device = None
            self.closed = False
            owners.append(self)

        async def launch(self, executable, allocator, configuration, *, device_id, launch_factory):
            assert launch_factory is None
            await asyncio.sleep(0)
            self.configuration = configuration
            self.device = SimpleNamespace(descriptor=SimpleNamespace(device_id=device_id))
            return self.device

        async def close(self):
            self.closed = True

    monkeypatch.setattr(runtime_module, "LocalDaemon", Daemon)
    return owners


@pytest.fixture
def scopes(monkeypatch):
    scopes = []

    @asynccontextmanager
    async def open_environment(**kwargs):
        bound = BoundEIP(kwargs["working_directory"], f"1:session-{len(scopes)}")
        scopes.append(bound)
        try:
            yield bound
        finally:
            bound.closed = True

    monkeypatch.setattr(provider_module, "open_eip_environment", open_environment)
    return scopes


async def test_local_adapters_share_device_but_own_distinct_scopes(tmp_path, owners, scopes):
    owner = runtime(tmp_path)
    first = environment(owner, tmp_path)
    second = environment(owner, tmp_path)
    assert owners == []
    await first.enter(mount_id="m")
    assert owners == []
    await asyncio.gather(first.prepare(), second.prepare())
    assert len(owners) == 1
    assert first.descriptor.generation != second.descriptor.generation
    assert first.descriptor.backing_identity == second.descriptor.backing_identity
    assert first.dump_state() is None
    await first.close()
    assert scopes[0].closed and not scopes[1].closed and not owners[0].closed
    await second.close()
    assert scopes[1].closed and not owners[0].closed
    await owner.close()
    assert owners[0].closed and tmp_path.is_dir()
    with pytest.raises(EnvironmentProviderError):
        await owner.acquire_device()


async def test_backing_identity_tracks_filesystem_and_launch_not_sessions(tmp_path, owners, scopes):
    root = tmp_path / "workspace"
    root.mkdir()

    async def observe(*, discovery=True):
        async with runtime(tmp_path, discovery=discovery) as owner:
            adapter = environment(owner, root)
            assert adapter.descriptor.backing_identity is None
            try:
                await adapter.prepare()
                return adapter.descriptor.backing_identity
            finally:
                await adapter.close()

    identity = await observe()
    assert identity is not None
    (root / "content").write_text("ordinary write")
    assert await observe() == identity
    assert await observe(discovery=False) != identity
    root.rename(tmp_path / "old-workspace")
    root.mkdir()
    assert await observe() != identity


async def test_local_session_open_failure_does_not_close_shared_device(tmp_path, owners, monkeypatch):
    owner = runtime(tmp_path)

    @asynccontextmanager
    async def failed(**kwargs):
        raise ValueError("invalid cwd")
        yield

    monkeypatch.setattr(provider_module, "open_eip_environment", failed)
    adapter = environment(owner, "/missing")
    with pytest.raises(EnvironmentProviderError) as failure:
        await adapter.prepare()
    assert failure.value.code == "provider_session_failed"
    assert isinstance(failure.value.__cause__, ValueError)
    assert len(owners) == 1 and not owners[0].closed
    assert await owner.acquire_device() is owners[0].device
    await owner.close()


async def test_local_session_open_cancellation_preserves_shared_device(tmp_path, owners, monkeypatch):
    started = asyncio.Event()

    @asynccontextmanager
    async def pending(**kwargs):
        started.set()
        await asyncio.Event().wait()
        yield

    monkeypatch.setattr(provider_module, "open_eip_environment", pending)
    async with runtime(tmp_path) as owner:
        adapter = environment(owner, tmp_path)
        task = asyncio.create_task(adapter.prepare())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await adapter.close()
        assert not owners[0].closed
        assert await owner.acquire_device() is owners[0].device


async def test_local_runtime_shutdown_waits_for_launch_and_fences_acquisition(tmp_path, owners, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    original = runtime_module.LocalDaemon.launch

    async def blocked(self, *args, **kwargs):
        started.set()
        await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(runtime_module.LocalDaemon, "launch", blocked)
    owner = runtime(tmp_path)
    acquire = asyncio.create_task(owner.acquire_device())
    await started.wait()
    close = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    await asyncio.gather(acquire, close)
    assert len(owners) == 1 and owners[0].closed
    with pytest.raises(EnvironmentProviderError):
        await owner.acquire_device()


async def test_local_lifecycle_never_destroys_host_files(tmp_path):
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    async with runtime(tmp_path) as owner:
        adapter = environment(owner, tmp_path)
        for operation in (adapter.stop, adapter.destroy):
            with pytest.raises(EnvironmentProviderError) as failure:
                await operation()
            assert failure.value.code == "provider_operation_unsupported"
        await adapter.close()
        assert marker.read_text() == "preserve" and adapter.dump_state() is None


def test_local_configuration_contains_only_session_selection(tmp_path):
    for obsolete in ({"workspace": {"path": str(tmp_path)}}, {"execution_network": "deny"}, {"max_file_bytes": 10}):
        with pytest.raises(ValidationError):
            LocalEnvdEnvironmentConfiguration.model_validate(obsolete)
    assert LocalEnvdEnvironmentConfiguration(working_directory="/C:/work").working_directory == "/C:/work"
    with pytest.raises(ValidationError):
        LocalEnvdEnvironmentConfiguration(working_directory="relative")
    with pytest.raises(EnvironmentProviderError) as failure:
        LOCAL_ENVD.construct(
            configuration=LocalEnvdEnvironmentConfiguration(),
            environment_id="logical",
            state=EnvironmentState(provider_key=LOCAL_ENVD.type, state_version="1", state={"pid": 12}),
            runtime=runtime(tmp_path),
        )
    assert failure.value.code == "provider_state_invalid"


@pytest.mark.parametrize("output_size", [70000, 10000000])
async def test_runtime_version_output_is_bounded(output_size):
    with pytest.raises(EnvironmentProviderError, match="bounded limit"):
        await daemon_module._run_checked_subprocess(
            Path(sys.executable), "-c", f"import sys; sys.stdout.write('x' * {output_size})", environment=None
        )


async def test_runtime_validation_reads_both_pipes_without_deadlock():
    stdout, stderr = await daemon_module._run_checked_subprocess(
        Path(sys.executable),
        "-c",
        "import sys; sys.stderr.write('e' * 60000); sys.stdout.write('o' * 60000)",
        environment=None,
    )
    assert stdout == b"o" * 60000 and stderr == b"e" * 60000


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process state inspection")
async def test_cancelled_runtime_validation_stops_its_process_group(tmp_path):
    from anyio import move_on_after

    child_file = tmp_path / "child.pid"
    script = "import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
    with move_on_after(0.5) as scope:
        await daemon_module._run_checked_subprocess(
            Path(sys.executable), "-c", script, str(child_file), environment=None
        )
    assert scope.cancel_called
    status = Path(f"/proc/{child_file.read_text()}/stat")
    async with asyncio.timeout(2):
        while True:
            try:
                state = status.read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                # Reaping can remove procfs state before opening or during the read.
                break
            if state == "Z":
                break
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("directory", [None, "/project"])
async def test_lazy_descriptor_preserves_recipe_working_directory(tmp_path, directory):
    from a13n_harness.environment.sources import EnvironmentMount

    owner = runtime(tmp_path)
    recipe = LocalEnvdEnvironmentConfiguration(working_directory=directory)
    adapter = LocalEnvdEnvironment(recipe, owner, environment_id="local-test")
    assert LOCAL_ENVD.describe_environment(recipe).working_directory == (directory or "/")
    assert adapter.descriptor.working_directory == (directory or "/")
    assert EnvironmentMount(adapter).working_directory == (directory or "/")
    assert EnvironmentMount(adapter, working_directory="/override").working_directory == "/override"
    await adapter.close()
    await owner.close()
