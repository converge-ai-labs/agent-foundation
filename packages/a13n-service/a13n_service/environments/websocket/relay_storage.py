"""Redis Stream transport and completion evidence, scoped to process incarnations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import asdict
from importlib.resources import files
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.storage.redis import redis_memory_identity

from ..domain import DomainModel
from .authority import ConnectionIdentity
from .coordination import KEY_PREFIX
from .relay_protocol import (
    CONTROL_OPERATIONS,
    DEFAULT_RELAY_LIMITS,
    RELAY_FRAME,
    RelayChunk,
    RelayFrame,
    RelayLimits,
    RelayRequest,
    RelayTerminal,
    canonical_message,
)

_SCRIPT = files(__package__).joinpath("relay.lua").read_text()
_ROWS = TypeAdapter(list[tuple[str, list[tuple[str, dict[str, str]]]]])
_UNUSED_KEY = f"{KEY_PREFIX}:unused"


class RelayStoreError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Client Environment operation transport could not complete")


class RelayEvidence(DomainModel):
    code: Literal["ok"] = "ok"
    phase: Literal["queued", "inflight", "started", "completed"] | None = None
    entry_id: str | None = None
    response_json: str | None = Field(default=None, repr=False)

    def terminal(self) -> RelayTerminal | None:
        return None if self.response_json is None else RelayTerminal.model_validate_json(self.response_json)


def _key(kind: str, identity: str) -> str:
    return f"{KEY_PREFIX}:{kind}:{hashlib.sha256(identity.encode()).hexdigest()}"


def response_key(worker_instance_id: str) -> str:
    return _key("responses", worker_instance_id)


class _RelayScript:
    def __init__(self, redis: Redis, limits: RelayLimits) -> None:
        self.redis = redis
        self.limits = limits
        self._script = redis.register_script(_SCRIPT)
        self._server_id = redis_memory_identity(redis)

    async def call(self, keys: tuple[str, str, str, str], **payload: object) -> RelayEvidence:
        request = {"limits": asdict(self.limits), "memory_server_id": self._server_id, **payload}
        try:
            async with asyncio.timeout(1):
                raw = await self._script(keys=keys, args=[json.dumps(request, separators=(",", ":"), allow_nan=False)])
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get("code"), str):
                raise ValueError("Relay script returned an invalid reply")
            if result["code"] != "ok":
                raise RelayStoreError(result["code"])
            return RelayEvidence.model_validate(result)
        except (RedisError, ValueError, TypeError, TimeoutError) as error:
            raise RelayStoreError("relay_unavailable") from error

    async def read(
        self, key: str, group: str, consumer: str, *, pending: bool, count: int, after_id: str = "0-0"
    ) -> tuple[tuple[str, dict[str, str]], ...]:
        if not 1 <= count <= 128:
            raise ValueError("Relay reads must contain 1 to 128 entries")
        if not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,20}", after_id) or (not pending and after_id != "0-0"):
            raise ValueError("Relay pending cursor must be a bounded Stream entry ID")
        try:
            async with asyncio.timeout(1):
                raw = await self.redis.xreadgroup(
                    group, consumer, {key: after_id if pending else ">"}, count=count, block=None if pending else 100
                )
            streams = _ROWS.validate_python(raw or [])
            if any(stream != key for stream, _ in streams):
                raise ValueError("Relay read returned a foreign Stream")
            return tuple(row for _, rows in streams for row in rows)
        except (RedisError, ValueError, TypeError, TimeoutError) as error:
            raise RelayStoreError("relay_unavailable") from error


class ConnectionRelayStore:
    """One request consumer scope; reconnect never reuses its Stream or ledger."""

    def __init__(
        self, redis: Redis, connection: ConnectionIdentity, *, limits: RelayLimits = DEFAULT_RELAY_LIMITS
    ) -> None:
        self.connection = connection
        self._storage = _RelayScript(redis, limits)
        self._scope = json.dumps(asdict(connection), sort_keys=True, separators=(",", ":"))
        self.requests_key = _key("requests", self._scope)
        self.ledger_key = _key("ledger", self._scope)
        self.expiries_key = _key("expiries", self._scope)

    async def prepare(self) -> None:
        await self._call("prepare_connection")

    async def touch(self) -> None:
        await self._call("touch_connection")

    async def prune(self) -> None:
        await self._call("prune")

    async def append(self, request: RelayRequest) -> RelayEvidence:
        return await self._call("append", request)

    async def start(self, request: RelayRequest, entry_id: str) -> RelayEvidence:
        """Only ``phase=started`` permits the caller's first possible EIP write."""
        return await self._call("start", request, entry_id=entry_id)

    async def complete(self, request: RelayRequest, entry_id: str, terminal: RelayTerminal) -> RelayEvidence:
        return await self._call("complete", request, entry_id=entry_id, frame=terminal)

    async def chunk(self, request: RelayRequest, entry_id: str, frame: RelayChunk) -> None:
        try:
            data = base64.b64decode(frame.data, validate=True)
        except ValueError as error:
            raise RelayStoreError("response_invalid") from error
        if len(data) > self._storage.limits.chunk_bytes:
            raise RelayStoreError("response_invalid")
        await self._call("chunk", request, entry_id=entry_id, frame=frame)

    async def read(
        self, *, pending: bool = False, count: int = 16, after_id: str = "0-0"
    ) -> tuple[tuple[str, RelayRequest], ...]:
        rows = await self._storage.read(
            self.requests_key,
            "owner",
            self.connection.owner_instance_id,
            pending=pending,
            count=count,
            after_id=after_id,
        )
        result: list[tuple[str, RelayRequest]] = []
        try:
            for entry, fields in rows:
                raw = fields["request"]
                if len(raw.encode()) > self._storage.limits.request_bytes:
                    raise ValueError("Relay request exceeds its byte budget")
                request = RelayRequest.model_validate_json(raw)
                if fields["request_id"] != request.request_id or self._encode(request) != raw:
                    raise ValueError("Relay request is not canonical or scoped to this connection")
                result.append((entry, request))
        except (KeyError, ValueError, TypeError) as error:
            raise RelayStoreError("request_invalid") from error
        return tuple(result)

    def _encode(self, request: RelayRequest) -> str:
        if request.use.connection != self.connection:
            raise RelayStoreError("scope_lost")
        limit = (
            self._storage.limits.control_bytes
            if request.operation in CONTROL_OPERATIONS
            else self._storage.limits.request_bytes
        )
        return canonical_message(request, max_bytes=limit)

    async def _call(
        self,
        operation: str,
        request: RelayRequest | None = None,
        *,
        entry_id: str | None = None,
        frame: RelayFrame | None = None,
    ) -> RelayEvidence:
        reply_key = _UNUSED_KEY
        payload: dict[str, object] = {
            "operation": operation,
            "scope": self._scope,
            "owner": self.connection.owner_instance_id,
        }
        if request is not None:
            reply_key = response_key(request.use.worker_instance_id)
            payload.update(
                request_id=request.request_id, request_json=self._encode(request), deadline_ms=request.deadline_ms
            )
            payload["is_control"] = request.operation in CONTROL_OPERATIONS
        if entry_id is not None:
            payload["entry_id"] = entry_id
        if frame is not None:
            if request is None or frame.request_id != request.request_id or frame.use != request.use:
                raise RelayStoreError("response_invalid")
            limit = (
                self._storage.limits.control_bytes
                if request.operation in CONTROL_OPERATIONS
                else self._storage.limits.response_bytes
            )
            payload["response_json"] = canonical_message(frame, max_bytes=limit)
            payload["transfer"] = None if frame.transfer is None else frame.transfer.model_dump()
            if isinstance(frame, RelayChunk):
                payload["chunk_bytes"] = len(base64.b64decode(frame.data, validate=True))
            else:
                payload["not_dispatched"] = frame.error is not None and frame.error.certainty == "not_dispatched"
                payload["failed"] = frame.error is not None
        return await self._storage.call((self.requests_key, self.ledger_key, self.expiries_key, reply_key), **payload)


class WorkerResponseMailbox:
    """A Worker incarnation's shared reader, independent of individual operations."""

    def __init__(self, redis: Redis, worker_instance_id: str, *, limits: RelayLimits = DEFAULT_RELAY_LIMITS) -> None:
        self.worker_instance_id = worker_instance_id
        self.key = response_key(worker_instance_id)
        self._storage = _RelayScript(redis, limits)

    async def prepare(self) -> None:
        await self._storage.call((_UNUSED_KEY, _UNUSED_KEY, _UNUSED_KEY, self.key), operation="prepare_worker")

    async def touch(self) -> None:
        await self._storage.call((_UNUSED_KEY, _UNUSED_KEY, _UNUSED_KEY, self.key), operation="touch_worker")

    async def read(self, *, pending: bool = False, count: int = 32) -> tuple[tuple[str, RelayFrame], ...]:
        rows = await self._storage.read(self.key, "worker", self.worker_instance_id, pending=pending, count=count)
        result: list[tuple[str, RelayFrame]] = []
        try:
            for entry, fields in rows:
                raw = fields["frame"]
                if len(raw.encode()) > self._storage.limits.response_bytes:
                    raise ValueError("Relay response exceeds its byte budget")
                frame = RELAY_FRAME.validate_json(raw)
                if (
                    frame.use.worker_instance_id != self.worker_instance_id
                    or canonical_message(frame, max_bytes=self._storage.limits.response_bytes) != raw
                ):
                    raise ValueError("Relay response is not canonical or scoped to this Worker")
                result.append((entry, frame))
        except (KeyError, ValueError, TypeError, ValidationError) as error:
            raise RelayStoreError("response_invalid") from error
        return tuple(result)

    async def acknowledge(self, *entry_ids: str) -> None:
        if not 1 <= len(entry_ids) <= 128:
            raise ValueError("Relay acknowledgements must contain 1 to 128 entries")
        await self._storage.call(
            (_UNUSED_KEY, _UNUSED_KEY, _UNUSED_KEY, self.key),
            operation="ack_responses",
            entry_ids=entry_ids,
            worker=self.worker_instance_id,
        )
