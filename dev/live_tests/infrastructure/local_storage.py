"""Compatible loopback S3 storage with fixture-owned credentials and cleanup."""

import asyncio
import logging
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

import anyio
from a13n_service.storage.object_store import S3ObjectStore
from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError

from .config import local_origin

# This image passes the Service's conditional delete and concurrent create-only probes.
RUSTFS_IMAGE = "rustfs/rustfs@sha256:b7014e0ce2bc703c1316b3ef760e29dfae61fe4a50d1a66fa89638e0f8ea211f"
PORT_MAPPING_TIMEOUT_SECONDS = 10
RUSTFS_START_ATTEMPTS = 3
logger = logging.getLogger(__name__)


class PortMappingUnavailable(RuntimeError):
    """A started container has no usable loopback forward."""


async def wait_published_port(container):
    # Testcontainers' get_exposed_port waits 120 seconds on the same broken
    # mapping. Inspect only the public binding, off the event loop, instead.
    native = container.get_wrapped_container()
    bindings = None
    try:
        async with asyncio.timeout(PORT_MAPPING_TIMEOUT_SECONDS):
            while True:
                await anyio.to_thread.run_sync(native.reload)
                if native.status in {"exited", "dead"}:
                    raise RuntimeError(f"Owned RustFS container {native.id} exited before publishing its port")
                bindings = native.attrs.get("NetworkSettings", {}).get("Ports", {}).get("9000/tcp")
                for binding in bindings or ():
                    if binding.get("HostIp") == "127.0.0.1" and binding.get("HostPort"):
                        return int(binding["HostPort"])
                await anyio.sleep(0.1)
    except TimeoutError as error:
        raise PortMappingUnavailable(
            f"container={native.id} status={native.status} port_bindings={bindings}"
        ) from error


async def start_rustfs(stack, access, secret):
    from testcontainers.core.container import DockerContainer

    for attempt in range(1, RUSTFS_START_ATTEMPTS + 1):
        async with AsyncExitStack() as startup:
            container = (
                DockerContainer(RUSTFS_IMAGE)
                .with_env("RUSTFS_ACCESS_KEY", access)
                .with_env("RUSTFS_SECRET_KEY", secret)
                .with_env("RUSTFS_CONSOLE_ENABLE", "false")
                .with_command("/data")
                .with_bind_ports(9000, ("127.0.0.1", 0))
            )
            startup.push_async_callback(anyio.to_thread.run_sync, container.stop)
            await anyio.to_thread.run_sync(container.start)
            try:
                port = await wait_published_port(container)
            except PortMappingUnavailable as error:
                logger.warning(
                    "rustfs_port_mapping_unavailable attempt=%s/%s %s", attempt, RUSTFS_START_ATTEMPTS, error
                )
                if attempt == RUSTFS_START_ATTEMPTS:
                    raise RuntimeError(
                        "Owned RustFS could not publish its port after bounded startup retries"
                    ) from error
                # Exit this scope to remove the failed owned container before retrying.
                continue
            stack.push_async_callback(startup.pop_all().aclose)
            logger.info("rustfs_port_published attempt=%s port=%s", attempt, port)
            return f"http://127.0.0.1:{port}"


async def wait_ready(client):
    try:
        async with asyncio.timeout(90):
            while True:
                try:
                    # RustFS /health can succeed before the authenticated S3 API is ready.
                    await client.list_buckets()
                    return
                except (BotoCoreError, ClientError):
                    await anyio.sleep(0.5)
    except TimeoutError as error:
        raise RuntimeError("Owned RustFS did not become ready within 90 seconds") from error


@asynccontextmanager
async def open_object_storage(*, endpoint_url=None, region="us-east-1", credentials=None):
    async with AsyncExitStack() as stack:
        credentials = dict(credentials or {})
        environment = (
            {
                "AWS_ACCESS_KEY_ID": credentials["aws_access_key_id"],
                "AWS_SECRET_ACCESS_KEY": credentials["aws_secret_access_key"],
                "AWS_SESSION_TOKEN": credentials.get("aws_session_token", ""),
            }
            if credentials
            else {}
        )
        if endpoint_url:
            endpoint = local_origin(endpoint_url)
        else:
            access, secret = uuid4().hex.upper(), secrets.token_urlsafe(32)
            print("Starting owned RustFS; Docker downloads the pinned image on first use.", flush=True)
            endpoint = await start_rustfs(stack, access, secret)
            region = "us-east-1"
            credentials = {"aws_access_key_id": access, "aws_secret_access_key": secret, "aws_session_token": ""}
            environment = {"AWS_ACCESS_KEY_ID": access, "AWS_SECRET_ACCESS_KEY": secret}
        s3 = await stack.enter_async_context(
            get_session().create_client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                **credentials,
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
        if credentials:
            await wait_ready(s3)
        bucket = "a13n-live-" + uuid4().hex
        options = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
        await s3.create_bucket(Bucket=bucket, **options)
        if endpoint_url:
            stack.push_async_callback(delete_bucket, s3, bucket)
        # Owned RustFS data is removed with its container and anonymous volumes.
        # Listing/deleting every object first is redundant and can time out after large workloads.
        await S3ObjectStore(s3, bucket).check_compatibility()
        print("S3 readiness and storage compatibility checks passed.", flush=True)
        yield {
            **environment,
            "A13N_SERVICE_OBJECT_BACKEND": "s3",
            "A13N_SERVICE_OBJECT_ENDPOINT_URL": endpoint,
            "A13N_SERVICE_OBJECT_BUCKET": bucket,
            "A13N_SERVICE_OBJECT_REGION": region,
            "A13N_SERVICE_OBJECT_FORCE_PATH_STYLE": "true",
        }


async def delete_bucket(client, bucket):
    # The caller allocated this random bucket; never accept a developer's configured bucket.
    while True:
        page = await client.list_objects_v2(Bucket=bucket, MaxKeys=1000)
        for item in page.get("Contents", []):
            await client.delete_object(Bucket=bucket, Key=item["Key"])
        if not page.get("IsTruncated"):
            break
    await client.delete_bucket(Bucket=bucket)
