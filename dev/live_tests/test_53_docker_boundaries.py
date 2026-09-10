"""Real bootstrap loss, Engine transport loss, and externally owned Docker volumes."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from a13n_environment import DockerSDKEngine, EnvironmentProviderError

from .file_backends import FileBackend
from .tcp_proxy import TCPProxy

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def live_opt_in(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real Docker boundary faults")


async def act(backend, environment, action):
    if action == "prepare":
        await backend.prepare(environment)
    else:
        await getattr(environment, action)()


@pytest.mark.parametrize("fault", ["missing-directory", "missing-credential", "corrupt-manifest", "corrupt-config"])
@pytest.mark.parametrize("action", ["prepare", "reconcile", "stop", "destroy"])
async def test_bootstrap_failure_never_retargets_or_mutates_container(tmp_path, fault, action):
    async with FileBackend("docker", tmp_path).open() as backend:
        original = backend.environment
        state = original.dump_state()
        identity = state.state["container_id"]
        allocation = await backend.runtime.bootstrap_store.recover(state.state["bootstrap_correlation"])
        await original.close()
        saved = tmp_path / "saved-bootstrap"
        file = None
        if fault == "missing-directory":
            allocation.directory.rename(saved)
        else:
            file = (
                allocation.directory
                / {
                    "missing-credential": "credential",
                    "corrupt-manifest": "manifest.json",
                    "corrupt-config": "envd.json",
                }[fault]
            )
            content = file.read_bytes()
            if fault == "missing-credential":
                file.unlink()
            else:
                file.write_bytes(b"invalid-test-material")
        try:
            rejected = backend.adapter(state=state)
            with pytest.raises(EnvironmentProviderError):
                await act(backend, rejected, action)
            assert rejected.dump_state() == state
            assert (await backend.engine.inspect_container(identity)).status == "running"
            targets = await backend.engine.find_containers({"io.a13n.environment-id": backend.identity})
            assert [target.container_id for target in targets] == [identity]
            assert (backend.root / "file-tests/source").read_bytes() == b"ORIGINAL\n"
        finally:
            if file is None:
                saved.rename(allocation.directory)
            else:
                file.write_bytes(content)
                file.chmod(0o644)
        recovered = await backend.prepare(backend.adapter(state=state))
        assert recovered.dump_state() == state


class DockerSocketProxy(TCPProxy):
    def __init__(self, socket):
        super().__init__("127.0.0.1", 0)
        self.socket = socket

    async def _open_upstream(self):
        return await asyncio.open_unix_connection(self.socket)


@pytest.mark.parametrize("action", ["prepare", "reconcile", "stop", "destroy"])
async def test_engine_connection_loss_is_unknown_not_target_absence(tmp_path, action):
    endpoint = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    assert endpoint.startswith("unix://"), "This fault fixture requires a local Unix Docker socket"
    socket = Path(endpoint.removeprefix("unix://"))
    async with FileBackend("docker", tmp_path).open() as backend, DockerSocketProxy(socket).listen() as proxy:
        state = backend.environment.dump_state()
        await backend.environment.close()
        engine = DockerSDKEngine.connect(f"http://127.0.0.1:{proxy.local_port}", timeout_seconds=2)
        try:
            assert (await engine.inspect_container(state.state["container_id"])).status == "running"
            proxy.cut()
            rejected = backend.adapter(state=state, runtime=replace(backend.runtime, engine=engine))
            with pytest.raises(EnvironmentProviderError):
                await act(backend, rejected, action)
            assert rejected.dump_state() == state
            assert proxy.rejected > 0
            assert (await backend.engine.inspect_container(state.state["container_id"])).status == "running"
            proxy.restore()
            recovered = backend.adapter(state=state, runtime=replace(backend.runtime, engine=engine))
            assert await recovered.reconcile() == "running"
            assert recovered.dump_state() == state
            targets = await engine.find_containers({"io.a13n.environment-id": backend.identity})
            assert [target.container_id for target in targets] == [state.state["container_id"]]
        finally:
            proxy.restore()
            await engine.close()


async def test_destroy_preserves_external_named_volume_and_its_contents(tmp_path):
    async with FileBackend("docker", tmp_path).open(prepare=False) as backend:
        volume = await asyncio.to_thread(backend.engine._client.volumes.create, name="a13n-live-" + uuid4().hex)
        try:
            configuration = backend.configuration.model_dump(mode="json")
            configuration["mounts"].append(
                {
                    "mount_id": "external",
                    "container_path": "/external",
                    "source": {"kind": "volume", "name": volume.name},
                }
            )
            backend.configuration = backend.provider.validate_configuration(schema_version="1", value=configuration)
            backend.environment = backend.adapter()
            await backend.prepare(backend.environment)
            await backend.native_shell("printf EXTERNAL_VOLUME > /external/sentinel")
            state = backend.environment.dump_state()
            await backend.environment.close()
            await backend.adapter(state=state).destroy()
            assert await backend.engine.inspect_container(state.state["container_id"]) is None
            await asyncio.to_thread(volume.reload)
            # A new exact target mounts the same externally retained volume.
            backend.environment = backend.adapter()
            await backend.prepare(backend.environment)
            assert backend.environment.dump_state().state["container_id"] != state.state["container_id"]
            assert await backend.native_shell("cat /external/sentinel") == "EXTERNAL_VOLUME"
        finally:
            try:
                await backend.environment.close()
                cleanup = backend.adapter(state=backend.environment.dump_state())
                if await cleanup.reconcile() != "absent":
                    await cleanup.destroy()
            finally:
                # Preserve the failure while removing only this fixture's exact
                # targets before releasing its externally owned volume.
                for target in await backend.engine.find_containers({"io.a13n.environment-id": backend.identity}):
                    await backend.engine.stop_container(target.container_id, timeout_seconds=1)
                    await backend.engine.remove_container(target.container_id)
                await asyncio.to_thread(volume.remove)
