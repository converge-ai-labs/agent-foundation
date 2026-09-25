"""Host-configured OpenTelemetry observation for Agent Harness runs."""

from __future__ import annotations

from asyncio import CancelledError
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from os import environ
from re import fullmatch
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Self

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.metrics import MeterProvider, NoOpMeterProvider
from opentelemetry.trace import INVALID_SPAN, NoOpTracerProvider, Span, Status, StatusCode, TracerProvider
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings

from a13n_harness._json import redact_json
from a13n_harness._tool_observation import (
    _current_tool,
    _ToolObservation,
    _ToolObservationCapability,
)
from a13n_harness._tool_observation import (
    record_tool_outcome_unknown as record_tool_outcome_unknown,
)
from a13n_harness._tool_observation import (
    set_tool_span_attributes as set_tool_span_attributes,
)
from a13n_harness._trace import _EnrichedTracer
from a13n_harness._trace_details import SkillObservation, record_content, record_span_metadata
from a13n_harness.errors import DefinitionError

if TYPE_CHECKING:
    from opentelemetry.metrics import Histogram, UpDownCounter
    from opentelemetry.trace import Tracer
    from pydantic_ai.capabilities import WrapModelRequestHandler, WrapRunHandler
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.run import AgentRunResult
    from pydantic_ai.tools import RunContext

    from a13n_harness.context import AgentContext
    from a13n_harness.identity import AgentInstanceContext

HARNESS_TRACE_LEVEL_ENV = "A13N_HARNESS_TRACE_LEVEL"
HARNESS_TRACE_CONTENT_ENV = "A13N_HARNESS_TRACE_CONTENT"
HARNESS_METRICS_ENV = "A13N_HARNESS_METRICS"

_INSTRUMENTATION_SCOPE = "a13n-harness"
_PYDANTIC_INSTRUMENTATION_VERSION = 5
_MAX_IDENTITY_ATTRIBUTE_BYTES = 1024
_MAX_OBSERVATION_NAME_BYTES = 256
_MAX_OBSERVATION_SESSION_ID_BYTES = 256
_MAX_OBSERVATION_LABELS = 16
_MAX_OBSERVATION_LABEL_BYTES = 64
_MAX_OBSERVATION_METADATA_ENTRIES = 16
_MAX_OBSERVATION_METADATA_KEY_LENGTH = 64
_MAX_OBSERVATION_METADATA_STRING_BYTES = 256
_OBSERVATION_METADATA_KEY_PATTERN = r"[a-z][a-z0-9_.-]*"
# Durations are in seconds; the OpenTelemetry default buckets are sized for milliseconds.
_RUN_SECONDS_BUCKETS = (1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)
_OPERATION_SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

OperationKind = Literal["recovery", "delegation", "handoff", "compaction", "tool_review"]
RunOutcome = Literal["completed", "suspended", "failed", "cancelled"]


class HarnessTraceContent(StrEnum):
    """Execution-content capture policy for native spans and bounded root I/O."""

    NONE = "none"
    STANDARD = "standard"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class HarnessObservationContext:
    """Bounded Host-selected grouping and metadata for one logical run span."""

    name: str | None = None
    session_id: str | None = None
    labels: tuple[str, ...] = ()
    metadata: Mapping[str, str | bool | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_observation_text("name", self.name, _MAX_OBSERVATION_NAME_BYTES)
        _validate_observation_text("session_id", self.session_id, _MAX_OBSERVATION_SESSION_ID_BYTES)

        if isinstance(self.labels, str) or not isinstance(self.labels, Sequence):
            raise DefinitionError(
                "Harness observation labels must be an ordered sequence.",
                code="observation_context_invalid",
                details={"field": "labels"},
            )
        labels = tuple(self.labels)
        if len(labels) > _MAX_OBSERVATION_LABELS:
            raise DefinitionError(
                "Harness observation labels exceed the supported count.",
                code="observation_context_invalid",
                details={"field": "labels"},
            )
        for label in labels:
            _validate_observation_text("labels", label, _MAX_OBSERVATION_LABEL_BYTES, required=True)
        if len(set(labels)) != len(labels):
            raise DefinitionError(
                "Harness observation labels must be unique.",
                code="observation_context_invalid",
                details={"field": "labels"},
            )

        if not isinstance(self.metadata, Mapping):
            raise DefinitionError(
                "Harness observation metadata must be a mapping.",
                code="observation_context_invalid",
                details={"field": "metadata"},
            )
        metadata = dict(self.metadata)
        if len(metadata) > _MAX_OBSERVATION_METADATA_ENTRIES:
            raise DefinitionError(
                "Harness observation metadata exceeds the supported entry count.",
                code="observation_context_invalid",
                details={"field": "metadata"},
            )
        for key, value in metadata.items():
            if (
                not isinstance(key, str)
                or len(key) > _MAX_OBSERVATION_METADATA_KEY_LENGTH
                or fullmatch(_OBSERVATION_METADATA_KEY_PATTERN, key) is None
            ):
                raise DefinitionError(
                    "Harness observation metadata contains an invalid key.",
                    code="observation_context_invalid",
                    details={"field": "metadata"},
                )
            _validate_observation_metadata_value(value)

        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))


def _validate_observation_text(field_name: str, value: str | None, max_bytes: int, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DefinitionError(
            f"Harness observation {field_name} is invalid.",
            code="observation_context_invalid",
            details={"field": field_name},
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        encoded = b""
    if not encoded or len(encoded) > max_bytes:
        raise DefinitionError(
            f"Harness observation {field_name} exceeds its supported size.",
            code="observation_context_invalid",
            details={"field": field_name},
        )


def _validate_observation_metadata_value(value: object) -> None:
    valid = (
        isinstance(value, bool)
        or (isinstance(value, int) and -(2**63) <= value < 2**63)
        or (isinstance(value, float) and isfinite(value))
    )
    if isinstance(value, str):
        try:
            valid = "\x00" not in value and len(value.encode("utf-8")) <= _MAX_OBSERVATION_METADATA_STRING_BYTES
        except UnicodeEncodeError:
            valid = False
    if not valid:
        raise DefinitionError(
            "Harness observation metadata contains an unsupported value.",
            code="observation_context_invalid",
            details={"field": "metadata"},
        )


@dataclass(frozen=True, slots=True)
class HarnessInstrumentation:
    """Explicit Host-owned providers and Harness observation policy."""

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    trace_content: HarnessTraceContent = HarnessTraceContent.STANDARD

    def __post_init__(self) -> None:
        if self.tracer_provider is None and self.meter_provider is None:
            raise DefinitionError(
                "HarnessInstrumentation requires at least one OpenTelemetry provider; use None to disable observation.",
                code="instrumentation_provider_missing",
            )
        if self.tracer_provider is not None and not isinstance(self.tracer_provider, TracerProvider):
            raise DefinitionError(
                "tracer_provider must be an OpenTelemetry TracerProvider or None.",
                code="instrumentation_provider_invalid",
                details={"provider": "tracer"},
            )
        if self.meter_provider is not None and not isinstance(self.meter_provider, MeterProvider):
            raise DefinitionError(
                "meter_provider must be an OpenTelemetry MeterProvider or None.",
                code="instrumentation_provider_invalid",
                details={"provider": "meter"},
            )
        if not isinstance(self.trace_content, HarnessTraceContent):
            raise DefinitionError(
                "trace_content must be a HarnessTraceContent value.",
                code="instrumentation_policy_invalid",
                details={"field": "trace_content"},
            )

    def get_tracer(self, scope: str) -> Tracer:
        """Return an enriched OTel tracer for Host operation scopes, or a no-op tracer.

        The selected provider remains unchanged and Host-owned. Use current-span
        scopes to propagate bounded correlation to nested Harness execution.
        """
        provider = self.tracer_provider or NoOpTracerProvider()
        return _EnrichedTracer(provider.get_tracer(scope))

    def record_input(self, span: Span, value: object, *, kind: str = "prompt") -> None:
        """Project only this invocation's input, subject to the selected content policy."""
        record_content(
            span,
            "input",
            value,
            include_content=self.trace_content is not HarnessTraceContent.NONE,
            kind=kind,
        )

    def record_output(self, span: Span, value: object, *, status: str) -> None:
        """Project a final result, never infer it from the last model response."""
        record_content(
            span,
            "output",
            value,
            include_content=self.trace_content is not HarnessTraceContent.NONE,
            kind=status,
        )

    @contextmanager
    def phase(self, scope: str, name: str) -> Generator[Span]:
        """A best-effort span around an existing execution boundary; no new lifecycle."""
        span = INVALID_SPAN
        token = None
        try:
            tracer = _EnrichedTracer((self.tracer_provider or NoOpTracerProvider()).get_tracer(scope))
            span, context = tracer.start_span_with_context(name, record_exception=False, set_status_on_exception=False)
            token = otel_context.attach(context)
        except Exception:
            pass
        try:
            yield span
        except BaseException as exc:
            try:
                if isinstance(exc, CancelledError):
                    record_span_metadata(span, {"phase.status": "cancelled"})
                else:
                    record_span_metadata(span, {"phase.status": "failed"})
                    span.set_attribute("error.type", type(exc).__name__)
                    span.set_status(StatusCode.ERROR)
            except Exception:
                pass
            raise
        finally:
            if token is not None:
                otel_context.detach(token)
            try:
                span.end()
            except Exception:
                pass

    @classmethod
    def from_environment(
        cls,
        *,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> Self | None:
        """Resolve Harness policy from environment and selected global OTel providers."""
        level_value = environ.get(HARNESS_TRACE_LEVEL_ENV, "off")
        content_value = environ.get(HARNESS_TRACE_CONTENT_ENV, HarnessTraceContent.STANDARD.value)
        metrics_value = environ.get(HARNESS_METRICS_ENV, "off")
        if level_value not in {"off", "verbose"}:
            raise DefinitionError(
                f"{HARNESS_TRACE_LEVEL_ENV} has an unsupported value.",
                code="instrumentation_environment_invalid",
                details={"field": HARNESS_TRACE_LEVEL_ENV},
            )
        if content_value not in {content.value for content in HarnessTraceContent}:
            raise DefinitionError(
                f"{HARNESS_TRACE_CONTENT_ENV} has an unsupported value.",
                code="instrumentation_environment_invalid",
                details={"field": HARNESS_TRACE_CONTENT_ENV},
            )
        if metrics_value not in {"off", "standard"}:
            raise DefinitionError(
                f"{HARNESS_METRICS_ENV} has an unsupported value.",
                code="instrumentation_environment_invalid",
                details={"field": HARNESS_METRICS_ENV},
            )
        if level_value == "off" and metrics_value == "off":
            return None
        selected_tracer_provider = None
        if level_value == "verbose":
            selected_tracer_provider = tracer_provider if tracer_provider is not None else trace.get_tracer_provider()
        selected_meter_provider = None
        if metrics_value == "standard":
            selected_meter_provider = meter_provider if meter_provider is not None else metrics.get_meter_provider()
        return cls(
            tracer_provider=selected_tracer_provider,
            meter_provider=selected_meter_provider,
            trace_content=HarnessTraceContent(content_value),
        )


@dataclass(frozen=True, slots=True)
class _AttemptCorrelation:
    index: int


@dataclass(frozen=True, slots=True)
class _ModelRequestCorrelation:
    trace_id: int
    span_id: int


_attempt_correlation: ContextVar[_AttemptCorrelation | None] = ContextVar(
    "a13n_harness_attempt_correlation",
    default=None,
)
_current_run_observation: ContextVar[_LogicalRunObservation | None] = ContextVar(
    "a13n_harness_current_run_observation",
    default=None,
)
_current_model_request_correlation: ContextVar[_ModelRequestCorrelation | None] = ContextVar(
    "a13n_harness_current_model_request_correlation",
    default=None,
)


class _InstrumentationOwnershipCapability(AbstractCapability[Any]):
    """Reject any run-resolved Pydantic instrumentation not selected by Harness."""

    def __init__(self, expected_settings: InstrumentationSettings | None) -> None:
        self._expected_settings = expected_settings

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: WrapRunHandler,
    ) -> AgentRunResult[Any]:
        instrumentations = [
            capability for capability in ctx.capabilities.values() if isinstance(capability, Instrumentation)
        ]
        valid = (
            not instrumentations
            if self._expected_settings is None
            else len(instrumentations) == 1 and instrumentations[0].settings is self._expected_settings
        )
        if not valid:
            raise DefinitionError(
                "Run-resolved Pydantic AI Instrumentation is reserved to HarnessBuilder.",
                code="instrumentation_owner_conflict",
                details={"source": "run_resolved"},
            )
        return await handler()


class _ModelRequestObservationCapability(AbstractCapability[Any]):
    """Mark the exact Pydantic-owned model span that may receive bounded enrichment."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(Instrumentation,))

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        del ctx
        span = trace.get_current_span()
        span_context = span.get_span_context()
        correlation = (
            _ModelRequestCorrelation(trace_id=span_context.trace_id, span_id=span_context.span_id)
            if span.is_recording() and span_context.is_valid
            else None
        )
        token = _current_model_request_correlation.set(correlation)
        try:
            return await handler(request_context)
        finally:
            _current_model_request_correlation.reset(token)


class _ModelAttemptObservationCapability(AbstractCapability[Any]):
    """Enrich the current Pydantic-owned Agent-attempt span."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(Instrumentation,))

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: WrapRunHandler,
    ) -> AgentRunResult[Any]:
        correlation = _attempt_correlation.get()
        span = trace.get_current_span()
        if correlation is not None and span.is_recording():
            try:
                span.set_attribute("a13n.model_attempt.index", correlation.index)
                span.set_attributes(_agent_instance_attributes(ctx.deps.instance))
            except Exception:
                pass
        return await handler()


def _agent_instance_attributes(instance: AgentInstanceContext) -> dict[str, str]:
    """Project the bounded identity and lineage registry for one trusted instance."""
    attributes: dict[str, str] = {}

    def add(key: str, value: str | None) -> None:
        if value is None or "\x00" in value:
            return
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            return
        if len(encoded) <= _MAX_IDENTITY_ATTRIBUTE_BYTES:
            attributes[key] = value

    add("a13n.agent.identity.issuer", instance.identity.issuer)
    add("a13n.agent.identity.subject", instance.identity.subject)
    add("a13n.agent.id", instance.identity.get_claim("agent_id"))
    add("a13n.user.id", instance.identity.get_claim("user_id"))
    add("a13n.agent.instance.id", instance.agent_instance_id)
    add("a13n.agent.parent_instance.id", instance.parent_agent_instance_id)
    add("a13n.delegation.id", instance.delegation_id)
    add("a13n.actor", instance.actor)
    return attributes


def _set_current_model_span_attributes(attributes: Mapping[str, str | float]) -> None:
    """Enrich only the model span explicitly marked by Harness-selected instrumentation."""
    correlation = _current_model_request_correlation.get()
    if correlation is None:
        return
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if (
        not span.is_recording()
        or span_context.trace_id != correlation.trace_id
        or span_context.span_id != correlation.span_id
    ):
        return
    try:
        span.set_attributes(attributes)
        span.set_attributes(
            {
                f"langfuse.observation.metadata.{key.removeprefix('a13n.').replace('.', '_')}": value
                for key, value in attributes.items()
                if key.startswith("a13n.usage.")
            }
        )
    except Exception:
        pass


@dataclass(frozen=True, slots=True)
class _ObservationActivation:
    otel_token: Token[Context] | None
    run_token: Token[_LogicalRunObservation | None]
    tool_token: Token[_ToolObservation | None]


class _ObservationRuntime:
    """Builder-compiled providers, instruments, and mandatory capabilities."""

    def __init__(self, configuration: HarnessInstrumentation | None) -> None:
        self.configuration = configuration
        self._tracer: _EnrichedTracer | None = None
        self._run_duration: Histogram | None = None
        self._run_active: UpDownCounter | None = None
        self._run_model_attempts: Histogram | None = None
        self._operation_duration: Histogram | None = None
        self.pydantic_instrumentation: Instrumentation | None = None
        ownership = _InstrumentationOwnershipCapability(expected_settings=None)
        self.pydantic_capabilities: tuple[AbstractCapability[AgentContext], ...] = (ownership,)

        if configuration is None:
            return

        if configuration.tracer_provider is not None:
            self._tracer = _EnrichedTracer(configuration.tracer_provider.get_tracer(_INSTRUMENTATION_SCOPE))
        if configuration.meter_provider is not None:
            meter = configuration.meter_provider.get_meter(_INSTRUMENTATION_SCOPE)
            self._run_duration = meter.create_histogram(
                "a13n.harness.run.duration",
                unit="s",
                description="Duration of one logical Harness run",
                explicit_bucket_boundaries_advisory=_RUN_SECONDS_BUCKETS,
            )
            self._run_active = meter.create_up_down_counter(
                "a13n.harness.run.active",
                unit="{run}",
                description="Number of active logical Harness runs",
            )
            self._run_model_attempts = meter.create_histogram(
                "a13n.harness.run.model_attempts",
                unit="{attempt}",
                description="Number of model attempts in one logical Harness run",
            )
            self._operation_duration = meter.create_histogram(
                "a13n.harness.operation.duration",
                unit="s",
                description="Duration of one independently meaningful Harness operation",
                explicit_bucket_boundaries_advisory=_OPERATION_SECONDS_BUCKETS,
            )

        pydantic_tracing = configuration.tracer_provider is not None
        if pydantic_tracing or configuration.meter_provider is not None:
            tracer_provider = configuration.tracer_provider if pydantic_tracing else NoOpTracerProvider()
            meter_provider = configuration.meter_provider or NoOpMeterProvider()
            include_content = configuration.trace_content is not HarnessTraceContent.NONE
            settings = InstrumentationSettings(
                tracer_provider=tracer_provider,
                meter_provider=meter_provider,
                include_content=include_content,
                include_binary_content=configuration.trace_content is HarnessTraceContent.FULL,
                include_model_request_parameters=configuration.trace_content is HarnessTraceContent.FULL,
                version=_PYDANTIC_INSTRUMENTATION_VERSION,
            )
            if pydantic_tracing:
                settings.tracer = _EnrichedTracer(settings.tracer)
            self.pydantic_instrumentation = Instrumentation(settings=settings)
            self.pydantic_capabilities = (
                _InstrumentationOwnershipCapability(expected_settings=settings),
                self.pydantic_instrumentation,
                _ModelRequestObservationCapability(),
                _ModelAttemptObservationCapability(),
                _ToolObservationCapability(),
            )

    def start_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        observation_context: HarnessObservationContext | None,
    ) -> _LogicalRunObservation | None:
        if self.configuration is None:
            return None
        span = None
        span_context = None
        if self._tracer is not None:
            try:
                # Preserve the existing neutral attributes as Host sampler inputs.
                # Only the additional aliases/propagation wait for is_recording().
                span, span_context = self._tracer.start_span_with_context(
                    "harness.run",
                    attributes=self._run_attributes(thread_id, run_id, instance, observation_context),
                )
            except Exception:
                pass
        observation = _LogicalRunObservation(
            runtime=self,
            span=span,
            span_context=span_context,
        )
        if self._run_active is not None:
            try:
                self._run_active.add(1)
                observation._active_recorded = True
            except Exception:
                pass
        return observation

    @staticmethod
    def _run_attributes(
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        observation_context: HarnessObservationContext | None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "a13n.thread.id": thread_id,
            "a13n.run.id": run_id,
            **_agent_instance_attributes(instance),
        }
        if observation_context is not None:
            if observation_context.name is not None:
                attributes["a13n.observation.name"] = observation_context.name
            if observation_context.session_id is not None:
                attributes["a13n.observation.session.id"] = observation_context.session_id
            if observation_context.labels:
                attributes["a13n.observation.labels"] = observation_context.labels
            attributes.update(
                {f"a13n.observation.metadata.{key}": value for key, value in observation_context.metadata.items()}
            )
        return attributes


class _LogicalRunObservation:
    """One logical run span and its exactly-once metric lifecycle."""

    def __init__(
        self,
        *,
        runtime: _ObservationRuntime,
        span: Span | None,
        span_context: Context | None,
    ) -> None:
        self._runtime = runtime
        self._span = span
        self._span_context = span_context
        self.skills = SkillObservation(span or INVALID_SPAN)
        self._started_at = monotonic()
        self._model_attempts = 0
        self._finished = False
        self._active_recorded = False

    def activate(self) -> _ObservationActivation:
        return _ObservationActivation(
            otel_token=(otel_context.attach(self._span_context) if self._span_context is not None else None),
            run_token=_current_run_observation.set(self),
            tool_token=_current_tool.set(None),
        )

    @staticmethod
    def suppress() -> _ObservationActivation:
        """Mask an enclosing Run without changing independent Host OTel context."""
        return _ObservationActivation(
            otel_token=None,
            run_token=_current_run_observation.set(None),
            tool_token=_current_tool.set(None),
        )

    @staticmethod
    def deactivate(activation: _ObservationActivation) -> None:
        _current_tool.reset(activation.tool_token)
        _current_run_observation.reset(activation.run_token)
        if activation.otel_token is not None:
            otel_context.detach(activation.otel_token)

    def record_input(self, value: object, *, kind: str) -> None:
        configuration = self._runtime.configuration
        if configuration is not None and self._span is not None:
            configuration.record_input(self._span, value, kind=kind)

    def record_output(self, value: object, *, status: str) -> None:
        configuration = self._runtime.configuration
        if configuration is not None and self._span is not None:
            configuration.record_output(self._span, value, status=status)

    def record_model_attempt(self) -> Token[_AttemptCorrelation | None]:
        index = self._model_attempts
        self._model_attempts += 1
        return _attempt_correlation.set(_AttemptCorrelation(index=index))

    @staticmethod
    def reset_model_attempt(token: Token[_AttemptCorrelation | None]) -> None:
        _attempt_correlation.reset(token)

    def finish(
        self,
        *,
        outcome: RunOutcome,
        failure_code: str | None = None,
        error: bool = False,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        duration = max(0.0, monotonic() - self._started_at)
        attributes = {"a13n.run.outcome": outcome}
        if self._span is not None:
            try:
                self._span.set_attribute("a13n.run.outcome", outcome)
                self._span.set_attribute("langfuse.observation.metadata.run_outcome", outcome)
                if failure_code is not None:
                    self._span.set_attribute("a13n.run.failure.code", failure_code)
                if error:
                    self._span.set_status(Status(StatusCode.ERROR))
                self._span.end()
            except Exception:
                pass
        if self._runtime._run_duration is not None:
            try:
                self._runtime._run_duration.record(duration, attributes)
            except Exception:
                pass
        if self._runtime._run_model_attempts is not None:
            try:
                self._runtime._run_model_attempts.record(self._model_attempts, attributes)
            except Exception:
                pass
        if self._runtime._run_active is not None and self._active_recorded:
            try:
                self._runtime._run_active.add(-1)
            except Exception:
                pass

    @contextmanager
    def operation(
        self,
        kind: OperationKind,
        *,
        capability_id: str | None = None,
        operation_id: str | None = None,
    ) -> Generator[Span]:
        started_at = monotonic()
        attributes: dict[str, str] = {"a13n.operation.kind": kind}
        if capability_id is not None:
            attributes["a13n.capability.id"] = capability_id
        if operation_id is not None:
            attributes["a13n.operation.id"] = operation_id
        span = None
        configuration = self._runtime.configuration
        span_context = None
        if self._runtime._tracer is not None and configuration is not None:
            try:
                span, span_context = self._runtime._tracer.start_span_with_context(
                    "harness.operation", attributes=attributes
                )
            except Exception:
                pass
        activation = None
        if span_context is not None:
            activation = otel_context.attach(span_context)
        observed_span = span or INVALID_SPAN
        record_span_metadata(observed_span, {"operation.kind": kind})
        try:
            yield observed_span
        except BaseException as exc:
            record_span_metadata(
                observed_span, {"operation.status": "cancelled" if isinstance(exc, CancelledError) else "failed"}
            )
            if not isinstance(exc, CancelledError):
                try:
                    observed_span.set_attribute("error.type", type(exc).__name__)
                    observed_span.set_status(StatusCode.ERROR)
                except Exception:
                    pass
            raise
        else:
            record_span_metadata(observed_span, {"operation.status": "completed"})
        finally:
            if activation is not None:
                otel_context.detach(activation)
            if span is not None:
                try:
                    span.end()
                except Exception:
                    pass
            if self._runtime._operation_duration is not None:
                try:
                    self._runtime._operation_duration.record(
                        max(0.0, monotonic() - started_at),
                        {"a13n.operation.kind": kind},
                    )
                except Exception:
                    pass


def _auxiliary_agent_capabilities() -> tuple[AbstractCapability[Any], ...]:
    """Inherit active Run telemetry without creating another logical Run or attempt.

    Attach at invocation time: auxiliary Agents can be reused across Runs with
    different providers and content policies. Native instrumentation owns their
    Agent/model spans under the current tool or operation span.
    """
    observation = _current_run_observation.get()
    instrumentation = observation._runtime.pydantic_instrumentation if observation is not None else None
    if instrumentation is None:
        return ()
    return (instrumentation, _ModelRequestObservationCapability())


@contextmanager
def observe_operation(
    kind: OperationKind,
    *,
    capability_id: str | None = None,
    operation_id: str | None = None,
) -> Generator[Span]:
    """Observe a bounded Harness operation when an active run selected it."""
    observation = _current_run_observation.get()
    if observation is None:
        yield INVALID_SPAN
        return
    with observation.operation(
        kind,
        capability_id=capability_id,
        operation_id=operation_id,
    ) as span:
        yield span


@contextmanager
def observe_phase(kind: Literal["prepare", "finalize", "skills.resolve"]) -> Generator[Span]:
    observation = _current_run_observation.get()
    configuration = observation._runtime.configuration if observation is not None else None
    if configuration is None or configuration.tracer_provider is None:
        yield INVALID_SPAN
        return
    with configuration.phase(_INSTRUMENTATION_SCOPE, f"harness.{kind}") as span:
        yield span


def observe_output(span: Span, value: object, *, status: str) -> None:
    """Record an operation-local result using the active Run's content policy."""
    observation = _current_run_observation.get()
    configuration = observation._runtime.configuration if observation is not None else None
    if configuration is not None:
        configuration.record_output(span, value, status=status)


def observe_skill_catalog(names: Sequence[str], *, count: int | None = None) -> None:
    observation = _current_run_observation.get()
    if observation is not None:
        observation.skills.catalog(names, count=count)


def observe_skill_access(name: str, *, source_id: str, tool_id: str) -> None:
    observation = _current_run_observation.get()
    if observation is None or observation._span is None or not observation._span.is_recording():
        return
    observation.skills.access(name)
    span = trace.get_current_span()
    if span.get_span_context().trace_id != observation._span.get_span_context().trace_id:
        return
    try:
        span.set_attributes(
            {"a13n.skill.name": name[:256], "a13n.skill.source_id": source_id[:256], "a13n.skill.tool_id": tool_id}
        )
    except Exception:
        pass


def _compile_observation(configuration: HarnessInstrumentation | None) -> _ObservationRuntime:
    if configuration is not None and not isinstance(configuration, HarnessInstrumentation):
        raise DefinitionError(
            "instrumentation must be a HarnessInstrumentation value or None.",
            code="instrumentation_invalid",
        )
    return _ObservationRuntime(configuration)


__all__ = [
    "HARNESS_METRICS_ENV",
    "HARNESS_TRACE_CONTENT_ENV",
    "HARNESS_TRACE_LEVEL_ENV",
    "HarnessInstrumentation",
    "HarnessObservationContext",
    "HarnessTraceContent",
    "SkillObservation",
    "record_span_metadata",
    "redact_json",
]
