"""ENOSPC through the production Docker Provider over an owned 1 MiB tmpfs volume."""

import asyncio
import json
import logging
import os
from uuid import uuid4

import pytest
from a13n_environment import EnvironmentError

from .file_backends import FileBackend

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


async def test_docker_provider_enospc_preserves_files_and_recovers(request, tmp_path):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real Docker Provider ENOSPC")
    async with FileBackend("docker", tmp_path).open(prepare=False) as backend:
        volume = await asyncio.to_thread(
            backend.engine._client.volumes.create,
            name="a13n-limited-" + uuid4().hex,
            driver="local",
            driver_opts={"type": "tmpfs", "device": "tmpfs", "o": "size=1m,uid=10001,gid=10001,mode=0700"},
        )
        try:
            configuration = backend.configuration.model_dump(mode="json")
            configuration["image"] = os.environ.get("LIVE_TEST_DOCKER_RESOURCE_IMAGE", "a13n-file-resources:docker")
            configuration["mounts"][0]["source"] = {"kind": "volume", "name": volume.name}
            backend.configuration = backend.provider.validate_configuration(schema_version="1", value=configuration)
            backend.environment = backend.adapter()
            await backend.prepare(backend.environment)

            async def evidence(action):
                return json.loads(
                    await backend.native_shell(
                        'python /app/file_resource_worker.py evidence "$1" /workspace', action, user="10001"
                    )
                )

            initial = await evidence("prepare")
            assert 0 < initial["capacity"] <= 1024 * 1024
            files = backend.environment.operations.files

            async def payload():
                for _ in range(8):
                    yield b"y" * 65536

            for mode in ("create", "replace", "append"):
                with pytest.raises(EnvironmentError) as caught:
                    await files.write_bytes_stream("/new" if mode == "create" else "/destination", payload(), mode=mode)
                assert caught.value.code == "environment_provider_failure"
                assert (await evidence("snapshot"))["files"] == initial["files"]
            probe = await evidence("probe")
            assert probe["errno"] == 28 and probe["files"] == initial["files"]
            await evidence("free")
            await files.write_text("/destination", "RECOVERED", mode="replace")
            assert await files.read_bytes("/destination") == b"RECOVERED"
            assert set((await evidence("snapshot"))["files"]) == {"destination"}
            logger.info(
                "Docker Provider ENOSPC errno=28 capacity=%s bytes; publication preserved and retry recovered",
                initial["capacity"],
            )
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
