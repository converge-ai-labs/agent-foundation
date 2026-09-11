"""Build an installed plugin image and run its unmodified Service entry point."""

import asyncio
import json
import logging
import subprocess
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import anyio
import docker
import httpx2

from ..infrastructure.config import STATE
from ..infrastructure.round_two_lab import REPOSITORY, free_origin

logger = logging.getLogger(__name__)
FIXTURES = Path(__file__).parent


async def command(log_path, *arguments):
    logger.info("Plugin image stage=%s log=%s", log_path.stem, log_path)
    with log_path.open("wb") as log:
        with anyio.fail_after(900):
            result = await anyio.run_process(
                arguments, cwd=REPOSITORY, stdout=log, stderr=subprocess.STDOUT, check=False
            )
    assert result.returncode == 0, f"Plugin image stage failed; inspect {log_path}"


@asynccontextmanager
async def plugin_image():
    identity = uuid4().hex
    root = STATE / "plugin-images" / identity
    root.mkdir(parents=True, mode=0o700)
    base, image = f"a13n-live-plugin-base:{identity}", f"a13n-live-plugin:{identity}"
    async with AsyncExitStack() as stack:
        await command(
            root / "build-service.log",
            "make",
            "image-a13n-service",
            f"A13N_SERVICE_IMAGE={base}",
        )
        stack.push_async_callback(command, root / "remove-base.log", "docker", "image", "rm", base)
        await command(
            root / "build-wheel.log",
            "uv",
            "build",
            "--wheel",
            "--no-sources",
            str(FIXTURES / "plugin_package"),
            "--out-dir",
            str(root / "wheels"),
        )
        await command(
            root / "install-wheel.log",
            "docker",
            "build",
            "-f",
            str(FIXTURES / "plugin_worker.Dockerfile"),
            "--build-arg",
            f"SERVICE_IMAGE={base}",
            "-t",
            image,
            str(root / "wheels"),
        )
        stack.push_async_callback(command, root / "remove-plugin.log", "docker", "image", "rm", image)
        logger.info("Plugin wheel installed in image=%s; build evidence=%s", image, root)
        yield image


def container_url(url):
    """Linux uses host networking; Docker Desktop forwards its host gateway to loopback."""
    parts = urlsplit(url)
    if sys.platform == "linux" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return url
    credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit(parts._replace(netloc=f"{credentials}host.docker.internal{port}"))


@asynccontextmanager
async def installed_worker(journey, image, *, plugin_keys=("live.packaged",)):
    lab = journey.lab
    effects = lab.root / "plugin-effects"
    effects.mkdir(exist_ok=True)
    # Only this empty fixture directory is shared with the image's non-root user.
    effects.chmod(0o777)
    environment = {key: value for key, value in lab.environment.items() if key.startswith(("A13N_SERVICE_", "AWS_"))}
    for key in ("DATABASE_URL", "REDIS_URL", "OBJECT_ENDPOINT_URL"):
        name = "A13N_SERVICE_" + key
        environment[name] = container_url(environment[name])
    origin = free_origin()
    environment.update(
        {
            "A13N_SERVICE_ROLE": "worker",
            "A13N_SERVICE_PORT": str(urlsplit(origin).port) if sys.platform == "linux" else "8000",
            "A13N_SERVICE_BUILD_VERSION": "live-plugin-image",
            "A13N_SERVICE_GATEWAY_RUN_QUEUE_NAME": "live-test",
            "A13N_SERVICE_PRICING_AUTO_UPDATE": "false",
            "A13N_SERVICE_SECRET_MASTER_KEY_BASE64": lab.config["encryption_key"],
            "A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID": "live-test-1",
            "A13N_SERVICE_PLUGIN_KEYS": json.dumps(plugin_keys),
            "A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_DOMAINS": '["host.docker.internal"]',
            "A13N_SERVICE_OBSERVABILITY_TRACING": "false",
            "NO_PROXY": "*",
        }
    )
    network = {"network_mode": "host"} if sys.platform == "linux" else {"ports": {"8000/tcp": ("127.0.0.1", 0)}}
    client = docker.from_env(timeout=60)
    container = None
    log_path = lab.root / f"plugin-worker-{uuid4().hex}.log"
    try:
        container = await asyncio.to_thread(
            client.containers.create,
            image,
            environment=environment,
            volumes={str(effects): {"bind": "/plugin-effects", "mode": "rw"}},
            **network,
        )
        await asyncio.to_thread(container.start)
        await asyncio.to_thread(container.reload)
        if sys.platform != "linux":
            port = container.attrs["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
            origin = f"http://127.0.0.1:{port}"
        async with httpx2.AsyncClient(timeout=2, trust_env=False) as http:

            async def ready():
                await asyncio.to_thread(container.reload)
                assert container.status == "running", f"Installed Worker exited; inspect {log_path}"
                try:
                    return (await http.get(origin + "/readyz")).status_code == 200
                except httpx2.TransportError:
                    return False

            await journey.live.wait(ready, bool, "Installed plugin Worker readiness")
        logger.info("Production image Worker ready container=%s keys=%s", container.short_id, plugin_keys)
        yield effects
    finally:
        if container is not None:
            try:
                await asyncio.to_thread(container.stop, timeout=10)
                log_path.write_bytes(await asyncio.to_thread(container.logs))
            finally:
                await asyncio.to_thread(container.remove, force=True)
        await asyncio.to_thread(client.close)


def events(root):
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
