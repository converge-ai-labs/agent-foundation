"""Short-lived Control-to-Worker requests for native Docker image checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Literal

from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_harness.providers.environment.docker.image_test import DockerImageTestFailure, test_docker_image
from a13n_harness.providers.environment.docker.provider import DockerConnectionConfiguration
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .models import EnvironmentProviderRecord

_QUEUE = "a13n:environment:image-tests"
_REQUEST_SECONDS = 130
_PROBE_INTERVAL = 5
_CONNECTIVITY_TTL = 15
logger = get_logger(__name__)


def _now() -> float:
    return time.time()


class ProviderConnectivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["connected", "unavailable", "unknown"]
    error: str | None = None


class ImageTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configuration_hash: str | None = None
    image_id: str | None = None
    image_source: Literal["local", "pulled"] | None = None
    checks: tuple[str, ...] = ()
    error: str | None = None


class ImageTestIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    provider_id: str
    organization_id: str
    workspace_id: str | None
    principal_type: str
    principal_id: str

    @property
    def key(self) -> str:
        owner = (
            self.provider_id,
            self.organization_id,
            self.workspace_id,
            self.principal_type,
            self.principal_id,
            self.request_id,
        )
        return hashlib.sha256(json.dumps(owner, separators=(",", ":")).encode()).hexdigest()


class ImageTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ImageTestIdentity
    configuration: DockerEnvironmentConfiguration
    expires_at: float = Field(default_factory=lambda: _now() + _REQUEST_SECONDS)


def connectivity_key(provider_id: str) -> str:
    return f"a13n:environment:provider:{provider_id}:connectivity"


def _result_key(identity: ImageTestIdentity) -> str:
    return f"a13n:environment:image-test:{identity.key}:result"


def _valid_key(identity: ImageTestIdentity) -> str:
    return f"a13n:environment:image-test:{identity.key}:valid"


def _cancel_key(identity: ImageTestIdentity) -> str:
    return f"a13n:environment:image-test:{identity.key}:cancel"


async def cancel_image_test(redis: Redis, identity: ImageTestIdentity) -> None:
    await redis.set(_cancel_key(identity), "1", ex=180)


async def request_image_test(redis: Redis, request: ImageTestRequest) -> ImageTestResponse:
    if not await redis.set(_valid_key(request.identity), "1", ex=_REQUEST_SECONDS + 10, nx=True):
        raise ValueError("Image test request ID is already in use")
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(_QUEUE, "-inf", _now())
            pipe.zadd(_QUEUE, {request.model_dump_json(): request.expires_at})
            pipe.expire(_QUEUE, _REQUEST_SECONDS + 50)
            await pipe.execute()
        async with asyncio.timeout(_REQUEST_SECONDS):
            while True:
                if await redis.exists(_cancel_key(request.identity)):
                    return ImageTestResponse(error="Image test canceled")
                response = await redis.getdel(_result_key(request.identity))
                if isinstance(response, (bytes, str)):
                    return ImageTestResponse.model_validate_json(response)
                await asyncio.sleep(0.2)
    finally:

        async def delete_keys() -> None:
            await redis.delete(_valid_key(request.identity), _result_key(request.identity))

        cleanup = asyncio.create_task(delete_keys())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise


class DockerImageTestWorker:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], redis: Redis) -> None:
        self.sessions = sessions
        self.redis = redis
        self.draining = False
        self.current: asyncio.Task | None = None

    async def run(self) -> None:
        while not self.draining:
            entry = await self.redis.zpopmin(_QUEUE)
            if not entry:
                await asyncio.sleep(0.2)
                continue
            try:
                raw = entry[0][0]
                if not isinstance(raw, (bytes, str)):
                    raise ValueError("Invalid queued image test")
                request = ImageTestRequest.model_validate_json(raw)
            except Exception:
                logger.warning("docker_image_test_invalid_queue_entry")
                continue
            if not await self._valid(request):
                continue
            self.current = asyncio.create_task(self._execute(request))
            try:
                await self.current
            finally:
                self.current = None

    async def shutdown(self) -> None:
        self.draining = True
        if self.current is not None:
            self.current.cancel()
            await asyncio.gather(self.current, return_exceptions=True)

    async def _valid(self, request: ImageTestRequest) -> bool:
        return (
            request.expires_at > _now()
            and not await self.redis.exists(_cancel_key(request.identity))
            and bool(await self.redis.exists(_valid_key(request.identity)))
        )

    async def _execute(self, request: ImageTestRequest) -> None:
        if not await self._valid(request):
            return
        configuration_hash: str | None = None
        try:
            async with short_session(self.sessions) as session:
                provider = await session.get(EnvironmentProviderRecord, request.identity.provider_id)
                if provider is None or provider.type != "docker" or not provider.enabled:
                    raise ValueError("Docker Provider is unavailable or disabled")
                backend = DockerConnectionConfiguration.model_validate(provider.configuration)
            configuration = request.configuration
            configuration_hash = hashlib.sha256(
                json.dumps(configuration.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
            if not await self._valid(request):
                return
            engine = await asyncio.to_thread(DockerSDKEngine.connect, backend.docker_host)
            try:
                if not await self._valid(request):
                    return
                operation = asyncio.create_task(test_docker_image(engine, configuration))
                try:
                    while not operation.done():
                        if not await self._valid(request):
                            operation.cancel()
                            await asyncio.gather(operation, return_exceptions=True)
                            return
                        await asyncio.sleep(0.2)
                    result = await operation
                finally:
                    if not operation.done():
                        operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
            finally:
                await engine.close()
            response = ImageTestResponse(
                configuration_hash=configuration_hash,
                image_id=result.image_id,
                image_source=result.image_source,
                checks=result.checks,
            )
        except asyncio.CancelledError:
            raise
        except DockerImageTestFailure as error:
            response = ImageTestResponse(
                configuration_hash=configuration_hash,
                image_id=error.image_id,
                image_source=error.image_source,
                error=str(error)[:1024],
            )
        except Exception as error:
            logger.warning(
                "docker_image_test_failed", extra={"provider_id": request.identity.provider_id, "error": str(error)}
            )
            response = ImageTestResponse(configuration_hash=configuration_hash, error=str(error)[:1024])
        if await self._valid(request):
            await self.redis.set(_result_key(request.identity), response.model_dump_json(), ex=180)


class DockerConnectivityProbe:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], redis: Redis) -> None:
        self.sessions = sessions
        self.redis = redis
        self.draining = False

    async def run(self) -> None:
        while not self.draining:
            try:
                await self.probe_connectivity()
            except Exception:
                logger.exception("docker_connectivity_probe_failed")
            await asyncio.sleep(_PROBE_INTERVAL)

    async def shutdown(self) -> None:
        self.draining = True

    async def probe_connectivity(self) -> None:
        async with short_session(self.sessions) as session:
            providers = tuple(
                (row.id, DockerConnectionConfiguration.model_validate(row.configuration).docker_host)
                for row in await session.scalars(
                    select(EnvironmentProviderRecord).where(
                        EnvironmentProviderRecord.type == "docker", EnvironmentProviderRecord.enabled.is_(True)
                    )
                )
            )
        for provider_id, docker_host in providers:
            client = None
            try:
                client = await asyncio.to_thread(DockerSDKEngine.connect, docker_host, timeout_seconds=3)
                connected = await asyncio.to_thread(client.client.ping)
                if not connected:
                    raise RuntimeError("Docker Engine ping returned false")
                observation = ProviderConnectivity(status="connected")
            except Exception as error:
                observation = ProviderConnectivity(status="unavailable", error=str(error)[:256])
            finally:
                if client is not None:
                    await client.close()
            await self.redis.set(connectivity_key(provider_id), observation.model_dump_json(), ex=_CONNECTIVITY_TTL)
