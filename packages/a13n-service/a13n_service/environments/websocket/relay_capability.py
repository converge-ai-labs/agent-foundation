"""Startup verification of the relay's actual scripting and Stream semantics."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.ids import new_object_id

from .authority import ConnectionIdentity, UseIdentity
from .relay_protocol import RelayRequest, RelayTerminal
from .relay_storage import ConnectionRelayStore, RelayStoreError, ResponseMailbox


async def validate_relay_backend(redis: Redis) -> None:
    """Probe isolated ephemeral keys; unsupported backends never serve the relay."""
    connection = ConnectionIdentity(
        new_object_id("org"), new_object_id("env"), new_object_id("ec"), new_object_id("ece"), new_object_id("ctrl")
    )
    use = UseIdentity(
        connection,
        new_object_id("eu"),
        new_object_id("run"),
        new_object_id("rat"),
        1,
        new_object_id("wkr"),
        "workspace",
        admission_deadline_ms=1,
    )
    owner = ConnectionRelayStore(redis, connection)
    mailbox = ResponseMailbox(redis, use.worker_instance_id)
    try:
        async with asyncio.timeout(5):
            await mailbox.prepare()
            await owner.prepare()
            seconds, micros = await redis.time()
            request = RelayRequest(
                request_id=new_object_id("erq"),
                scope=use,
                operation="scope.probe",
                deadline_ms=seconds * 1000 + micros // 1000 + 10_000,
            )
            appended = await owner.append(request)
            entries = await owner.read()
            if entries != ((appended.entry_id, request),):
                raise RelayStoreError("request_invalid")
            entry_id = entries[0][0]
            if (await owner.start(request, entry_id)).phase != "started":
                raise RelayStoreError("request_not_started")
            terminal = RelayTerminal(request_id=request.request_id, scope=use)
            await owner.complete(request, entry_id, terminal)
            frames = await mailbox.read()
            if len(frames) != 1 or frames[0][1] != terminal:
                raise RelayStoreError("response_invalid")
            if (await redis.xpending(owner.requests_key, "owner"))["pending"] != 0:
                raise RelayStoreError("request_not_acknowledged")
            await mailbox.acknowledge(frames[0][0])
            await owner.touch()
            await owner.prune()
            await mailbox.touch()
    except (RelayStoreError, RedisError, TimeoutError) as error:
        raise ValueError("Client WebSocket Environments require Redis with atomic Stream scripting support") from error
    finally:
        try:
            async with asyncio.timeout(1):
                await redis.delete(owner.requests_key, owner.ledger_key, owner.expiries_key, mailbox.key)
        except (RedisError, TimeoutError):
            # Each probe key also has a bounded TTL for dependency loss here.
            pass
