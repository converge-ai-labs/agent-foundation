"""Request identity, logs, metrics and a bounded HTTP trace at the ASGI boundary."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import nullcontext
from time import monotonic

from a13n_logging import bind_log_context, exception_details, set_log_context
from opentelemetry.trace import Status, StatusCode
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from a13n_service.error_response import api_error_body
from a13n_service.observability.correlation import bind_run_acceptance_observer
from a13n_service.observability.metrics import PROMETHEUS_CONTENT_TYPE
from a13n_service.observability.runtime import ObservabilityRuntime

logger = logging.getLogger("a13n_service.http")
_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


class RequestObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        request_id = state["request_id"] = f"req-{secrets.token_hex(12)}"
        process = getattr(scope["app"].state, "runtime", None)
        observation: ObservabilityRuntime | None = None if process is None else process.observability
        if scope["path"] == "/metrics" and scope["method"] in {"GET", "HEAD"}:
            payload = observation.render_metrics() if observation else None
            response = Response(
                payload if scope["method"] != "HEAD" else b"",
                status_code=404 if payload is None else 200,
                headers={"content-type": PROMETHEUS_CONTENT_TYPE, "X-Request-ID": request_id},
            )
            await response(scope, receive, send)
            return

        method = scope["method"] if scope["method"] in _METHODS else "OTHER"
        upstream = _upstream_id(scope)
        attributes: dict[str, str | int | float | bool] = {
            "a13n.request.id": request_id,
            "langfuse.observation.metadata.request_id": request_id,
            "http.request.method": method,
        }
        if upstream:
            state["upstream_request_id"] = upstream
            attributes["a13n.upstream_request.id"] = upstream
        status = 500
        completed = disconnected = response_started = False
        outcome = ""
        error_details: dict[str, object] = {}
        started = monotonic()

        async def observe_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            disconnected |= message["type"] == "http.disconnect"
            return message

        async def observe_send(message: Message) -> None:
            nonlocal status, completed, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                headers = [(k, v) for k, v in message.get("headers", ()) if k.lower() != b"x-request-id"]
                message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode())]}
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        span_context = observation.http_request(attributes) if observation else nullcontext(None)
        with (
            bind_log_context(request_id=request_id, **({"upstream_request_id": upstream} if upstream else {})),
            span_context as span,
        ):

            def accepted(run_id: str) -> None:
                set_log_context(run_id=run_id)
                if span is not None:
                    span.set_attribute("a13n.service.run.id", run_id)
                    span.set_attribute("langfuse.observation.metadata.run_id", run_id)

            try:
                with bind_run_acceptance_observer(accepted):
                    process_status = getattr(scope["app"].state, "process_status", None)
                    if (
                        process_status is not None
                        and process_status.draining
                        and scope["path"] not in {"/healthz", "/readyz"}
                    ):
                        response = JSONResponse(
                            api_error_body(
                                request_id, "service_unavailable", "The service is temporarily unavailable."
                            ),
                            status_code=503,
                        )
                        await response(scope, observe_receive, observe_send)
                    else:
                        await self._app(scope, observe_receive, observe_send)
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as error:
                outcome = "stream_error" if response_started else "server_error"
                error_details = {"exception": exception_details(error)}
                raise
            finally:
                if not outcome:
                    outcome = (
                        "cancelled"
                        if disconnected and not completed
                        else "server_error"
                        if status >= 500
                        else "client_error"
                        if status >= 400
                        else "success"
                    )
                route = getattr(scope.get("route"), "path", "unmatched")
                elapsed = monotonic() - started
                failed = outcome in {"server_error", "stream_error"}
                if span is not None:
                    span.set_attributes(
                        {"http.route": route, "http.response.status_code": status, "a13n.http.outcome": outcome}
                    )
                    if failed:
                        span.set_status(Status(StatusCode.ERROR))
                if observation is not None and observation.metrics_runtime is not None:
                    labels = {
                        "http.request.method": method,
                        "http.route": route,
                        "http.response.status_code": status,
                        "a13n.http.outcome": outcome,
                    }
                    try:
                        observation.metrics_runtime.requests.add(1, labels)
                        observation.metrics_runtime.duration.record(elapsed, labels)
                    except Exception:
                        logger.warning("http_metrics_recording_failed")
                logger.log(
                    logging.ERROR if failed else logging.INFO,
                    "http_request_completed",
                    extra={
                        "event": "http_request_completed",
                        "method": method,
                        "route": route,
                        "status_code": status,
                        "outcome": outcome,
                        "duration_seconds": elapsed,
                        **error_details,
                    },
                )


def _upstream_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", ()):
        if key.lower() == b"x-request-id" and 0 < len(value) <= 128 and all(0x21 <= byte <= 0x7E for byte in value):
            return value.decode("ascii")
    return None
