"""Host-configured OpenTelemetry observation for Agent Harness runs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
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
from opentelemetry.trace import NoOpTracerProvider, Status, StatusCode, TracerProvider
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings

from a13n_harness.errors import DefinitionError

if TYPE_CHECKING:
    from opentelemetry.metrics import Histogram, UpDownCounter
    from opentelemetry.trace import Span, Tracer
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

OperationKind = Literal["recovery", "delegation", "handoff", "compaction"]
RunOutcome = Literal["completed", "suspended", "failed", "cancelled"]


class HarnessTraceLevel(StrEnum):
    """Structural detail selected for Harness and Pydantic AI traces."""

    SUMMARY = "summary"
    STANDARD = "standard"
    VERBOSE = "verbose"


class HarnessTraceContent(StrEnum):
    """Pydantic AI execution-content capture policy."""

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
    trace_level: HarnessTraceLevel = HarnessTraceLevel.SUMMARY
    trace_content: HarnessTraceContent = HarnessTraceContent.NONE

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
        if not isinstance(self.trace_level, HarnessTraceLevel):
            raise DefinitionError(
                "trace_level must be a HarnessTraceLevel value.",
                code="instrumentation_policy_invalid",
                details={"field": "trace_level"},
            )
        if not isinstance(self.trace_content, HarnessTraceContent):
            raise DefinitionError(
                "trace_content must be a HarnessTraceContent value.",
                code="instrumentation_policy_invalid",
                details={"field": "trace_content"},
            )
        if self.trace_content is not HarnessTraceContent.NONE and (
            self.tracer_provider is None or self.trace_level is HarnessTraceLevel.SUMMARY
        ):
            raise DefinitionError(
                "Non-none trace content requires standard or verbose tracing with a tracer provider.",
                code="instrumentation_policy_invalid",
                details={"field": "trace_content"},
            )

    @classmethod
    def from_environment(
        cls,
        *,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
    ) -> Self | None:
        """Resolve Harness policy from environment and selected global OTel providers."""
        level_value = environ.get(HARNESS_TRACE_LEVEL_ENV, "off")
        content_value = environ.get(HARNESS_TRACE_CONTENT_ENV, HarnessTraceContent.NONE.value)
        metrics_value = environ.get(HARNESS_METRICS_ENV, "off")
        if level_value not in {"off", *(level.value for level in HarnessTraceLevel)}:
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
            if content_value != HarnessTraceContent.NONE.value:
                raise DefinitionError(
                    "Non-none trace content requires standard or verbose tracing.",
                    code="instrumentation_policy_invalid",
                    details={"field": HARNESS_TRACE_CONTENT_ENV},
                )
            return None
        return cls(
            tracer_provider=(tracer_provider or trace.get_tracer_provider()) if level_value != "off" else None,
            meter_provider=(meter_provider or metrics.get_meter_provider()) if metrics_value == "standard" else None,
            trace_level=(HarnessTraceLevel(level_value) if level_value != "off" else HarnessTraceLevel.SUMMARY),
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
    except Exception:
        pass


@dataclass(frozen=True, slots=True)
class _ObservationActivation:
    otel_token: Token[Context] | None
    run_token: Token[_LogicalRunObservation | None]


class _ObservationRuntime:
    """Builder-compiled providers, instruments, and mandatory capabilities."""

    def __init__(self, configuration: HarnessInstrumentation | None) -> None:
        self.configuration = configuration
        self._tracer: Tracer | None = None
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
            self._tracer = configuration.tracer_provider.get_tracer(_INSTRUMENTATION_SCOPE)
        if configuration.meter_provider is not None:
            meter = configuration.meter_provider.get_meter(_INSTRUMENTATION_SCOPE)
            self._run_duration = meter.create_histogram(
                "a13n.harness.run.duration",
                unit="s",
                description="Duration of one logical Harness run",
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
            )

        pydantic_tracing = configuration.tracer_provider is not None and configuration.trace_level in (
            HarnessTraceLevel.STANDARD,
            HarnessTraceLevel.VERBOSE,
        )
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
            self.pydantic_instrumentation = Instrumentation(settings=settings)
            self.pydantic_capabilities = (
                _InstrumentationOwnershipCapability(expected_settings=settings),
                self.pydantic_instrumentation,
                _ModelRequestObservationCapability(),
                _ModelAttemptObservationCapability(),
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
        span = None
        if self._tracer is not None:
            try:
                span = self._tracer.start_span("harness.run", attributes=attributes)
            except Exception:
                pass
        observation = _LogicalRunObservation(runtime=self, span=span)
        if self._run_active is not None:
            try:
                self._run_active.add(1)
                observation._active_recorded = True
            except Exception:
                pass
        return observation


class _LogicalRunObservation:
    """One logical run span and its exactly-once metric lifecycle."""

    def __init__(self, *, runtime: _ObservationRuntime, span: Span | None) -> None:
        self._runtime = runtime
        self._span = span
        self._span_context = trace.set_span_in_context(span) if span is not None else None
        self._started_at = monotonic()
        self._model_attempts = 0
        self._finished = False
        self._active_recorded = False

    def activate(self) -> _ObservationActivation:
        return _ObservationActivation(
            otel_token=(otel_context.attach(self._span_context) if self._span_context is not None else None),
            run_token=_current_run_observation.set(self),
        )

    @staticmethod
    def deactivate(activation: _ObservationActivation) -> None:
        _current_run_observation.reset(activation.run_token)
        if activation.otel_token is not None:
            otel_context.detach(activation.otel_token)

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
    ) -> Iterator[None]:
        started_at = monotonic()
        attributes: dict[str, str] = {"a13n.operation.kind": kind}
        if capability_id is not None:
            attributes["a13n.capability.id"] = capability_id
        if operation_id is not None:
            attributes["a13n.operation.id"] = operation_id
        span = None
        configuration = self._runtime.configuration
        if (
            self._runtime._tracer is not None
            and configuration is not None
            and configuration.trace_level is HarnessTraceLevel.VERBOSE
        ):
            try:
                span = self._runtime._tracer.start_span("harness.operation", attributes=attributes)
            except Exception:
                pass
        activation = None
        if span is not None:
            activation = otel_context.attach(trace.set_span_in_context(span))
        try:
            yield
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


@contextmanager
def observe_operation(
    kind: OperationKind,
    *,
    capability_id: str | None = None,
    operation_id: str | None = None,
) -> Iterator[None]:
    """Observe a bounded Harness operation when an active run selected it."""
    observation = _current_run_observation.get()
    if observation is None:
        yield
        return
    with observation.operation(
        kind,
        capability_id=capability_id,
        operation_id=operation_id,
    ):
        yield


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
    "HarnessTraceLevel",
]
