"""Foreground listener diagnostics and finite shutdown of browser event streams."""

from __future__ import annotations

import socket
from collections.abc import AsyncGenerator, Mapping
from time import monotonic
from types import FrameType

import uvicorn
from a13n_logging import get_logger
from anyio import CancelScope, Event, create_task_group, sleep
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger("a13n_harness_ui.webui")


class WebUIServer(uvicorn.Server):
    """Wake infinite browser streams before Uvicorn drains HTTP requests."""

    def __init__(self, config: uvicorn.Config, *, stopping: Event) -> None:
        super().__init__(config)
        self._stopping = stopping

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        logger.info(
            "Stop requested. Waiting for WebUI startup or cleanup to finish."
            if self.should_exit or not self.started
            else "Stop requested."
        )
        super().handle_exit(sig, frame)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        logger.info("Starting WebUI server…")
        await super().startup(sockets)
        if self.started:
            logger.info("WebUI ready. Press Ctrl+C to stop.")

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        logger.info("Stopping WebUI: closing browser connections and cleaning up active work…")
        self._stopping.set()
        started = monotonic()
        async with create_task_group() as group:

            async def report_waiting() -> None:
                while True:
                    await sleep(2)
                    logger.info(
                        "Still stopping WebUI (%ds): waiting for connections or App cleanup.", monotonic() - started
                    )

            group.start_soon(report_waiting)
            try:
                await super().shutdown(sockets)
            finally:
                group.cancel_scope.cancel()
        if not self.force_exit:
            logger.info("WebUI stopped.")


class EventStreamResponse(StreamingResponse):
    """End SSE bodies normally when this listener stops, not after drain timeout."""

    def __init__(
        self, content: AsyncGenerator[str], *, stopping: Event, media_type: str, headers: Mapping[str, str]
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._events = content
        self._stopping = stopping

    async def stream_response(self, send: Send) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        async with create_task_group() as group:

            async def wait_for_shutdown() -> None:
                await self._stopping.wait()
                group.cancel_scope.cancel()

            group.start_soon(wait_for_shutdown)
            try:
                async for chunk in self.body_iterator:
                    if not isinstance(chunk, (bytes, memoryview)):
                        chunk = chunk.encode(self.charset)
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            finally:
                with CancelScope(shield=True):
                    await self._events.aclose()
                group.cancel_scope.cancel()
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class RequestLog:
    """Report API outcomes without logging keys, query values, bodies or native paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = monotonic()

        async def report(message: Message) -> None:
            if message["type"] == "http.response.start":
                route = scope.get("route")
                path = route.path if isinstance(route, Route) else "<unmatched>"
                status = message["status"]
                log = logger.warning if status >= 400 else logger.info
                if not path.startswith("/api/") and status < 400:
                    log = logger.debug
                log("%s %s → %d (%.0f ms)", scope["method"], path, status, (monotonic() - started) * 1000)
            await send(message)

        await self.app(scope, receive, report)
