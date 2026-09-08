"""Own every process and network fault used by the second-round HTTP journeys.

Each test gets new PostgreSQL/Redis containers and a unique S3 bucket. The S3
server must be a configured compatible loopback service; only this bucket is
created/deleted. Dependency cuts affect Worker connections, never server state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import signal
import socket
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import anyio
import httpx2
from a13n_service.ids import new_object_id
from a13n_service.settings import Settings
from a13n_service.storage.object_store import S3ObjectStore
from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session

from .client import LiveClient
from .config import STATE, local_origin
from .round_two_resources import provision
from .tcp_proxy import TCPProxy

REPOSITORY = Path(__file__).resolve().parents[2]


def identity():
    return {
        "organization_id": new_object_id("org"),
        "workspace_id": new_object_id("ws"),
        "user_id": new_object_id("usr"),
        "token": secrets.token_urlsafe(32),
    }


def private_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        json.dump(value, output, indent=2)


def free_origin():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"


class RoundTwoLab:
    def __init__(self, root: Path, config: dict, environment: dict):
        self.root, self.config, self.environment = root, config, environment
        self.worker_environment = dict(environment)
        self.proxies = {}
        self.processes = []
        self.workers = []
        self.origins = {}

    async def spawn(self, module, *arguments, environment=None):
        log_path = self.root / f"process-{len(self.processes)}.log"
        with log_path.open("ab") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                module,
                *arguments,
                cwd=self.root,
                env=environment or self.environment,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        self.processes.append(process)
        return process

    async def command(self, module, *arguments, environment=None):
        process = await self.spawn(module, *arguments, environment=environment)
        async with asyncio.timeout(120):
            assert await process.wait() == 0, f"Lab setup failed; inspect {self.root}"

    async def ready(self, process, origin, *, verify=True):
        async with httpx2.AsyncClient(timeout=2, trust_env=False, verify=verify) as client:
            deadline = monotonic() + 90
            while monotonic() < deadline:
                assert process.returncode is None, f"Lab process exited ({process.returncode}); inspect {self.root}"
                try:
                    response = await client.get(origin + "/readyz")
                    if response.status_code == 200:
                        return
                except httpx2.TransportError:
                    pass
                await anyio.sleep(0.1)
        raise AssertionError(f"Lab readiness timed out; inspect {self.root}")

    async def start_worker(self):
        origin = free_origin()
        environment = {**self.worker_environment, "LIVE_TEST_WORKER_URL": origin}
        process = await self.spawn("dev.live_tests.manage", "worker", environment=environment)
        self.workers.append(process)
        self.origins[process] = origin
        await self.ready(process, origin)
        return process

    def send(self, process, signum):
        if process not in self.processes:
            raise ValueError("Cannot signal a process not created by this lab")
        if process.returncode is None:
            # All descendants belong to the session created by spawn(). Never accept an external PID.
            os.killpg(process.pid, signum)

    async def stop(self, process, signum=signal.SIGTERM):
        self.send(process, signum)
        async with asyncio.timeout(45):
            return await process.wait()

    async def wait_unready(self, process):
        async with httpx2.AsyncClient(timeout=1, trust_env=False) as client:
            async with asyncio.timeout(10):
                while process.returncode is None:
                    try:
                        if (await client.get(self.origins[process] + "/readyz")).status_code != 200:
                            return
                    except httpx2.TransportError:
                        return
                    await anyio.sleep(0.05)

    async def close(self):
        for proxy in self.proxies.values():
            proxy.restore()
        for process in reversed(self.processes):
            if process.returncode is None:
                self.send(process, signal.SIGCONT)
                self.send(process, signal.SIGTERM)
        for process in reversed(self.processes):
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                self.send(process, signal.SIGKILL)
                await process.wait()

    async def attempts(self, run_id):
        return sorted(
            await self.client.collection(f"/api/v1/runs/{run_id}/attempts"), key=lambda item: item["attempt_number"]
        )

    async def wait_evidence(self, case, field, *, run_id=None):
        async def fetch():
            evidence = await self.client.evidence(case)
            if run_id and not evidence.get(field):
                run = await self.client.run(run_id)
                assert run["status"] in {"accepted", "running"}, f"Run ended before {field}: {run['failure']}"
            return evidence

        return await self.client.wait(fetch, lambda value: bool(value.get(field)), field)


@asynccontextmanager
async def open_lab(*, management=False):
    settings = Settings()
    assert settings.object_endpoint_url, "Round two requires a compatible loopback S3 endpoint in .env"
    endpoint = local_origin(settings.object_endpoint_url)
    root = STATE / "round-two" / uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    bucket = "a13n-live-" + uuid4().hex
    async with AsyncExitStack() as stack:
        s3 = await stack.enter_async_context(
            get_session().create_client(
                "s3",
                endpoint_url=endpoint,
                region_name=settings.object_region,
                config=AioConfig(
                    connect_timeout=3,
                    read_timeout=5,
                    proxies={},
                    retries={"total_max_attempts": 1},
                    s3={"addressing_style": "path"},
                    http_session_cls=HttpxSession,
                ),
            )
        )
        bucket_options = (
            {}
            if settings.object_region == "us-east-1"
            else {"CreateBucketConfiguration": {"LocationConstraint": settings.object_region}}
        )
        await s3.create_bucket(Bucket=bucket, **bucket_options)
        stack.push_async_callback(_delete_bucket, s3, bucket)
        await S3ObjectStore(s3, bucket).check_compatibility()

        from testcontainers.postgres import PostgresContainer
        from testcontainers.redis import RedisContainer

        postgres = PostgresContainer("postgres:17-alpine")
        redis = RedisContainer("redis:8-alpine")
        for container in (postgres, redis):
            stack.push_async_callback(anyio.to_thread.run_sync, container.stop)
            await anyio.to_thread.run_sync(container.start)
        database_url = (
            f"postgresql+psycopg://{postgres.username}:{postgres.password}@"
            f"{postgres.get_container_host_ip()}:{postgres.get_exposed_port(5432)}/{postgres.dbname}"
        )
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        config = {
            **identity(),
            "control_url": free_origin(),
            "worker_url": free_origin(),
            "workspace_root": str(root / "workspace"),
            "timeout_seconds": 120,
            "encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
            "other_identity": identity(),
        }
        from .fixture_peer import certificate_context, create_certificate

        if management:
            config.update(peer_url=free_origin().replace("http:", "https:"), **create_certificate(root))
        # Same Organization, a different Workspace and User; organization separation must not mask Workspace defects.
        config["other_identity"].update(organization_id=config["organization_id"], workspace_only=True)
        Path(config["workspace_root"]).mkdir(mode=0o700)
        config_path = root / "config.json"
        private_json(config_path, config)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("A13N_SERVICE_", "LIVE_TEST_", "PYTHONPATH", "OTEL_"))
            and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        environment.update(
            {
                "PYTHONPATH": str(REPOSITORY),
                "OTEL_TRACES_EXPORTER": "none",
                "LIVE_TEST_CONFIG": str(config_path),
                "A13N_SERVICE_DATABASE_URL": database_url,
                "A13N_SERVICE_REDIS_URL": redis_url,
                "A13N_SERVICE_OBJECT_BACKEND": "s3",
                "A13N_SERVICE_OBJECT_ENDPOINT_URL": endpoint,
                "A13N_SERVICE_OBJECT_BUCKET": bucket,
                "A13N_SERVICE_OBJECT_REGION": settings.object_region,
                "A13N_SERVICE_OBJECT_FORCE_PATH_STYLE": "true",
                "A13N_SERVICE_WORKER_CONCURRENCY": "1",
                "A13N_SERVICE_WORKER_LEASE_SECONDS": "12",
                "A13N_SERVICE_WORKER_DRAIN_SECONDS": "5",
                "A13N_SERVICE_WORKER_CLEANUP_SECONDS": "3",
                "A13N_SERVICE_WORKER_POLL_INTERVAL_SECONDS": "0.2",
                "A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS": '["127.0.0.1/32"]',
                "A13N_SERVICE_DATABASE_CONNECT_TIMEOUT_SECONDS": "2",
                "A13N_SERVICE_DATABASE_STATEMENT_TIMEOUT_SECONDS": "3",
                "A13N_SERVICE_REDIS_CONNECT_TIMEOUT_SECONDS": "1",
                "A13N_SERVICE_REDIS_COMMAND_TIMEOUT_SECONDS": "2",
                "A13N_SERVICE_OBJECT_CONNECT_TIMEOUT_SECONDS": "1",
                "A13N_SERVICE_OBJECT_READ_TIMEOUT_SECONDS": "3",
            }
        )
        if management:
            environment["SSL_CERT_FILE"] = config["peer_ca_bundle"]
        lab = RoundTwoLab(root, config, environment)
        for name, url, key in (
            ("postgres", database_url, "DATABASE_URL"),
            ("redis", redis_url, "REDIS_URL"),
            ("objects", endpoint, "OBJECT_ENDPOINT_URL"),
        ):
            parts = urlsplit(url)
            proxy = await stack.enter_async_context(TCPProxy(parts.hostname, parts.port or 80).listen())
            lab.proxies[name] = proxy
            credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
            replacement = urlunsplit(parts._replace(netloc=credentials + f"127.0.0.1:{proxy.local_port}"))
            lab.worker_environment[f"A13N_SERVICE_{key}"] = replacement
        stack.push_async_callback(lab.close)
        if management:
            peer = await lab.spawn("dev.live_tests.fixture_peer")
            await lab.ready(peer, config["peer_url"], verify=certificate_context(config))
        await lab.command("a13n_service", "db", "upgrade")
        await lab.command("dev.live_tests.manage", "init")
        other_path = root / "other.json"
        private_json(other_path, {**config, **config["other_identity"]})
        await lab.command(
            "dev.live_tests.manage", "init", environment={**environment, "LIVE_TEST_CONFIG": str(other_path)}
        )
        control = await lab.spawn("dev.live_tests.manage", "control")
        await lab.ready(control, config["control_url"])
        http = await stack.enter_async_context(
            httpx2.AsyncClient(
                base_url=config["control_url"],
                headers={"Authorization": "Bearer " + config["token"]},
                timeout=15,
                trust_env=False,
                follow_redirects=False,
            )
        )
        lab.client = LiveClient(config, http)
        await provision(lab.client)
        private_json(config_path, config)
        await lab.start_worker()
        try:
            yield lab
        finally:
            for proxy in lab.proxies.values():
                proxy.restore()
            await lab.client.cleanup()


async def _delete_bucket(client, bucket):
    # This bucket was allocated in open_lab(); no caller-provided bucket is ever deleted.
    while True:
        page = await client.list_objects_v2(Bucket=bucket, MaxKeys=1000)
        for item in page.get("Contents", []):
            await client.delete_object(Bucket=bucket, Key=item["Key"])
        if not page.get("IsTruncated"):
            break
    await client.delete_bucket(Bucket=bucket)
