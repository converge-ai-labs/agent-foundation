"""Single-send routing to a Shell's original worker. PostgreSQL remains the ownership authority."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from a13n_harness.environment.models import EnvironmentError
from a13n_harness.toolsets.shell_control import ShellControl
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

if TYPE_CHECKING:
    from a13n_service.runs.tool_executions.owner import Executions

logger = get_logger(__name__)
_MAX_BYTES = 4 * 1024 * 1024


class Caller(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    attempt_id: str
    worker_id: str
    execution_id: str | None = None


class Request(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    thread_id: str
    execution_id: str
    caller: Caller
    control: ShellControl
    expires_at: datetime


class Response(BaseModel):
    result: dict[str, JsonValue] | None = None
    error: str | None = None
    pre_dispatch: bool = False


def channel(worker_id: str) -> str:
    return f"a13n:tool-controls:{worker_id}"


def reply_channel(request_id: str) -> str:
    return f"a13n:tool-control-replies:{request_id}"


class Routing:
    def __init__(self, executions: Executions):
        self.executions = executions
        settings = executions.runtime.settings.redis
        # Redis' implicit command retry is unsafe for stdin/signals after a lost reply.
        self.client = Redis.from_url(
            settings.url.get_secret_value(),
            socket_timeout=settings.timeout,
            socket_connect_timeout=settings.timeout,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
        self.ready = asyncio.Event()

    async def call(self, worker_id: str, request: Request) -> dict[str, JsonValue] | None:
        payload = request.model_dump_json()
        if len(payload.encode()) > _MAX_BYTES:
            raise EnvironmentError(
                "Shell control exceeds the transport limit.",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            )
        timeout = max(0, (request.expires_at - datetime.now(UTC)).total_seconds())
        try:
            async with asyncio.timeout(timeout), self.client.pubsub() as subscription:
                await subscription.subscribe(reply_channel(request.id))
                # Confirm subscription before sending, including very fast owner replies.
                await subscription.get_message(timeout=timeout)
                listeners = await self.client.publish(channel(worker_id), payload)
                if listeners == 0:
                    raise EnvironmentError(
                        "Shell owner transport is unavailable; the control was not dispatched.",
                        code="environment_unavailable",
                        details={"dispatch_stage": "pre_dispatch"},
                    )
                while True:
                    message = await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
                    if message is None:
                        continue
                    response = Response.model_validate_json(message["data"])
                    if response.error is not None:
                        raise EnvironmentError(
                            response.error,
                            code="environment_unavailable",
                            details={"dispatch_stage": "pre_dispatch"} if response.pre_dispatch else {},
                        )
                    return response.result
        except (RedisError, TimeoutError):
            raise EnvironmentError(
                "Shell owner did not confirm the control outcome. Do not repeat a mutation; inspect the execution.",
                code="environment_unavailable",
            ) from None

    async def _reply(self, request: Request) -> None:
        try:
            try:
                remaining = max(0, (request.expires_at - datetime.now(UTC)).total_seconds())
                async with asyncio.timeout(remaining):
                    result = await self.executions.local_control(request)
                response = Response(result=result)
            except EnvironmentError as error:
                response = Response(
                    error=str(error), pre_dispatch=error.details.get("dispatch_stage") == "pre_dispatch"
                )
            except Exception as error:
                logger.warning("Shell control failed", extra={"exception_details": exception_details(error)})
                response = Response(
                    error="Shell control outcome is unknown; inspect the execution before any mutation."
                )
            payload = response.model_dump_json()
            if len(payload.encode()) > _MAX_BYTES:
                payload = Response(
                    error="Shell control response exceeds the transport limit; inspect retained results."
                ).model_dump_json()
            await self.client.publish(reply_channel(request.id), payload)
        except RedisError as error:
            logger.warning("Shell control reply failed", extra={"exception_details": exception_details(error)})

    async def run(self, worker_id: str) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                replies: set[asyncio.Task[None]] = set()
                while True:
                    try:
                        async with self.client.pubsub() as subscription:
                            await subscription.subscribe(channel(worker_id))
                            await subscription.get_message(timeout=self.executions.runtime.settings.redis.timeout)
                            self.ready.set()
                            while True:
                                message = await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
                                if message is None:
                                    continue
                                raw = message["data"]
                                if not isinstance(raw, str) or len(raw.encode()) > _MAX_BYTES:
                                    continue
                                try:
                                    request = Request.model_validate_json(raw)
                                except ValueError:
                                    continue
                                if len(replies) >= self.executions.runtime.settings.tool_executions.max_active:
                                    await self.client.publish(
                                        reply_channel(request.id),
                                        Response(
                                            error="Shell control capacity is exhausted.",
                                            pre_dispatch=True,
                                        ).model_dump_json(),
                                    )
                                    continue
                                task = group.create_task(self._reply(request))
                                replies.add(task)
                                task.add_done_callback(replies.discard)
                    except RedisError as error:
                        self.ready.clear()
                        logger.warning(
                            "Shell routing interrupted", extra={"exception_details": exception_details(error)}
                        )
                        await asyncio.sleep(1)
        finally:
            self.ready.clear()
            await self.client.aclose()
