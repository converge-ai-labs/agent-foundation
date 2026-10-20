"""Control-owned, bounded proxy collection and transient cross-replica cache reads.

Redis coordinates temporary owners and routes queries; samples remain exclusively in
process memory. Losing coordination discards observations, never grants execution authority.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import uuid4

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness.providers.environment.remote_envd.configuration import RemoteEnvdConnectionConfiguration
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.infra.errors import ServiceError, rate_limited
from a13n_service.providers.environment.metrics import observe
from a13n_service.providers.metrics_catalog import MetricQuery
from a13n_service.runs.environments.monitoring import collection
from a13n_service.runs.environments.monitoring.collection import ReverseTarget, Selection
from a13n_service.runs.environments.monitoring.export import Exporter, ExportHealth, Projection
from a13n_service.runs.environments.monitoring.history import Cache, History, Observation

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('PEXPIRE', KEYS[1], ARGV[2])
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


def lease_key(target_id: str) -> str:
    return f"a13n:metrics:owner:{target_id}"


def channel(node: str) -> str:
    return f"a13n:metrics:rpc:{node}"


class LocalResult(BaseModel):
    source: Literal["temporary"] = "temporary"
    observation: Observation
    history: History
    export: ExportHealth | None = None


Identity = Annotated[str, Field(min_length=1, max_length=72)]
Nonce = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class Request(MetricQuery):
    id: Nonce
    sender: Nonce
    target_id: Identity
    revision: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Nonce
    result: LocalResult | None
    error: Literal["payload_too_large"] | None = None


@dataclass(slots=True)
class Entry:
    selection: Selection
    lease: str
    cache: Cache
    projection: Projection
    last_export: float = 0
    authority_failed_at: float | None = None
    export_health: ExportHealth = field(default_factory=ExportHealth)
    credential_cache: ManagedCredentialCache = field(default_factory=ManagedCredentialCache, repr=False)


class Proxy:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.config = runtime.settings.monitoring
        self.node = uuid4().hex
        self.entries: dict[str, Entry] = {}
        self.reverse: dict[str, WebSocketEnvdConnections] = {}
        self.closed = False
        self.pending: dict[str, asyncio.Future[LocalResult | None]] = {}
        self.ready = asyncio.Event()
        self.poll_slots = anyio.CapacityLimiter(8)
        self.query_slots = anyio.CapacityLimiter(self.config.query_concurrency)
        self.reply_slots = anyio.CapacityLimiter(self.config.query_concurrency)
        self.exporter = Exporter(runtime.endpoint_policy, timeout=self.config.request_seconds)
        self.cursor = ""
        self.lease_ms = int((self.config.poll_seconds + self.config.request_seconds) * 3000)

    async def resolve(self, target_id: str, entry: Entry | None) -> Selection | None:
        """Retain owned history briefly on DB failure, without using stale authority."""
        try:
            selection = await collection.resolve(self.runtime, target_id)
        except SQLAlchemyError:
            if entry is not None and self.entries.get(target_id) is entry:
                entry.cache.status = "offline"
                entry.cache.invalidate_rates()
                entry.credential_cache.clear()
                now = time.monotonic()
                if entry.authority_failed_at is None:
                    entry.authority_failed_at = now
                if (
                    now - entry.authority_failed_at >= 3 * self.config.poll_seconds
                    or await self.runtime.redis.get(lease_key(target_id)) != entry.lease
                ):
                    await self.drop(target_id, expected=entry)
            raise
        if entry is not None:
            entry.authority_failed_at = None
        return selection

    async def current(self, entry: Entry) -> bool:
        """Fence an observation/export with both current configuration and ephemeral ownership."""
        if self.entries.get(entry.selection.target_id) is not entry:
            return False
        selection = await self.resolve(entry.selection.target_id, entry)
        if selection is None or selection.revision != entry.selection.revision or selection.target is None:
            return False
        lease = await self.runtime.redis.get(lease_key(selection.target_id))
        return lease == entry.lease and self.entries.get(selection.target_id) is entry

    async def drop(self, target_id: str, *, expected: Entry | None) -> None:
        # Compare and remove before awaiting: old work cannot retire a replacement attachment.
        if self.entries.get(target_id) is not expected:
            return
        entry = self.entries.pop(target_id, None)
        reverse = self.reverse.pop(target_id, None)
        try:
            if entry is not None:
                await self.runtime.redis.eval(_RELEASE, 1, lease_key(target_id), entry.lease)
        finally:
            if reverse is not None:
                await reverse.close()

    @asynccontextmanager
    async def reverse_connection(self, selection: Selection) -> AsyncIterator[WebSocketEnvdConnections]:
        """Reserve the same bounded collection lease before upgrading a reverse carrier."""
        if self.closed or not self.config.enabled:
            raise ServiceError("unavailable", "Proxy monitoring is unavailable")
        target_id = selection.target_id
        if target_id in self.entries or len(self.entries) >= self.config.max_devices:
            raise ServiceError("conflict", "Device is connected or monitoring capacity is exhausted")
        lease = self.node + ":" + uuid4().hex
        if not await self.runtime.redis.set(lease_key(target_id), lease, nx=True, px=self.lease_ms):
            raise ServiceError("conflict", "Device already has a connection owner")
        if self.closed or len(self.entries) >= self.config.max_devices:
            await self.runtime.redis.eval(_RELEASE, 1, lease_key(target_id), lease)
            raise ServiceError("unavailable", "Monitoring capacity is exhausted")
        connection = WebSocketEnvdConnections(
            configuration=RemoteEnvdConnectionConfiguration(
                initialization_timeout=self.config.request_seconds,
                request_timeout=self.config.request_seconds,
                max_in_flight=8,
            ),
            max_connections=1,
        )
        entry = Entry(
            selection,
            lease,
            Cache(selection.device_id),
            Projection(selection.workspace_id, target_id, selection.device_id, "device_registration_id"),
        )
        self.entries[target_id], self.reverse[target_id] = entry, connection
        try:
            if not await self.current(entry):
                raise ServiceError("unauthenticated", "Device registration changed during admission")
            yield connection
        finally:
            with anyio.move_on_after(self.config.request_seconds, shield=True):
                if self.reverse.get(target_id) is connection:
                    await self.drop(target_id, expected=self.entries.get(target_id))
                else:
                    await connection.close()

    async def poll(self, target_id: str) -> None:
        async with self.poll_slots:
            entry = self.entries.get(target_id)
            reverse = self.reverse.get(target_id)
            try:
                selection = await self.resolve(target_id, entry)
                if self.entries.get(target_id) is not entry:
                    return
                if selection is None or selection.target is None:
                    await self.drop(target_id, expected=entry)
                    return
                if isinstance(selection.target, ReverseTarget):
                    if entry is None or reverse is None:
                        return
                    previous = entry.selection.target
                    if not isinstance(previous, ReverseTarget) or previous.token_hash != selection.target.token_hash:
                        await self.drop(target_id, expected=entry)
                        return
                if entry is None:
                    if len(self.entries) >= self.config.max_devices:
                        return
                    lease = self.node + ":" + uuid4().hex
                    if not await self.runtime.redis.set(lease_key(target_id), lease, nx=True, px=self.lease_ms):
                        return
                    # No awaits between this second capacity check and reserving memory.
                    if len(self.entries) >= self.config.max_devices:
                        await self.runtime.redis.eval(_RELEASE, 1, lease_key(target_id), lease)
                        return
                else:
                    lease = entry.lease
                    if not await self.runtime.redis.eval(_RENEW, 1, lease_key(target_id), lease, self.lease_ms):
                        await self.drop(target_id, expected=entry)
                        return
                    if self.entries.get(target_id) is not entry:
                        return
                if entry is None or entry.selection.revision != selection.revision:
                    entry = Entry(
                        selection,
                        lease,
                        Cache(selection.device_id),
                        Projection(
                            selection.workspace_id,
                            target_id,
                            selection.device_id,
                            "device_registration_id"
                            if isinstance(selection.target, ReverseTarget)
                            else "environment_id",
                        ),
                    )
                    self.entries[target_id] = entry
                entry.cache.status = selection.status
                try:
                    with anyio.fail_after(self.config.request_seconds):
                        if isinstance(selection.target, ReverseTarget):
                            assert reverse is not None
                            observation = await observe(reverse, selection.device_id)
                        else:
                            observation = await collection.read(
                                self.runtime, selection, credential_cache=entry.credential_cache
                            )
                    if not await self.current(entry):
                        await self.drop(target_id, expected=entry)
                        return
                    entry.cache.system_info = observation.descriptor.system_info
                    if observation.metrics is None:
                        entry.cache.status = "unsupported"
                        entry.cache.invalidate_rates()
                        return
                    now, monotonic = time.time(), time.monotonic()
                    fresh = entry.cache.accept(
                        observation.metrics, now=now, monotonic=monotonic, poll_seconds=self.config.poll_seconds
                    )
                    if (
                        fresh
                        and selection.export is not None
                        and monotonic - entry.last_export >= selection.export_interval
                    ):
                        data = entry.projection.encode(
                            observation.metrics, observed_at=entry.cache.last_sample_at or now
                        )
                        self.exporter.submit(data, selection.export, partial(self.current, entry), entry.export_health)
                        entry.last_export = monotonic
                except EnvironmentProviderError as error:
                    entry.cache.status = (
                        "unsupported" if error.category == EnvironmentProviderErrorCategory.UNSUPPORTED else "offline"
                    )
                except (TimeoutError, NotImplementedError):
                    entry.cache.status = "offline"
                except Exception as error:
                    entry.cache.status = "offline"
                    logger.warning(
                        "Environment observation failed", extra={"exception_details": exception_details(error)}
                    )
                finally:
                    if entry.cache.status in {"offline", "unsupported"}:
                        entry.credential_cache.clear()
                        entry.cache.invalidate_rates()
            except SQLAlchemyError as error:
                logger.warning(
                    "Environment monitoring authority unavailable",
                    extra={"environment_id": target_id, "exception_details": exception_details(error)},
                )
            except Exception as error:
                # Invalid configuration or ownership uncertainty retires only this target.
                logger.warning(
                    "Environment monitoring target failed",
                    extra={"environment_id": target_id, "exception_details": exception_details(error)},
                )
                # A cache retained after ownership uncertainty must never answer or export.
                with anyio.move_on_after(self.config.request_seconds, shield=True):
                    try:
                        await self.drop(target_id, expected=entry)
                    except RedisError:
                        pass

    async def tick(self) -> None:
        # Existing entries retain admission; the cursor advances through candidates not owned here.
        ids = list(self.entries)
        if len(ids) < self.config.max_devices:
            try:
                found = await collection.candidates(self.runtime, after=self.cursor, limit=self.config.max_devices)
            except SQLAlchemyError as error:
                logger.warning(
                    "Environment monitoring discovery unavailable",
                    extra={"exception_details": exception_details(error)},
                )
            else:
                self.cursor = found[-1] if len(found) == self.config.max_devices else ""
                ids = list(dict.fromkeys([*ids, *found]))
        async with asyncio.TaskGroup() as group:
            for target_id in ids:
                group.create_task(self.poll(target_id))

    async def renew(self) -> None:
        """Keep admitted ownership alive independently of slow Device polling batches."""
        while True:
            await asyncio.sleep(self.config.poll_seconds)
            entries = list(self.entries.items())
            if not entries:
                continue
            try:
                async with self.runtime.redis.pipeline(transaction=False) as pipeline:
                    for target_id, entry in entries:
                        pipeline.eval(_RENEW, 1, lease_key(target_id), entry.lease, self.lease_ms)
                    renewed = await pipeline.execute()
                for (target_id, entry), retained in zip(entries, renewed, strict=True):
                    if self.entries.get(target_id) is not entry:
                        continue
                    if (
                        entry.authority_failed_at is not None
                        and time.monotonic() - entry.authority_failed_at >= 3 * self.config.poll_seconds
                    ):
                        await self.drop(target_id, expected=entry)
                        continue
                    if retained and isinstance(entry.selection.target, ReverseTarget):
                        try:
                            selection = await self.resolve(target_id, entry)
                            retained = (
                                selection is not None
                                and isinstance(selection.target, ReverseTarget)
                                and selection.target.token_hash == entry.selection.target.token_hash
                            )
                        except SQLAlchemyError:
                            # resolve() checks ownership and expires the bounded DB grace.
                            retained = self.entries.get(target_id) is entry
                        except RedisError:
                            raise
                        except Exception:
                            retained = False
                    if not retained:
                        await self.drop(target_id, expected=entry)
            except RedisError:
                for target_id, entry in entries:
                    if self.entries.get(target_id) is entry:
                        try:
                            await self.drop(target_id, expected=entry)
                        except RedisError:
                            pass

    async def collect(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:
                logger.warning(
                    "Environment monitoring pass failed", extra={"exception_details": exception_details(error)}
                )
            await asyncio.sleep(self.config.poll_seconds)

    async def local(self, target_id: str, revision: str, query: MetricQuery) -> LocalResult | None:
        entry = self.entries.get(target_id)
        if entry is None or entry.selection.revision != revision:
            return None
        # Non-ready targets can retain temporary history while still owned; only exports require ready.
        selection = await self.resolve(target_id, entry)
        if selection is None or selection.revision != revision:
            return None
        if await self.runtime.redis.get(lease_key(target_id)) != entry.lease:
            return None
        if self.entries.get(target_id) is not entry:
            return None
        return LocalResult(
            observation=entry.cache.observation(monotonic=time.monotonic(), include_snapshot=query.include_snapshot),
            history=entry.cache.read(query, now=time.time()),
            export=None if selection.export is None else entry.export_health.model_copy(),
        )

    async def query(self, selection: Selection, query: MetricQuery) -> LocalResult:
        if not self.config.enabled:
            raise ServiceError("unavailable", "Proxy monitoring is disabled")
        try:
            self.query_slots.acquire_nowait()
        except anyio.WouldBlock:
            raise rate_limited("Temporary metrics query capacity is exhausted", 1) from None
        try:
            with anyio.fail_after(self.config.query_seconds):
                owner = await self.runtime.redis.get(lease_key(selection.target_id))
                if owner is None or selection.target is None:
                    # Ineligible targets report their state without reserving a cache or lease.
                    cache = Cache(selection.device_id)
                    cache.status = (
                        selection.status
                        if selection.target is None
                        else ("capacity" if len(self.entries) >= self.config.max_devices else "waiting")
                    )
                    return LocalResult(
                        observation=cache.observation(
                            monotonic=time.monotonic(), include_snapshot=query.include_snapshot
                        ),
                        history=cache.read(query, now=time.time()),
                    )
                assert isinstance(owner, str), "the process Redis client decodes responses"
                node = owner.split(":", 1)[0]
                if node == self.node:
                    result = await self.local(selection.target_id, selection.revision, query)
                else:
                    await self.ready.wait()
                    request = Request(
                        **query.model_dump(include={"start", "end", "step", "metric", "include_snapshot"}),
                        id=uuid4().hex,
                        sender=self.node,
                        target_id=selection.target_id,
                        revision=selection.revision,
                    )
                    future = asyncio.get_running_loop().create_future()
                    self.pending[request.id] = future
                    try:
                        await self.runtime.redis.publish(channel(node), request.model_dump_json())
                        result = await future
                    finally:
                        self.pending.pop(request.id, None)
                if result is None:
                    raise ServiceError(
                        "unavailable", "Temporary metrics ownership or selection changed; retry the query"
                    )
                return result
        except (RedisError, TimeoutError):
            raise ServiceError("unavailable", "Temporary metrics owner is unavailable") from None
        finally:
            self.query_slots.release()

    async def reply(self, request: Request) -> None:
        try:
            result = await self.local(request.target_id, request.revision, request)
            response = Response(id=request.id, result=result).model_dump_json(exclude={"error"})
            if len(response.encode()) > self.config.query_bytes:
                response = Response(id=request.id, result=None, error="payload_too_large").model_dump_json()
            await self.runtime.redis.publish(channel(request.sender), response)
        except Exception as error:
            logger.warning("Temporary metrics reply failed", extra={"exception_details": exception_details(error)})
        finally:
            self.reply_slots.release_on_behalf_of(request.id)

    async def route(self) -> None:
        while True:
            try:
                async with self.runtime.redis.pubsub() as subscription, asyncio.TaskGroup() as replies:
                    await subscription.subscribe(channel(self.node))
                    self.ready.set()
                    while True:
                        message = await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
                        if message is None:
                            continue
                        raw = message["data"]
                        if not isinstance(raw, str) or len(raw.encode()) > self.config.query_bytes:
                            continue
                        try:
                            data = json.loads(raw)
                            if "sender" in data:
                                request = Request.model_validate(data)
                                try:
                                    self.reply_slots.acquire_on_behalf_of_nowait(request.id)
                                except (anyio.WouldBlock, RuntimeError):
                                    continue
                                replies.create_task(self.reply(request))
                            else:
                                response = Response.model_validate(data)
                                future = self.pending.get(response.id)
                                if future is not None and not future.done():
                                    if response.error == "payload_too_large":
                                        future.set_exception(
                                            ServiceError(
                                                "payload_too_large",
                                                "Temporary metrics reply exceeds the transport limit; reduce the range or omit the snapshot",
                                            )
                                        )
                                    else:
                                        future.set_result(response.result)
                        except ValueError:
                            continue
            except (RedisError, ExceptionGroup) as error:
                self.ready.clear()
                logger.warning(
                    "Temporary metrics routing interrupted", extra={"exception_details": exception_details(error)}
                )
                await asyncio.sleep(1)

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.route())
                group.create_task(self.collect())
                group.create_task(self.renew())
                group.create_task(self.exporter.run())
        finally:
            self.closed = True
            self.ready.clear()
            await self.exporter.close()
            # Lease expiry is sufficient if shutdown cannot reach Redis.
            with anyio.move_on_after(self.runtime.settings.redis.timeout, shield=True):
                for target_id, entry in list(self.entries.items()):
                    try:
                        await self.drop(target_id, expected=entry)
                    except RedisError:
                        pass
            self.entries.clear()
            await asyncio.gather(*(connection.close() for connection in self.reverse.values()), return_exceptions=True)
            self.reverse.clear()
