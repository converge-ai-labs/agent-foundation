"""Bound body allocation and upload time before JSON parsing or database work."""

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import error_response, request_id

type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type Application = Callable[[Message, Receive, Send], Awaitable[None]]


class BodyLimit:
    def __init__(
        self, app: Application, *, max_bytes: int, timeout: float, limit_for: Callable[[Message], int] | None = None
    ):
        self.app, self.max_bytes, self.timeout, self.limit_for = app, max_bytes, timeout, limit_for

    async def __call__(self, scope: Message, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = min(self.max_bytes, self.limit_for(scope)) if self.limit_for else self.max_bytes
        body = bytearray()
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > max_bytes:
                        limit = {"limit": max_bytes}
                        await error_response(
                            ServiceError("payload_too_large", "Request body exceeds its byte limit", limit),
                            request_id(scope),
                        )(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            timed_out = ServiceError("request_timeout", "Request body timed out")
            await error_response(timed_out, request_id(scope))(scope, receive, send)
            return
        buffered: bytes | None = bytes(body)
        del body

        async def bounded_receive() -> Message:
            nonlocal buffered
            if buffered is not None:
                value, buffered = buffered, None
                return {"type": "http.request", "body": value, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
