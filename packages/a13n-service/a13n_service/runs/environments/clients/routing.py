"""Bounded single-send execution requests addressed to the current connection owner."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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
_CLEANUP_CAPACITY = 8
_RESERVED_METHODS = frozenset(
    {
        "session.close",
        "scope.close",
        "scope.detach",
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
RECOVERY_DISCOVERY = frozenset(
    {"scope.open", "environment.readiness", "process.inspect", "process.resolve_start", "scope.detach"}
)
RETAINED_CLEANUP = RECOVERY_DISCOVERY | frozenset(
    {
        "environment.readiness",
        "process.inspect",
        "process.kill",
        "process.wait",
        "process.release",
        "output.release",
        "scope.detach",
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
    session_failed: bool = False


type Outcome = Annotated[Result | NativeFailure | Failure, Field(discriminator="kind")]


class Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    environment_id: str
    epoch: str
    scope_id: str
    outcome: Outcome


def request_channel(environment_id: str) -> str:
    return f"a13n:client-environment:requests:{environment_id}"


def reply_channel(environment_id: str) -> str:
    return f"a13n:client-environment:replies:{environment_id}"


async def subscribe(subscription: PubSub, channel: str) -> None:
    """Await the actual acknowledgment; sending SUBSCRIBE is not sufficient."""
    await subscription.subscribe(channel)
    while True:
        message = await subscription.get_message(timeout=1)
        if message is not None and message["type"] == "subscribe" and message["channel"] == channel:
            return


class Replies:
    """One environment subscription shared by this Worker's Sessions and calls."""

    def __init__(self, redis: Redis, environment_id: str, message_bytes: int) -> None:
        self.users = 0
        self.pending: dict[str, tuple[Request, asyncio.Future[Response | None]]] = {}
        self.ready = asyncio.Event()
        self.start(redis, environment_id, message_bytes)

    def start(self, redis: Redis, environment_id: str, message_bytes: int) -> None:
        self.ready.clear()
        self.task = asyncio.create_task(self._listen(redis, environment_id, message_bytes), name="client-replies")

    async def _listen(self, redis: Redis, environment_id: str, message_bytes: int) -> None:
        try:
            async with redis.pubsub() as listener:
                await subscribe(listener, reply_channel(environment_id))
                self.ready.set()
                while True:
                    message = await listener.get_message(ignore_subscribe_messages=True, timeout=1)
                    if message is None:
                        continue
                    raw = message["data"]
                    if not isinstance(raw, str) or len(raw.encode()) > message_bytes:
                        continue
                    try:
                        response = Response.model_validate_json(raw)
                    except ValueError:
                        continue
                    waiting = self.pending.get(response.id)
                    if waiting is None:
                        continue
                    request, future = waiting
                    if (response.environment_id, response.epoch, response.scope_id) != (
                        request.environment_id,
                        request.epoch,
                        request.scope_id,
                    ):
                        continue
                    if not future.done():
                        future.set_result(response)
        except Exception as error:
            logger.warning("Client reply subscription lost", extra={"exception_details": exception_details(error)})
        finally:
            # Wake admission and existing waits; neither one may replay a request.
            self.ready.set()
            for _, future in self.pending.values():
                if not future.done():
                    future.set_result(None)


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
        self._replies: dict[str, Replies] = {}
        self._ordinary: set[asyncio.Task[None]] = set()
        self._cleanup: set[asyncio.Task[None]] = set()
        self._closed = False
        self._pending = 0
        self._pending_cleanup = 0

    @asynccontextmanager
    async def replies(self, environment_id: str) -> AsyncIterator[Replies]:
        if self._closed:
            raise EnvironmentError(
                "Client routing is closed.", code="environment_unavailable", details={"dispatch_stage": "pre_dispatch"}
            )
        replies = self._replies.get(environment_id)
        if replies is None:
            replies = Replies(self.redis, environment_id, self.config.message_bytes)
            self._replies[environment_id] = replies
        elif replies.task.done():
            # Existing Sessions still hold this subscription's lifetime. Restart
            # its reader without losing their references or reviving failed calls.
            replies.start(self.redis, environment_id, self.config.message_bytes)
        replies.users += 1
        try:
            yield replies
        finally:
            replies.users -= 1
            if replies.users == 0:
                if self._replies.get(environment_id) is replies:
                    del self._replies[environment_id]
                replies.task.cancel()
                await asyncio.gather(replies.task, return_exceptions=True)

    def _capacity_exhausted(self, request: Request, count: int) -> bool:
        limit = _CLEANUP_CAPACITY if request.reserved_capacity else self.config.max_requests
        return count >= limit

    async def call(self, request: Request) -> Response:
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
        pending = self._pending_cleanup if cleanup else self._pending
        if self._capacity_exhausted(request, pending):
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
            async with (
                asyncio.timeout(min(remaining, self.config.request_seconds)),
                self.replies(request.environment_id) as replies,
            ):
                await replies.ready.wait()
                if replies.task.done():
                    raise RedisError("Client reply subscription is unavailable")
                if request.id in replies.pending:
                    raise EnvironmentError(
                        "Client request ID is already pending.",
                        code="environment_request_invalid",
                        details={"dispatch_stage": "pre_dispatch"},
                    )
                future: asyncio.Future[Response | None] = asyncio.get_running_loop().create_future()
                replies.pending[request.id] = request, future
                # From this point failure may follow a delivered PUBLISH.
                try:
                    published = True
                    count = await self.redis.publish(request_channel(request.environment_id), payload)
                    if count == 0:
                        raise EnvironmentError(
                            "Client connection owner is unavailable; the request was not dispatched.",
                            code="environment_unavailable",
                            details={"dispatch_stage": "pre_dispatch", "client_connection_failed": True},
                        )
                    response = await future
                    if response is None:
                        raise RedisError("Client reply subscription was interrupted")
                    return response
                finally:
                    replies.pending.pop(request.id, None)
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
                environment_id=response.environment_id,
                epoch=response.epoch,
                scope_id=response.scope_id,
                outcome=Failure(message="Client result exceeds the transport limit; execution outcome is unknown."),
            ).model_dump_json()
        try:
            await self.redis.publish(reply_channel(response.environment_id), payload)
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
                    session_failed=error.details.get("client_session_failed") is True,
                )
            except Exception as error:
                logger.warning(
                    "Client environment request failed", extra={"exception_details": exception_details(error)}
                )
                outcome = Failure(message="Client operation outcome is unknown; inspect before retrying.")
        await self._publish(
            Response(
                id=request.id,
                environment_id=request.environment_id,
                epoch=request.epoch,
                scope_id=request.scope_id,
                outcome=outcome,
            )
        )

    async def serve(
        self,
        environment_id: str,
        epoch: str,
        dispatch: Callable[[Request], Awaitable[dict[str, JsonValue]]],
        *,
        ready: asyncio.Event,
        on_subscription_lost: Callable[[], Awaitable[None]],
    ) -> None:
        """Serve only this connection; a lost subscription retires its carrier."""
        try:
            async with asyncio.TaskGroup() as tasks, self.redis.pubsub() as listener:
                async with asyncio.timeout(self.config.request_seconds):
                    await subscribe(listener, request_channel(environment_id))
                ready.set()
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
                    # Old and new subscribers may overlap while handlers unwind.
                    # A subscriber must never answer for another connection epoch.
                    if (request.environment_id, request.epoch) != (environment_id, epoch):
                        continue
                    active = self._cleanup if request.reserved_capacity else self._ordinary
                    if self._capacity_exhausted(request, len(active)):
                        await self._publish(
                            Response(
                                id=request.id,
                                environment_id=environment_id,
                                epoch=request.epoch,
                                scope_id=request.scope_id,
                                outcome=Failure(message="Client request capacity is exhausted.", pre_dispatch=True),
                            )
                        )
                        continue
                    task = tasks.create_task(self._reply(request, dispatch))
                    active.add(task)
                    task.add_done_callback(active.discard)
        except Exception as error:
            logger.warning(
                "Client environment routing interrupted",
                extra={"environment_id": environment_id, "exception_details": exception_details(error)},
            )
        finally:
            ready.clear()
            await on_subscription_lost()

    async def close(self) -> None:
        self._closed = True
        tasks = [replies.task for replies in self._replies.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._replies.clear()
        await self.redis.aclose()
