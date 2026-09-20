from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_service.api import install_api_conventions
from a13n_service.observability import TraceContent, build_observability_runtime
from a13n_service.observability.correlation import publish_run_acceptance
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import FunctionModel


def _runtime(exporter: InMemorySpanExporter, *, tracing: bool = True):
    return build_observability_runtime(
        enabled=tracing,
        metrics_enabled=True,
        trace_content=TraceContent.none,
        service_name="a13n-service",
        service_version="test",
        deployment_environment="test",
        service_role="control",
        service_instance_id="control-1",
        span_exporter=exporter,
    )


def _app(observability) -> FastAPI:
    app = FastAPI()
    app.state.runtime = SimpleNamespace(observability=observability)
    app.state.cancel_stream_started = asyncio.Event()
    install_api_conventions(app)

    @app.get("/runs/{run_id}")
    async def accepted(run_id: str, request: Request) -> dict[str, str]:
        publish_run_acceptance(run_id)
        logging.getLogger("a13n_service.test").info("accepted")
        return {"request_id": request.state.request_id, "run_id": run_id}

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("private provider response")

    @app.get("/stream-failure")
    async def stream_failure() -> StreamingResponse:
        async def body():
            yield b"started"
            raise OSError("private stream payload")

        return StreamingResponse(body())

    @app.get("/stream-cancel")
    async def stream_cancel() -> StreamingResponse:
        async def body():
            yield b"started"
            app.state.cancel_stream_started.set()
            await asyncio.Event().wait()

        return StreamingResponse(body())

    return app


@pytest.mark.anyio
async def test_http_boundary_generates_identity_and_exports_success_and_error_traces() -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    app = _app(runtime)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://service"
        ) as client:
            accepted = await client.get("/runs/run_123", headers={"X-Request-ID": "upstream-123"})
            broken = await client.get("/broken", headers={"X-Request-ID": "upstream-error"})
        assert accepted.status_code == 200
        request_id = accepted.headers["X-Request-ID"]
        assert request_id.startswith("req-") and request_id != "upstream-123"
        assert accepted.json() == {"request_id": request_id, "run_id": "run_123"}
        assert broken.status_code == 500
        assert broken.json()["error"]["request_id"] == broken.headers["X-Request-ID"]
        assert runtime.tracer_provider is not None and runtime.tracer_provider.force_flush()
        spans = [span for span in exporter.get_finished_spans() if span.name == "a13n.service.http.request"]
        assert len(spans) == 2
        by_route = {span.attributes["http.route"]: span for span in spans}
        success = by_route["/runs/{run_id}"]
        assert success.parent is None
        assert success.attributes["a13n.request.id"] == request_id
        assert success.attributes["a13n.upstream_request.id"] == "upstream-123"
        assert success.attributes["a13n.service.run.id"] == "run_123"
        assert by_route["/broken"].status.status_code.name == "ERROR"
        assert "private provider response" not in str(by_route["/broken"].attributes)
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_concurrent_requests_keep_context_isolated(caplog: pytest.LogCaptureFixture) -> None:
    from a13n_logging import ContextFilter

    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    app = _app(runtime)
    caplog.set_level(logging.INFO, logger="a13n_service.test")
    caplog.handler.addFilter(ContextFilter())
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://service") as client:
            first, second = await asyncio.gather(client.get("/runs/run_first"), client.get("/runs/run_second"))
        expected = {
            (first.json()["request_id"], "run_first"),
            (second.json()["request_id"], "run_second"),
        }
        records = {
            (record.request_id, record.run_id) for record in caplog.records if record.name == "a13n_service.test"
        }
        assert records == expected
    finally:
        caplog.handler.removeFilter(
            next(filter for filter in caplog.handler.filters if isinstance(filter, ContextFilter))
        )
        await runtime.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("tracing", [True, False])
async def test_metrics_endpoint_scrapes_service_and_harness_from_shared_provider(tracing: bool) -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter, tracing=tracing)

    async def model(_messages, _info):
        return "done"

    harness = HarnessBuilder(instrumentation=runtime.harness_instrumentation).build(
        AgentSpec(name="metrics-agent"), output_type=str, model=FunctionModel(function=model)
    )
    await harness.run("hello", bindings=RunBindings.embedded())
    app = _app(runtime)
    try:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://service") as client:
            await client.get("/runs/run_metrics")
            response = await client.get("/metrics")
        assert response.status_code == 200
        body = response.text
        assert "a13n_service_http_requests_total" in body
        assert "a13n_harness_run_duration_seconds" in body
        assert 'service_name="a13n-service"' in body
        assert 'a13n_service_role="control"' in body
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_stream_failure_and_cancellation_have_distinct_operational_outcomes() -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    app = _app(runtime)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://service"
        ) as client:
            failed = await client.get("/stream-failure")
            cancelled = asyncio.create_task(client.get("/stream-cancel"))
            await app.state.cancel_stream_started.wait()
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
        assert failed.status_code == 200
        assert runtime.tracer_provider is not None and runtime.tracer_provider.force_flush()
        spans = {span.attributes["http.route"]: span for span in exporter.get_finished_spans()}
        assert spans["/stream-failure"].attributes["a13n.http.outcome"] == "stream_error"
        assert spans["/stream-failure"].status.status_code.name == "ERROR"
        assert spans["/stream-cancel"].attributes["a13n.http.outcome"] == "cancelled"
        assert spans["/stream-cancel"].status.status_code.name == "UNSET"
        body = runtime.render_metrics().decode()
        assert 'a13n_http_outcome="stream_error"' in body
        assert 'a13n_http_outcome="cancelled"' in body
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_stream_disconnect_before_final_body_is_cancelled() -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    app = _app(runtime)
    received = iter(
        (
            {"type": "http.request", "body": b"", "more_body": False},
            {"type": "http.disconnect"},
        )
    )
    sent: list[dict[str, object]] = []

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream-cancel",
        "raw_path": b"/stream-cancel",
        "query_string": b"",
        "headers": (),
        "client": ("127.0.0.1", 1234),
        "server": ("service", 80),
        "root_path": "",
        "app": app,
        "state": {},
    }
    try:
        await app(scope, receive, send)
        assert any(message["type"] == "http.response.start" for message in sent)
        assert not any(
            message["type"] == "http.response.body" and not message.get("more_body", False) for message in sent
        )
        assert runtime.tracer_provider is not None and runtime.tracer_provider.force_flush()
        span = next(span for span in exporter.get_finished_spans() if span.name == "a13n.service.http.request")
        assert span.attributes["a13n.http.outcome"] == "cancelled"
    finally:
        await runtime.aclose()
