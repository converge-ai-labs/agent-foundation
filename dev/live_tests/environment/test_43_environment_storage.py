"""Real ENOSPC at both filesystem implementations and EIP transfer transports."""

import asyncio
import json
import logging
import os
from contextlib import closing
from uuid import uuid4

import docker
import pytest
from docker.constants import DEFAULT_DOCKER_API_VERSION

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.mark.parametrize("backend", ["direct_local", "local_envd", "http_envd", "websocket_envd"])
async def test_full_filesystem_preserves_files_and_releases_candidates(request, backend):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for disposable limited-storage containers")
    image = os.environ.get("LIVE_TEST_FILE_RESOURCE_IMAGE", "a13n-file-resources:local")

    def exercise():
        with closing(docker.from_env(timeout=100, version=DEFAULT_DOCKER_API_VERSION)) as client:
            host_config = client.api.create_host_config(
                network_mode="none",
                mem_limit="512m",
                pids_limit=128,
                tmpfs={"/limited": "size=1m,uid=10001,gid=10001,mode=0700"},
            )
            created = client.api.create_container(
                image, [backend], name="a13n-file-storage-" + uuid4().hex, host_config=host_config
            )
            container = client.containers.get(created["Id"])
            try:
                container.start()
                result = container.wait(timeout=90)
                output = container.logs().decode()
                assert result["StatusCode"] == 0, output
                return json.loads(output.strip().splitlines()[-1])
            finally:
                container.remove(force=True)
                assert not client.containers.list(all=True, filters={"id": container.id}), "Storage fixture leaked"

    result = await asyncio.to_thread(exercise)
    assert result["backend"] == backend
    assert result["errno"] == 28 and result["capacity"] <= 1024 * 1024
    assert result["destination_preserved"] and result["staging_cleaned"] and result["recovered"]
    assert set(result["failures"]) == {"create", "replace", "append"}
    logger.info(
        "Real ENOSPC backend=%s capacity=%s preserved destination and staging; retry recovered",
        backend,
        result["capacity"],
    )
