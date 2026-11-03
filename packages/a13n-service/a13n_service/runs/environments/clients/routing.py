"""Bounded single-send execution requests addressed to the current connection owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from a13n_envd_client.eip.v1 import EIPError
from a13n_envd_client.errors import EIPMethodError, EIPRequestTimeoutError, EIPTransportClosedError
from a13n_harness.environment.models import EnvironmentError
from a13n_logging import exception_details, get_logger
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.settings import ClientEnvironments, RedisSettings

logger = get_logger(__name__)
_RESERVED_METHODS = frozenset(
    {
        "session.close",
        "scope.close",
        "session.keepalive",
        "operation.cancel",
        "process.kill",
        "process.release",
        "output.release",
        "file.close_reader",
        "file.abort_writer",
        "computer.close_observation",
    }
)


# Only native cleanup of an existing retained process may outlive cancellation.
RETAINED_CLEANUP = frozenset(
    {
        "environment.readiness",
        "process.inspect",
        "process.kill",
        "process.wait",
        "process.release",
        "output.release",
        "scope.close",
    }
)


class Request(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    environment_id: str = Field(min_length=1, max_length=72)
    epoch: str = Field(pattern=r"^[a-f0-9]{32}$")
    scope_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    thread_id: str = Field(min_length=1, max_length=72)
    caller: Caller
    method: str = Field(min_length=1, max_length=64)
    params: dict[str, JsonValue]
    expires_at: AwareDatetime
    cleanup: bool = False

    @property
    def retained_cleanup(self) -> bool:
        return self.caller.execution_id is not None and self.cleanup and self.method in RETAINED_CLEANUP

    @property
    def allows_cancelled_owner(self) -> bool:
        # Harness checks readiness before its guard can observe cancellation.
        return self.retained_cleanup or (
            self.caller.execution_id is not None and self.method == "environment.readiness"
        )

    @property
    def reserved_capacity(self) -> bool:
        return self.method in _RESERVED_METHODS or self.retained_cleanup


class Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["result"] = "result"
    value: dict[str, JsonValue]


class NativeFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["native_failure"] = "native_failure"
    error: EIPError


class Failure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["failure"] = "failure"
    message: str
    code: str = "environment_unavailable"
    pre_dispatch: bool = False
    connection_failed: bool = False


type Outcome = Annotated[Result | NativeFailure | Failure, Field(discriminator="kind")]


class Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    epoch: str
    scope_id: str
    outcome: Outcome


def request_channel(node: str) -> str:
    return f"a13n:client-environment:requests:{node}"


def reply_channel(request_id: str) -> str:
    return f"a13n:client-environment:replies:{request_id}"


async def subscribe(subscription: PubSub, channel: str) -> None:
    """Await the actual acknowledgment; sending SUBSCRIBE is not sufficient."""
    await subscription.subscribe(channel)
    while True:
        message = await subscription.get_message(timeout=1)
        if message is not None and message["type"] == "subscribe" and message["channel"] == channel:
            return


class Routing:
    def __init__(self, redis: RedisSettings, config: ClientEnvironments) -> None:
        self.config = config
        # A lost PUBLISH response must not cause implicit Redis command replay.
        self.redis = Redis.from_url(
            redis.url.get_secret_value(),
            socket_timeout=redis.timeout,
            socket_connect_timeout=redis.timeout,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
        self.ready = asyncio.Event()
        self._pending = 0
        self._pending_cleanup = 0

    async def call(self, node: str, request: Request) -> Response:
        remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
        payload = request.model_dump_json()
        if len(payload.encode()) > self.config.message_bytes:
            raise EnvironmentError(
                "Client request exceeds the transport limit.",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            )
        if remaining <= 0:
            raise EnvironmentError(
                "Client request expired before dispatch.",
                code="environment_unavailable",
                details={"dispatch_stage": "pre_dispatch"},
            )
        cleanup = request.reserved_capacity
        pending, limit = (self._pending_cleanup, 8) if cleanup else (self._pending, self.config.max_requests)
        if pending >= limit:
            raise EnvironmentError(
                "Client request capacity is exhausted.",
                code="environment_unavailable",
                details={"dispatch_stage": "pre_dispatch"},
            )
        if cleanup:
            self._pending_cleanup += 1
        else:
            self._pending += 1
        published = False
        try:
            async with asyncio.timeout(min(remaining, self.config.request_seconds)), self.redis.pubsub() as listener:
                await subscribe(listener, reply_channel(request.id))
                # From this point failure may follow a delivered PUBLISH.
                published = True
                count = await self.redis.publish(request_channel(node), payload)
                if count == 0:
                    raise EnvironmentError(
                        "Client connection owner is unavailable; the request was not dispatched.",
                        code="environment_unavailable",
                        details={"dispatch_stage": "pre_dispatch", "client_connection_failed": True},
                    )
                while True:
                    message = await listener.get_message(ignore_subscribe_messages=True, timeout=1)
                    if message is None:
                        continue
                    raw = message["data"]
                    if not isinstance(raw, str) or len(raw.encode()) > self.config.message_bytes:
                        continue
                    try:
                        response = Response.model_validate_json(raw)
                    except ValueError:
                        continue
                    if (response.id, response.epoch, response.scope_id) != (
                        request.id,
                        request.epoch,
                        request.scope_id,
                    ):
                        continue
                    return response
        except (RedisError, TimeoutError):
            raise EnvironmentError(
                "Client response was not received; the operation outcome is unknown. Do not repeat a mutation."
                if published
                else "Client request could not be dispatched.",
                code="environment_unavailable",
                details={} if published else {"dispatch_stage": "pre_dispatch"},
            ) from None
        finally:
            if cleanup:
                self._pending_cleanup -= 1
            else:
                self._pending -= 1

    async def _publish(self, response: Response) -> None:
        payload = response.model_dump_json()
        if len(payload.encode()) > self.config.message_bytes:
            payload = Response(
                id=response.id,
                epoch=response.epoch,
                scope_id=response.scope_id,
                outcome=Failure(message="Client result exceeds the transport limit; execution outcome is unknown."),
            ).model_dump_json()
        try:
            await self.redis.publish(reply_channel(response.id), payload)
        except RedisError as error:
            logger.warning("Client environment reply lost", extra={"exception_details": exception_details(error)})

    async def _reply(self, request: Request, dispatch: Callable[[Request], Awaitable[dict[str, JsonValue]]]) -> None:
        remaining = (request.expires_at - datetime.now(UTC)).total_seconds() - self.config.response_margin_seconds
        outcome: Outcome
        if remaining <= 0:
            outcome = Failure(message="Client request expired.", pre_dispatch=True)
        else:
            try:
                async with asyncio.timeout(
                    min(remaining, self.config.request_seconds - self.config.response_margin_seconds)
                ):
                    outcome = Result(value=await dispatch(request))
            except EIPMethodError as error:
                outcome = NativeFailure(error=error.error)
            except EIPRequestTimeoutError as error:
                outcome = Failure(
                    message="Client response timed out; outcome is unknown."
                    if error.dispatched
                    else "Client request expired before native dispatch.",
                    pre_dispatch=not error.dispatched,
                )
            except EIPTransportClosedError:
                outcome = Failure(
                    message="Client disconnected; the dispatched operation outcome is unknown.", connection_failed=True
                )
            except EnvironmentError as error:
                outcome = Failure(
                    message=str(error),
                    code=error.code,
                    pre_dispatch=error.details.get("dispatch_stage") == "pre_dispatch",
                    connection_failed=error.details.get("client_connection_failed") is True,
                )
            except Exception as error:
                logger.warning(
                    "Client environment request failed", extra={"exception_details": exception_details(error)}
                )
                outcome = Failure(message="Client operation outcome is unknown; inspect before retrying.")
        await self._publish(Response(id=request.id, epoch=request.epoch, scope_id=request.scope_id, outcome=outcome))

    async def serve(
        self,
        node: str,
        dispatch: Callable[[Request], Awaitable[dict[str, JsonValue]]],
        *,
        on_subscription_lost: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        ordinary: set[asyncio.Task[None]] = set()
        cleanup: set[asyncio.Task[None]] = set()
        try:
            async with asyncio.TaskGroup() as tasks:
                while True:
                    try:
                        async with self.redis.pubsub() as listener:
                            async with asyncio.timeout(self.config.request_seconds):
                                await subscribe(listener, request_channel(node))
                            self.ready.set()
                            while True:
                                message = await listener.get_message(ignore_subscribe_messages=True, timeout=1)
                                if message is None:
                                    continue
                                raw = message["data"]
                                if not isinstance(raw, str) or len(raw.encode()) > self.config.message_bytes:
                                    continue
                                try:
                                    request = Request.model_validate_json(raw)
                                except ValueError:
                                    continue
                                reserved = request.reserved_capacity
                                active, limit = (cleanup, 8) if reserved else (ordinary, self.config.max_requests)
                                if len(active) >= limit:
                                    await self._publish(
                                        Response(
                                            id=request.id,
                                            epoch=request.epoch,
                                            scope_id=request.scope_id,
                                            outcome=Failure(
                                                message="Client request capacity is exhausted.", pre_dispatch=True
                                            ),
                                        )
                                    )
                                    continue
                                task = tasks.create_task(self._reply(request, dispatch))
                                active.add(task)
                                task.add_done_callback(active.discard)
                    except (RedisError, TimeoutError) as error:
                        was_ready = self.ready.is_set()
                        self.ready.clear()
                        if was_ready and on_subscription_lost is not None:
                            await on_subscription_lost()
                        logger.warning(
                            "Client environment routing interrupted",
                            extra={"exception_details": exception_details(error)},
                        )
                        await asyncio.sleep(1)
        finally:
            self.ready.clear()

    async def close(self) -> None:
        await self.redis.aclose()
