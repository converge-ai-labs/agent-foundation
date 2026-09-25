"""Metrics and trace export: the process's meter, its Prometheus endpoint, one OTLP trace pipeline per executable
to the deployment's trace backend, and attempt correlation.

The Service owns the OpenTelemetry SDK. Metrics are process-wide: the executable serves them once
(`serve_metrics`), Service modules record through `meter`, and the Harness receives the same provider. Traces
leave through a provider of their own that only the Harness uses. Every Harness span of an attempt carries the
attempt's correlation as Harness observation metadata; trace queries match the same attributes, so
`correlation_attributes` is the one owner of their names.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Protocol

from a13n_harness import HarnessInstrumentation, HarnessObservationContext, HarnessTraceContent
from a13n_logging import get_logger
from anyio import CancelScope, move_on_after, to_thread
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import start_http_server

logger = get_logger(__name__)

EXPORT_SECONDS = 10
SHUTDOWN_SECONDS = 10

_METADATA = "a13n.observation.metadata."
_SESSION = "a13n.observation.session.id"

# Service instruments are created from this meter where they are recorded; they record nothing until
# `serve_metrics` installs the process's provider.
meter = metrics.get_meter("a13n_service")


class Gauge:
    """A current value per attribute set, reported at every collection until it is replaced.

    The SDK's synchronous gauge reports a value only at the first collection after it was set, so a scrape between
    two updates would find no series.
    """

    def __init__(self, name: str, *, unit: str, description: str) -> None:
        self._values: dict[frozenset[tuple[str, str]], float] = {}
        meter.create_observable_gauge(name, callbacks=[self._observe], unit=unit, description=description)

    def set(self, value: float, attributes: Mapping[str, str]) -> None:
        self._values[frozenset(attributes.items())] = value

    def _observe(self, options: CallbackOptions) -> list[Observation]:
        # Scrapes call this from the exporter's thread; copying the items is atomic.
        return [Observation(value, dict(attributes)) for attributes, value in list(self._values.items())]


def _resource() -> Resource:
    return Resource.create({"service.name": "a13n-service", "service.version": version("a13n-service")})


def serve_metrics(host: str, port: int) -> None:
    """Serve this process's metrics, the Service's and the Harness's, in the Prometheus text format at
    `http://{host}:{port}/metrics`, from a daemon thread for the process's lifetime.

    Call once, at the executable boundary, like logging configuration: the meter provider is process-wide.
    """
    reader = PrometheusMetricReader(scope_info_enabled=False)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader], resource=_resource()))
    start_http_server(port, addr=host)


class ExportTarget(Protocol):
    """Where the OTLP/HTTP exporter sends spans, and the headers that authorize it."""

    @property
    def otlp_endpoint(self) -> str: ...

    @property
    def otlp_headers(self) -> dict[str, str]: ...


def correlation_attributes(
    organization_id: str,
    workspace_id: str,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    run_attempt_id: str | None = None,
) -> dict[str, str]:
    """The span attributes naming a tenant scope and, when given, a session, thread, run and attempt within it.

    The Harness owns the `run_id` and `thread_id` metadata keys (its own run and thread), hence
    `service_run_id`; the thread is the observation session, by which Langfuse groups a conversation, so the
    Service session is plain metadata.
    """
    attributes = {
        f"{_METADATA}organization_id": organization_id,
        f"{_METADATA}workspace_id": workspace_id,
        f"{_METADATA}session_id": session_id,
        f"{_METADATA}service_run_id": run_id,
        f"{_METADATA}run_attempt_id": run_attempt_id,
        _SESSION: thread_id,
    }
    return {key: value for key, value in attributes.items() if value is not None}


def attempt_observation(
    *, organization_id: str, workspace_id: str, session_id: str, thread_id: str, run_id: str, run_attempt_id: str
) -> HarnessObservationContext:
    """The Harness observation context of one attempt; pass it as `RunBindings.observation`."""
    metadata = correlation_attributes(
        organization_id, workspace_id, session_id=session_id, run_id=run_id, run_attempt_id=run_attempt_id
    )
    return HarnessObservationContext(
        session_id=thread_id, metadata={key.removeprefix(_METADATA): value for key, value in metadata.items()}
    )


@asynccontextmanager
async def open_instrumentation(
    target: ExportTarget | None, *, metered: bool, content: HarnessTraceContent
) -> AsyncIterator[HarnessInstrumentation | None]:
    """The process's Harness instrumentation: spans exported to `target`, and the process's meter provider when
    `metered`; None when both are off.

    Spans leave in background batches, so a slow or failing backend never delays execution; exit flushes
    what is queued within a bounded time.
    """
    meter_provider = metrics.get_meter_provider() if metered else None
    if target is None:
        yield (
            None
            if meter_provider is None
            else HarnessInstrumentation(meter_provider=meter_provider, trace_content=content)
        )
        return
    provider = TracerProvider(resource=_resource(), shutdown_on_exit=False)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=target.otlp_endpoint, headers=target.otlp_headers, timeout=EXPORT_SECONDS)
        )
    )
    try:
        yield HarnessInstrumentation(tracer_provider=provider, meter_provider=meter_provider, trace_content=content)
    finally:
        with CancelScope(shield=True), move_on_after(SHUTDOWN_SECONDS) as scope:
            try:
                await to_thread.run_sync(provider.shutdown, abandon_on_cancel=True)
            except Exception as error:
                logger.warning("Trace export shutdown failed", extra={"error_type": type(error).__name__})
        if scope.cancel_called:
            logger.warning("Trace export did not flush before shutdown")
