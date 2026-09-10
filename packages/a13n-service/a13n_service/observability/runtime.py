"""Process-owned OpenTelemetry runtime for a13n Service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal, cast
from urllib.parse import unquote

import anyio
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness.observation import record_span_metadata
from anyio import to_thread
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcOTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HttpOTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased, Sampler, TraceIdRatioBased
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, Tracer
from opentelemetry.trace import Span as APISpan

logger = logging.getLogger("a13n_service.observability")

INSTRUMENTATION_SCOPE = "a13n-a13n-service"
_ALLOWED_INSTRUMENTATION_SCOPES = frozenset(
    {
        INSTRUMENTATION_SCOPE,
        "a13n-harness",
        "pydantic-ai",
    }
)
_RUN_ATTEMPT_ROOT = "a13n.service.run_attempt"
_PHASE_NAMES = frozenset(
    {
        "a13n.service.reconstruct",
        "a13n.service.environment.prepare",
        "a13n.service.persist",
    }
)
_REGISTERED_SPAN_NAMES = _PHASE_NAMES | {_RUN_ATTEMPT_ROOT}
_MAX_CORRELATION_BYTES = 1024
_MAX_FAILURE_CODE_BYTES = 256
_DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_MISSING_CONTENT = object()
_RESOURCE_OVERRIDE_KEYS = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.environment.name",
        "service.instance.id",
    }
)
_BATCH_INTEGER_SETTINGS = {
    "OTEL_BSP_MAX_QUEUE_SIZE": 2048,
    "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": 512,
    "OTEL_BSP_SCHEDULE_DELAY": 5000,
    "OTEL_BSP_EXPORT_TIMEOUT": 30000,
}
_LINK_KINDS = frozenset(
    {
        "run_acceptance",
        "dispatch",
        "lease_expired",
        "retry_after_failure",
        "planned_handoff",
        "feedback",
        "async_child",
    }
)

RunAttemptOutcome = Literal["succeeded", "yielded", "failed", "cancelled"]
RecoveryReason = Literal["lease_expired", "retry_after_failure", "planned_handoff", "pending_input"]


class TraceContent(StrEnum):
    """Deployment-level content selection mapped directly to Harness."""

    none = "none"
    standard = "standard"
    full = "full"


@dataclass(frozen=True, slots=True)
class RunAttemptCorrelation:
    """Validated Service identities projected onto one Attempt trace."""

    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    run_attempt_number: int
    agent_id: str
    agent_revision_id: str
    model_id: str | None = None
    model_provider_type: str | None = None
    replaces_run_attempt_id: str | None = None
    recovery_reason: RecoveryReason | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "organization_id",
            "workspace_id",
            "session_id",
            "thread_id",
            "run_id",
            "run_attempt_id",
            "agent_id",
            "agent_revision_id",
        ):
            _validate_bounded_text(field_name, cast(str, getattr(self, field_name)), required=True)
        for field_name in ("model_id", "model_provider_type", "replaces_run_attempt_id"):
            value = cast(str | None, getattr(self, field_name))
            if value is not None:
                _validate_bounded_text(field_name, value, required=True)
        if self.run_attempt_number < 1:
            raise ValueError("run_attempt_number must be positive")
        if self.recovery_reason is not None and self.recovery_reason not in {
            "lease_expired",
            "retry_after_failure",
            "planned_handoff",
            "pending_input",
        }:
            raise ValueError("recovery_reason is invalid")

    def attributes(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {
            "a13n.organization.id": self.organization_id,
            "a13n.workspace.id": self.workspace_id,
            "a13n.observation.session.id": self.session_id,
            "session.id": self.thread_id,
            "a13n.thread.id": self.thread_id,
            "a13n.service.run.id": self.run_id,
            "a13n.run_attempt.id": self.run_attempt_id,
            "a13n.run_attempt.number": self.run_attempt_number,
            "a13n.agent.preset.id": self.agent_id,
            "a13n.agent.preset.revision.id": self.agent_revision_id,
        }
        if self.model_id is not None:
            values["a13n.model.id"] = self.model_id
        if self.model_provider_type is not None:
            values["a13n.model.provider.type"] = self.model_provider_type
        if self.replaces_run_attempt_id is not None:
            values["a13n.run_attempt.replaces.id"] = self.replaces_run_attempt_id
        if self.recovery_reason is not None:
            values["a13n.run_attempt.recovery.reason"] = self.recovery_reason
        return values


_current_run_attempt: ContextVar[RunAttemptCorrelation | None] = ContextVar(
    "a13n_service_current_run_attempt",
    default=None,
)


_current_attempt_trace: ContextVar[RunAttemptTrace | None] = ContextVar(
    "a13n_service_current_attempt_trace", default=None
)


def observe_input(value: object) -> None:
    """Reuse the accepted payload already materialized by Worker preparation."""
    attempt = _current_attempt_trace.get()
    if attempt is not None:
        attempt.set_input(value)


def remember_output(object_digest: str, value: object) -> None:
    """Retain one externalized candidate locally, without publishing uncommitted output."""
    attempt = _current_attempt_trace.get()
    if attempt is not None:
        attempt.remember_output(object_digest, value)


@contextmanager
def observe_phase(name: str, *, operation: str | None = None) -> Generator[APISpan | None]:
    """Time an actual Service phase under the active Attempt, never an ambient Host span."""
    attempt = _current_attempt_trace.get()
    if attempt is None or attempt._ended:
        yield None
        return
    with attempt.phase(name) as span:
        if span is not None and operation is not None:
            record_span_metadata(span, {"service.phase.operation": operation})
        yield span


def observe_phase_result(span: APISpan | None, **facts: str | bool | int) -> None:
    """Project owner-selected phase decisions, never candidate business output."""
    attempt = _current_attempt_trace.get()
    if span is None or attempt is None or attempt._ended or not span.is_recording():
        return
    record_span_metadata(span, {f"service.phase.{key}": value for key, value in facts.items()})
    try:
        span.set_attributes(_content_attributes("output", facts, attempt._trace_content))
    except Exception:
        _safe_warning("observability_content_projection_failed")


class _RunAttemptSpanProcessor(SpanProcessor):
    """Project correlation and export only approved RunAttempt spans."""

    def __init__(self, delegate: SpanProcessor | None = None) -> None:
        self._delegate = delegate

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        correlation = _current_run_attempt.get()
        scope = span.instrumentation_scope
        if correlation is None or scope is None or scope.name not in _ALLOWED_INSTRUMENTATION_SCOPES:
            return
        for key, value in correlation.attributes().items():
            span.set_attribute(key, value)
        if self._delegate is not None:
            try:
                self._delegate.on_start(span, parent_context)
            except Exception:
                _safe_warning("observability_processor_failed")

    def on_end(self, span: ReadableSpan) -> None:
        scope = span.instrumentation_scope
        if (
            self._delegate is not None
            and scope is not None
            and scope.name in _ALLOWED_INSTRUMENTATION_SCOPES
            and (scope.name != INSTRUMENTATION_SCOPE or span.name in _REGISTERED_SPAN_NAMES)
            and span.attributes is not None
            and "a13n.run_attempt.id" in span.attributes
        ):
            try:
                self._delegate.on_end(span)
            except Exception:
                _safe_warning("observability_processor_failed")

    def shutdown(self) -> None:
        if self._delegate is not None:
            self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        if self._delegate is None:
            return True
        return self._delegate.force_flush(timeout_millis)


class _SafeSpanExporter(SpanExporter):
    """Prevent a throwing exporter from affecting work or disclosing its error."""

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._delegate.export(spans)
        except Exception:
            _safe_warning("observability_export_failed")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


@dataclass(slots=True)
class ObservabilityRuntime:
    """One process-owned tracer provider and its Harness projection."""

    tracer_provider: TracerProvider | None
    trace_content: TraceContent
    shutdown_timeout_seconds: float = _DEFAULT_SHUTDOWN_TIMEOUT_SECONDS

    @property
    def harness_instrumentation(self) -> HarnessInstrumentation | None:
        if self.tracer_provider is None:
            return None
        return HarnessInstrumentation(
            tracer_provider=self.tracer_provider,
            trace_content=HarnessTraceContent(self.trace_content.value),
        )

    @contextmanager
    def run_attempt(
        self,
        correlation: RunAttemptCorrelation,
        *,
        input_value: object = _MISSING_CONTENT,
        input_external: bool = False,
        links: tuple[Link, ...] = (),
    ) -> Generator[RunAttemptTrace]:
        """Start one parentless Attempt root and keep it current for its lifecycle."""

        if _current_run_attempt.get() is not None:
            raise RuntimeError("A RunAttempt trace is already active in this context")
        if self.tracer_provider is None:
            yield RunAttemptTrace(None, self.trace_content)
            return

        _validate_links(links)
        tracer = self.tracer_provider.get_tracer(INSTRUMENTATION_SCOPE)
        run_token = _current_run_attempt.set(correlation)
        try:
            span = tracer.start_span(
                _RUN_ATTEMPT_ROOT,
                context=Context(),
                attributes=correlation.attributes(),
                links=links,
            )
        except BaseException:
            _current_run_attempt.reset(run_token)
            raise
        try:
            otel_token = otel_context.attach(trace.set_span_in_context(span))
        except BaseException:
            span.end()
            _current_run_attempt.reset(run_token)
            raise
        attempt = RunAttemptTrace(span, self.trace_content, tracer=tracer)
        attempt.set_input(input_value, external=input_external)
        attempt_token = _current_attempt_trace.set(attempt)
        try:
            yield attempt
        except BaseException:
            attempt.mark_local_error()
            raise
        finally:
            _current_attempt_trace.reset(attempt_token)
            otel_context.detach(otel_token)
            _current_run_attempt.reset(run_token)
            attempt.end()

    async def aclose(self) -> None:
        """Flush and stop the provider without extending process shutdown indefinitely."""

        provider = self.tracer_provider
        self.tracer_provider = None
        if provider is None:
            return
        timeout_millis = max(1, int(self.shutdown_timeout_seconds * 1000))

        def flush_and_shutdown() -> tuple[bool, bool]:
            flush_ok = False
            shutdown_ok = False
            try:
                flush_ok = provider.force_flush(timeout_millis)
            except Exception:
                pass
            try:
                provider.shutdown()
                shutdown_ok = True
            except Exception:
                pass
            return flush_ok, shutdown_ok

        result: tuple[bool, bool] | None = None
        with anyio.move_on_after(self.shutdown_timeout_seconds) as scope:
            result = await to_thread.run_sync(flush_and_shutdown, abandon_on_cancel=True)
        if scope.cancel_called:
            logger.warning(
                "observability_shutdown_timeout",
                extra={"event": "observability_shutdown_timeout"},
            )
        elif result is not None and not all(result):
            logger.warning(
                "observability_shutdown_failed",
                extra={"event": "observability_shutdown_failed"},
            )


class RunAttemptTrace:
    """Mutable local handle for one already-started RunAttempt span."""

    def __init__(self, span: APISpan | None, trace_content: TraceContent, *, tracer: Tracer | None = None) -> None:
        self._span = span
        self._trace_content = trace_content
        self._tracer = tracer
        self._ended = False
        self._outcome: RunAttemptOutcome | None = None
        self._pending_output: tuple[str, object] | None = None

    def set_input(self, value: object, *, external: bool = False) -> None:
        self._set_content(
            "input", _MISSING_CONTENT if external else value, "external_payload" if external else "unavailable"
        )

    def remember_output(self, object_digest: str, value: object) -> None:
        if self._captures_content():
            self._pending_output = (object_digest, value)

    def _captures_content(self) -> bool:
        return (
            not self._ended
            and self._span is not None
            and self._span.is_recording()
            and self._trace_content is not TraceContent.none
        )

    def _set_content(self, prefix: str, value: object, omitted: str) -> None:
        if self._span is None or self._ended or not self._span.is_recording():
            return
        try:
            if self._trace_content is TraceContent.none:
                status = "content_disabled"
            elif value is _MISSING_CONTENT:
                status = omitted
            else:
                self._span.set_attributes(_content_attributes(prefix, value, self._trace_content))
                status = "captured"
            self._span.set_attribute(f"a13n.run_attempt.{prefix}.capture", status)
        except Exception:
            _safe_warning("observability_content_projection_failed")

    def set_disposition(self, disposition: str) -> None:
        """Keep the returned fenced decision distinct from the Attempt lifecycle."""
        if self._span is not None and not self._ended:
            record_span_metadata(self._span, {"run_attempt.disposition": disposition})

    def set_outcome(
        self,
        outcome: RunAttemptOutcome,
        *,
        output_value: object = _MISSING_CONTENT,
        output_object_digest: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        """Project only a matching authoritative durable Attempt decision."""

        if outcome not in {"succeeded", "yielded", "failed", "cancelled"}:
            raise ValueError("RunAttempt outcome is invalid")
        if failure_code is not None:
            _validate_bounded_text(
                "failure_code",
                failure_code,
                required=True,
                max_bytes=_MAX_FAILURE_CODE_BYTES,
            )
        if self._outcome is not None and self._outcome != outcome:
            raise RuntimeError("RunAttempt outcome is already set")
        self._outcome = outcome
        if self._span is None:
            return
        record_span_metadata(self._span, {"run_attempt.outcome": outcome})
        if failure_code is not None:
            self._span.set_attribute("a13n.run_attempt.failure.code", failure_code)
        omitted = "not_committed"
        if outcome != "succeeded":
            output_value = _MISSING_CONTENT
        elif output_object_digest is not None:
            pending = self._pending_output
            output_value = (
                pending[1] if pending is not None and pending[0] == output_object_digest else _MISSING_CONTENT
            )
            omitted = "external_payload"
        self._set_content("output", output_value, omitted)
        self._pending_output = None
        if outcome == "failed":
            self._span.set_status(Status(StatusCode.ERROR))

    @contextmanager
    def phase(self, name: str) -> Generator[APISpan | None]:
        """Observe one stable Service-owned execution phase."""

        if name not in _PHASE_NAMES:
            raise ValueError("Service phase name is not registered")
        if self._tracer is None or self._ended:
            yield None
            return
        if self._span is None:
            raise RuntimeError("RunAttempt root span is unavailable")
        try:
            span = self._tracer.start_span(
                name,
                context=trace.set_span_in_context(self._span, Context()),
            )
        except Exception:
            _safe_warning("observability_phase_start_failed")
            yield None
            return
        token = otel_context.attach(trace.set_span_in_context(span))
        outcome = "succeeded"
        error_type: str | None = None
        try:
            yield span
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BaseException as error:
            outcome = "failed"
            error_type = type(error).__name__[:256]
            raise
        finally:
            otel_context.detach(token)
            try:
                record_span_metadata(span, {"service.phase.outcome": outcome})
                if error_type is not None:
                    span.set_attribute("error.type", error_type)
                    span.set_status(Status(StatusCode.ERROR))
            except Exception:
                _safe_warning("observability_phase_attributes_failed")
            finally:
                try:
                    span.end()
                except Exception:
                    _safe_warning("observability_phase_end_failed")

    def mark_local_error(self) -> None:
        if self._span is not None:
            self._span.set_status(Status(StatusCode.ERROR))

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._pending_output = None
        if self._span is not None:
            self._span.end()


def build_observability_runtime(
    *,
    enabled: bool,
    trace_content: TraceContent,
    service_name: str,
    service_version: str,
    deployment_environment: str,
    service_role: str,
    service_instance_id: str | None = None,
    shutdown_timeout_seconds: float = _DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    span_exporter: SpanExporter | None = None,
) -> ObservabilityRuntime:
    """Build the one process tracing stack from Service and standard OTel settings."""

    if not enabled:
        return ObservabilityRuntime(None, trace_content, shutdown_timeout_seconds)
    if not isinstance(trace_content, TraceContent):
        raise ValueError("trace_content is invalid")
    if not isfinite(shutdown_timeout_seconds) or shutdown_timeout_seconds <= 0:
        raise ValueError("shutdown_timeout_seconds must be positive and finite")
    resource = _resource(
        service_name=service_name,
        service_version=service_version,
        deployment_environment=deployment_environment,
        service_role=service_role,
        service_instance_id=service_instance_id,
    )

    exporter = span_exporter if span_exporter is not None else _exporter_from_environment()
    delegate = _batch_span_processor(_SafeSpanExporter(exporter)) if exporter is not None else None
    provider = TracerProvider(
        sampler=_sampler_from_environment(),
        resource=resource,
        shutdown_on_exit=False,
    )
    provider.add_span_processor(_RunAttemptSpanProcessor(delegate))
    if exporter is None:
        logger.warning(
            "observability_exporter_unconfigured",
            extra={"event": "observability_exporter_unconfigured"},
        )
    return ObservabilityRuntime(provider, trace_content, shutdown_timeout_seconds)


def _resource(
    *,
    service_name: str,
    service_version: str,
    deployment_environment: str,
    service_role: str,
    service_instance_id: str | None,
) -> Resource:
    resource_attributes: dict[str, str] = {
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment.name": deployment_environment,
        "a13n.service.role": service_role,
    }
    for key, value in resource_attributes.items():
        _validate_bounded_text(key, value, required=True)
    if service_instance_id is not None:
        _validate_bounded_text("service.instance.id", service_instance_id, required=True)
        resource_attributes["service.instance.id"] = service_instance_id
    resource_attributes.update(_resource_overrides_from_environment())
    return Resource(resource_attributes)


def _resource_overrides_from_environment() -> dict[str, str]:
    overrides: dict[str, str] = {}
    configured = os.getenv("OTEL_RESOURCE_ATTRIBUTES")
    if configured:
        for item in configured.split(","):
            if "=" not in item:
                raise ValueError("OTEL_RESOURCE_ATTRIBUTES contains an invalid key-value pair")
            key, raw_value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError("OTEL_RESOURCE_ATTRIBUTES contains an invalid key-value pair")
            value = unquote(raw_value.strip())
            if key in _RESOURCE_OVERRIDE_KEYS:
                _validate_bounded_text(key, value, required=True)
                overrides[key] = value
    service_name = os.getenv("OTEL_SERVICE_NAME")
    if service_name is not None:
        _validate_bounded_text("OTEL_SERVICE_NAME", service_name, required=True)
        overrides["service.name"] = service_name
    return overrides


def _batch_span_processor(exporter: SpanExporter) -> BatchSpanProcessor:
    values = {name: _positive_integer_environment(name, default) for name, default in _BATCH_INTEGER_SETTINGS.items()}
    if values["OTEL_BSP_MAX_EXPORT_BATCH_SIZE"] > values["OTEL_BSP_MAX_QUEUE_SIZE"]:
        raise ValueError("OTEL_BSP_MAX_EXPORT_BATCH_SIZE must not exceed OTEL_BSP_MAX_QUEUE_SIZE")
    return BatchSpanProcessor(
        exporter,
        max_queue_size=values["OTEL_BSP_MAX_QUEUE_SIZE"],
        schedule_delay_millis=values["OTEL_BSP_SCHEDULE_DELAY"],
        max_export_batch_size=values["OTEL_BSP_MAX_EXPORT_BATCH_SIZE"],
        export_timeout_millis=values["OTEL_BSP_EXPORT_TIMEOUT"],
    )


def _positive_integer_environment(name: str, default: int) -> int:
    configured = os.getenv(name)
    if configured is None:
        return default
    try:
        value = int(configured)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def valid_span_link(context: SpanContext, kind: str) -> Link | None:
    """Create one bounded best-effort link without granting product authority."""

    if not context.is_valid or kind not in _LINK_KINDS:
        return None
    return Link(context, {"a13n.link.kind": kind})


def _validate_links(links: tuple[Link, ...]) -> None:
    for link in links:
        attributes = link.attributes
        if (
            not link.context.is_valid
            or attributes is None
            or set(attributes) != {"a13n.link.kind"}
            or attributes["a13n.link.kind"] not in _LINK_KINDS
        ):
            raise ValueError("RunAttempt link is invalid")


def _exporter_from_environment() -> SpanExporter | None:
    configured = os.getenv("OTEL_TRACES_EXPORTER")
    if configured is None or not configured.strip() or configured.strip().lower() == "none":
        return None
    exporters = [value.strip().lower() for value in configured.split(",") if value.strip()]
    if exporters != ["otlp"]:
        raise ValueError("OTEL_TRACES_EXPORTER must select at most one 'otlp' exporter or 'none'")
    protocol = (
        (os.getenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") or "grpc")
        .strip()
        .lower()
    )
    if protocol == "grpc":
        return GrpcOTLPSpanExporter()
    if protocol == "http/protobuf":
        return HttpOTLPSpanExporter()
    raise ValueError("OTLP trace protocol must be 'grpc' or 'http/protobuf'")


def _sampler_from_environment() -> Sampler:
    name = os.getenv("OTEL_TRACES_SAMPLER", "always_on").strip().lower()
    argument = os.getenv("OTEL_TRACES_SAMPLER_ARG")
    if name == "always_on":
        return ALWAYS_ON
    if name == "always_off":
        return ALWAYS_OFF
    if name == "parentbased_always_on":
        return ParentBased(ALWAYS_ON)
    if name == "parentbased_always_off":
        return ParentBased(ALWAYS_OFF)
    if name in {"traceidratio", "parentbased_traceidratio"}:
        try:
            ratio = float(argument) if argument is not None else 1.0
        except ValueError as error:
            raise ValueError("OTEL_TRACES_SAMPLER_ARG must be a finite ratio") from error
        if not isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError("OTEL_TRACES_SAMPLER_ARG must be between 0 and 1")
        sampler: Sampler = TraceIdRatioBased(ratio)
        return ParentBased(sampler) if name.startswith("parentbased_") else sampler
    raise ValueError("OTEL_TRACES_SAMPLER selects an unsupported head sampler")


def _content_attributes(prefix: str, value: object, content: TraceContent) -> dict[str, str]:
    if content is TraceContent.none or value is _MISSING_CONTENT:
        return {}
    if isinstance(value, str):
        return {f"{prefix}.value": value, f"{prefix}.mime_type": "text/plain"}
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{prefix} trace content must be JSON-compatible") from error
    return {f"{prefix}.value": encoded, f"{prefix}.mime_type": "application/json"}


def _validate_bounded_text(
    field_name: str,
    value: str,
    *,
    required: bool,
    max_bytes: int = _MAX_CORRELATION_BYTES,
) -> None:
    if not isinstance(value, str) or (required and not value) or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds its supported size")


def _safe_warning(event: str) -> None:
    logger.warning(event, extra={"event": event})


__all__ = [
    "INSTRUMENTATION_SCOPE",
    "ObservabilityRuntime",
    "RecoveryReason",
    "RunAttemptCorrelation",
    "RunAttemptOutcome",
    "RunAttemptTrace",
    "TraceContent",
    "build_observability_runtime",
    "observe_input",
    "observe_phase",
    "observe_phase_result",
    "remember_output",
    "valid_span_link",
]
