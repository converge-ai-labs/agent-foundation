"""Bounded enrichment of existing spans through the public OpenTelemetry API."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from json import dumps
from math import isfinite

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, SpanKind, Tracer
from opentelemetry.util._decorator import _agnosticcontextmanager
from opentelemetry.util.types import AttributeValue

_CONTEXT_KEY = otel_context.create_key("a13n.trace.metadata")
_IDENTITY_KEYS = {
    "a13n.agent.identity.issuer": "identity_issuer",
    "a13n.agent.identity.subject": "identity_subject",
    "a13n.agent.id": "agent_id",
    "a13n.user.id": "user_id",
    "a13n.agent.instance.id": "agent_instance_id",
    "a13n.agent.parent_instance.id": "parent_agent_instance_id",
    "a13n.delegation.id": "delegation_id",
    "a13n.actor": "actor",
}
_CORRELATION_KEYS = {
    **_IDENTITY_KEYS,
    "a13n.thread.id": "thread_id",
    "a13n.run.id": "run_id",
    "a13n.ui.operation.id": "operation_id",
    "a13n.ui.operation.kind": "kind",
}
_GROUPING_KEYS = {
    "a13n.observation.name": "langfuse.trace.name",
    "a13n.observation.session.id": "langfuse.session.id",
    "a13n.observation.labels": "langfuse.trace.tags",
}
_METADATA_PREFIX = "a13n.observation.metadata."


@dataclass(frozen=True)
class _TraceMetadata:
    trace_id: int
    attributes: Mapping[str, AttributeValue]


def _bounded(value: AttributeValue) -> bool:
    if isinstance(value, str):
        try:
            return "\x00" not in value and len(value.encode("utf-8")) <= 1024
        except UnicodeEncodeError:
            return False
    return (
        isinstance(value, bool)
        or (isinstance(value, int) and -(2**63) <= value < 2**63)
        or (isinstance(value, float) and isfinite(value))
    )


def _correlation(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]:
    result = {}
    metadata_count = 0
    for key, value in attributes.items():
        if key in _CORRELATION_KEYS or key in _GROUPING_KEYS:
            if key == "a13n.observation.labels":
                if not isinstance(value, str) and isinstance(value, Sequence) and len(value) <= 16:
                    if all(isinstance(label, str) and _bounded(label) for label in value):
                        result[key] = value
            elif _bounded(value):
                result[key] = value
        elif key.startswith(_METADATA_PREFIX) and len(key) <= len(_METADATA_PREFIX) + 64:
            if metadata_count < 16 and _bounded(value):
                result[key] = value
                metadata_count += 1
    return result


def _aliases(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]:
    result = {}
    for key, value in attributes.items():
        if key in _GROUPING_KEYS:
            result[_GROUPING_KEYS[key]] = value
        metadata_key = _CORRELATION_KEYS.get(key)
        if key.startswith(_METADATA_PREFIX):
            metadata_key = key.removeprefix(_METADATA_PREFIX)
        if metadata_key is not None:
            encoded = value if isinstance(value, str) else dumps(value, separators=(",", ":"))
            result[f"langfuse.observation.metadata.{metadata_key}"] = encoded
            if key.startswith(_METADATA_PREFIX):
                result[f"langfuse.trace.metadata.{metadata_key}"] = encoded
    user_id = attributes.get("a13n.user.id")
    if user_id is not None:
        result["langfuse.user.id"] = user_id
    return result


class _EnrichedTracer(Tracer):
    """Decorate a selected tracer, never mutate or own its provider.

    Metadata travels only in local OTel context, guarded by trace identity. No
    baggage, span registry, SDK-specific span access, or payload copying is used.
    """

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def start_span_with_context(
        self,
        name: str,
        context: Context | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> tuple[Span, Context]:
        span = self._tracer.start_span(
            name,
            context=context,
            kind=kind,
            attributes=attributes,
            links=links,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )
        child_context = trace.set_span_in_context(span, context)
        # A selective sampler can record descendants of an unrecorded Run.
        # Do not let those descendants fall back to the caller's identity.
        child_context = otel_context.set_value(_CONTEXT_KEY, None, child_context)
        if not span.is_recording():
            return span, child_context
        try:
            return span, self.enrich_span(span, name, context=context, attributes=attributes or {})
        except Exception:
            # Presentation must not replace execution or leave an unended span.
            return span, child_context

    def enrich_span(
        self,
        span: Span,
        name: str,
        *,
        context: Context | None = None,
        attributes: Mapping[str, AttributeValue],
    ) -> Context:
        own = attributes
        child_context = trace.set_span_in_context(span, context)
        inherited = otel_context.get_value(_CONTEXT_KEY, context)
        values: dict[str, AttributeValue] = {}
        if isinstance(inherited, _TraceMetadata) and inherited.trace_id == span.get_span_context().trace_id:
            values.update(inherited.attributes)
        if name == "harness.run":
            # A new Run must not inherit absent identity claims from its caller.
            for key in _IDENTITY_KEYS:
                values.pop(key, None)
            if values.get("a13n.thread.id") != own.get("a13n.thread.id"):
                values.pop("a13n.observation.session.id", None)
                values.pop(f"{_METADATA_PREFIX}subagent_role", None)
            values.setdefault("a13n.observation.session.id", own.get("a13n.thread.id", ""))
        # Prefer the child's metadata when the inherited bounded map is full.
        own_values = _correlation(own)
        values = _correlation({**own_values, **{key: value for key, value in values.items() if key not in own_values}})
        enrichment = {**values, **_aliases(values)}
        operation = own.get("gen_ai.operation.name")
        observation_type = None
        if name == "harness.run" or operation == "invoke_agent":
            observation_type = "agent"
        elif operation == "execute_tool":
            observation_type = "tool"
        elif operation in {"chat", "text_completion", "generate_content"}:
            observation_type = "generation"
        if observation_type is not None:
            enrichment["langfuse.observation.type"] = observation_type
        # Explicit span-local attributes take precedence, including vendor type.
        span.set_attributes({key: value for key, value in enrichment.items() if key not in own})
        child_context = otel_context.set_value(
            _CONTEXT_KEY,
            _TraceMetadata(span.get_span_context().trace_id, values),
            child_context,
        )
        return child_context

    def start_span(
        self,
        name: str,
        context: Context | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Span:
        span, _ = self.start_span_with_context(
            name,
            context,
            kind,
            attributes,
            links,
            start_time,
            record_exception,
            set_status_on_exception,
        )
        return span

    @_agnosticcontextmanager
    def start_as_current_span(
        self,
        name: str,
        context: Context | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> Iterator[Span]:
        span, child_context = self.start_span_with_context(
            name,
            context,
            kind,
            attributes,
            links,
            start_time,
            record_exception,
            set_status_on_exception,
        )
        token = otel_context.attach(child_context)
        try:
            with trace.use_span(
                span,
                end_on_exit=end_on_exit,
                record_exception=record_exception,
                set_status_on_exception=set_status_on_exception,
            ):
                yield span
        finally:
            otel_context.detach(token)
