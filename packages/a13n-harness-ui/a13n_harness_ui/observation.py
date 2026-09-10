"""App-owned OpenTelemetry setup and bounded Host operation observations."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from importlib.metadata import version
from typing import Literal

from a13n_harness import HarnessInstrumentation
from a13n_logging import get_logger
from anyio import CancelScope, move_on_after, to_thread
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import INVALID_SPAN, Link, NoOpTracerProvider, ProxyTracerProvider, Span, StatusCode

_SCOPE = "a13n-harness-ui"
_TRACE_FIELDS = ("langfuse.trace.name", "langfuse.session.id", "langfuse.trace.tags")


class HarnessUiSpanProcessor(SpanProcessor):
    """Enrich existing spans for Langfuse without another execution pipeline.

    Explicit-provider Hosts may install this processor before their exporter,
    or supply their own vendor profile instead. Only local parent attributes
    are copied; no baggage, trace registry, or payload capture is introduced.
    """

    def on_start(self, span: SdkSpan, parent_context: Context | None = None) -> None:
        parent = trace.get_current_span(parent_context)
        if isinstance(parent, SdkSpan) and parent.get_span_context().trace_id == span.get_span_context().trace_id:
            attributes = parent.attributes or {}
            for key in _TRACE_FIELDS:
                value = attributes.get(key)
                if value is not None and key not in (span.attributes or {}):
                    span.set_attribute(key, value)
        attributes = span.attributes or {}
        operation = attributes.get("gen_ai.operation.name")
        if span.name == "harness.run" or operation == "invoke_agent":
            span.set_attribute("langfuse.observation.type", "agent")
        elif operation == "execute_tool":
            span.set_attribute("langfuse.observation.type", "tool")


@dataclass(frozen=True)
class UiObservation:
    instrumentation: HarnessInstrumentation | None = None

    @contextmanager
    def operation(
        self,
        kind: Literal["root", "subagent"],
        *,
        thread_id: str,
        operation_id: str,
        linked: bool = False,
    ) -> Iterator[Span]:
        provider = self.instrumentation.tracer_provider if self.instrumentation is not None else None
        if provider is None or isinstance(provider, (NoOpTracerProvider, ProxyTracerProvider)):
            yield INVALID_SPAN
            return
        tracer = provider.get_tracer(_SCOPE)
        parent = trace.get_current_span().get_span_context()
        links = (Link(parent),) if linked and parent.is_valid else ()
        name = f"harness_ui.{kind}"
        with tracer.start_as_current_span(
            name,
            context=Context() if linked else None,
            links=links,
            attributes={
                "a13n.thread.id": thread_id,
                "a13n.ui.operation.id": operation_id,
                "a13n.ui.operation.kind": kind,
                "langfuse.trace.name": name,
                "langfuse.session.id": thread_id,
                "langfuse.trace.tags": ("harness-ui", kind),
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(StatusCode.ERROR)
                raise


def finish_operation(span: Span, *, status: str, run_id: str | None = None, error_code: str | None = None) -> None:
    span.set_attribute("a13n.ui.operation.status", status)
    if run_id is not None:
        span.set_attribute("a13n.run.id", run_id)
    if error_code is not None:
        span.set_attribute("a13n.error.code", error_code)
    if status in {"failed", "lost"}:
        span.set_status(StatusCode.ERROR)


@asynccontextmanager
async def open_observation(
    instrumentation: HarnessInstrumentation | Literal["environment"] | None = "environment",
    *,
    shutdown_timeout_seconds: float = 10.0,
) -> AsyncIterator[UiObservation]:
    """Resolve once per App; never replace or shut down a caller's providers."""
    selected = HarnessInstrumentation.from_environment() if instrumentation == "environment" else instrumentation
    owned: TracerProvider | None = None
    try:
        if (
            instrumentation == "environment"
            and selected is not None
            and isinstance(selected.tracer_provider, ProxyTracerProvider)
            and os.environ.get("OTEL_SDK_DISABLED", "").lower().strip() != "true"
        ):
            exporter = os.environ.get("OTEL_TRACES_EXPORTER", "none").strip().lower()
            if exporter not in {"none", "otlp"}:
                raise ValueError("Harness UI automatic tracing supports OTEL_TRACES_EXPORTER=otlp or none")
            if exporter == "otlp":
                protocol = os.environ.get(
                    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
                    os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
                )
                if protocol.strip().lower() != "http/protobuf":
                    raise ValueError("Harness UI automatic OTLP tracing requires the http/protobuf protocol")
                owned = TracerProvider(
                    resource=Resource.create(
                        {"service.name": "a13n-harness-ui", "service.version": version("a13n-harness-ui")}
                    ).merge(OTELResourceDetector().detect()),
                    shutdown_on_exit=False,
                )
                # Resource.create reads standard OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME.
                # Keep the SDK's resource/sampling/batching/exporter environment semantics.
                owned.add_span_processor(HarnessUiSpanProcessor())
                owned.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                selected = replace(selected, tracer_provider=owned)
        yield UiObservation(selected)
    finally:
        if owned is not None:
            with CancelScope(shield=True), move_on_after(shutdown_timeout_seconds) as scope:
                try:
                    await to_thread.run_sync(owned.shutdown, abandon_on_cancel=True)
                except Exception:
                    get_logger(__name__).warning("Harness UI trace shutdown failed")
            if scope.cancel_called:
                get_logger(__name__).warning("Harness UI trace shutdown exceeded its time budget")
