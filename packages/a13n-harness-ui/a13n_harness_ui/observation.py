"""App-owned OpenTelemetry setup and bounded Host operation observations."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Collection, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from importlib.metadata import version
from math import isfinite
from typing import TYPE_CHECKING, Literal

from a13n_harness import HarnessEvent, HarnessExtensionEvent, HarnessInstrumentation
from a13n_harness.observation import SkillObservation, record_span_metadata
from a13n_logging import get_logger
from anyio import CancelScope, get_cancelled_exc_class, move_on_after, to_thread
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import INVALID_SPAN, Link, NoOpTracerProvider, ProxyTracerProvider, Span, StatusCode
from pydantic import JsonValue

if TYPE_CHECKING:
    from a13n_harness_ui.composition import ResolvedRunComposition

_SCOPE = "a13n-harness-ui"


@dataclass(frozen=True)
class _OperationObservation:
    span: Span
    instrumentation: HarnessInstrumentation
    thread_id: str
    skills: SkillObservation


_current_operation: ContextVar[_OperationObservation | None] = ContextVar("a13n_ui_observation_operation", default=None)
_MAX_CONFIGURATION_BYTES = 8192
_MODEL_NUMERIC_SETTINGS = (
    "max_tokens",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "timeout",
)
_MODEL_ENUM_SETTINGS = {
    "openai_reasoning_effort": {"none", "minimal", "low", "medium", "high", "xhigh"},
    "openai_service_tier": {"auto", "default", "flex", "priority"},
}
_TRACE_FIELDS = ("langfuse.trace.name", "langfuse.session.id", "langfuse.trace.tags")


class HarnessUiSpanProcessor(SpanProcessor):
    """Enrich existing spans for Langfuse without another execution pipeline.

    Legacy opt-in propagation for independently instrumented SDK spans. UI and
    Harness-selected spans are enriched automatically without this processor.
    Only local parent attributes are copied; no baggage or registry is used.
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
        root_thread_id: str | None = None,
        parent_thread_id: str | None = None,
        subagent_role: str | None = None,
        segment_index: int | None = None,
        resumed_from_execution_id: str | None = None,
    ) -> Iterator[Span]:
        provider = self.instrumentation.tracer_provider if self.instrumentation is not None else None
        if provider is None or isinstance(provider, (NoOpTracerProvider, ProxyTracerProvider)):
            with _operation_scope(None):
                yield INVALID_SPAN
            return
        assert self.instrumentation is not None
        tracer = self.instrumentation.get_tracer(_SCOPE)
        parent = trace.get_current_span().get_span_context()
        links = (Link(parent),) if linked and parent.is_valid else ()
        name = f"harness_ui.{kind}"
        metadata = {
            "root_thread_id": root_thread_id or thread_id,
            "parent_thread_id": parent_thread_id,
            "subagent_role": subagent_role,
            "execution_id": operation_id if kind == "subagent" else None,
            "segment_index": segment_index,
            "resumed_from_execution_id": resumed_from_execution_id,
        }
        with (
            tracer.start_as_current_span(
                name,
                context=Context() if linked else None,
                links=links,
                attributes={
                    "a13n.thread.id": thread_id,
                    "a13n.ui.operation.id": operation_id,
                    "a13n.ui.operation.kind": kind,
                    "a13n.ui.trace_content": self.instrumentation.trace_content.value,
                    "a13n.observation.name": name,
                    "a13n.observation.session.id": thread_id,
                    "a13n.observation.labels": ("harness-ui", kind),
                    **{
                        f"a13n.observation.metadata.{key}": value
                        for key, value in metadata.items()
                        if value is not None
                    },
                },
                record_exception=False,
                set_status_on_exception=False,
            ) as span,
            _operation_scope(_OperationObservation(span, self.instrumentation, thread_id, SkillObservation(span))),
        ):
            try:
                yield span
            except get_cancelled_exc_class():
                finish_operation(span, status="cancelled")
                raise
            except BaseException as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(StatusCode.ERROR)
                raise


@contextmanager
def _operation_scope(span: _OperationObservation | None) -> Iterator[None]:
    token = _current_operation.set(span)
    try:
        yield
    finally:
        _current_operation.reset(token)


def record_configuration(composition: ResolvedRunComposition, capability_ids: Collection[str]) -> None:
    """Keep one bounded, allowlisted configuration summary on this UI operation only."""
    operation = _current_operation.get()
    span = operation.span if operation is not None else None
    if span is None or not span.is_recording():
        return
    if trace.get_current_span().get_span_context().trace_id != span.get_span_context().trace_id:
        return
    try:
        summary = _configuration_summary(composition, capability_ids)
        encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_CONFIGURATION_BYTES:
            # Keep counts and primary selections when a large catalog exceeds the budget.
            for value in summary.values():
                if isinstance(value, dict) and "items" in value:
                    value["omitted"] = value["count"]
                    value["items"] = []
            summary["truncated"] = True
            encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES:
            span.set_attribute("a13n.ui.configuration", encoded)
            span.set_attribute("langfuse.observation.metadata.configuration", encoded)
    except Exception:
        # Diagnostics never fail preparation or alter the accepted composition.
        pass


def _active_operation() -> _OperationObservation | None:
    operation = _current_operation.get()
    if operation is None or not operation.span.is_recording():
        return None
    if trace.get_current_span().get_span_context().trace_id != operation.span.get_span_context().trace_id:
        return None
    return operation


@contextmanager
def phase(kind: Literal["prepare", "finalize"]) -> Iterator[Span]:
    operation = _active_operation()
    if operation is None:
        yield INVALID_SPAN
        return
    with operation.instrumentation.phase(_SCOPE, f"harness_ui.{kind}") as span:
        yield span


def record_phase_result(span: Span, *, status: str, **facts: str | bool | int) -> None:
    """Expose the phase's own result without copying the Run's response body."""
    operation = _active_operation()
    if operation is not None:
        record_span_metadata(span, {"phase.status": status, **{f"phase.{key}": value for key, value in facts.items()}})
        operation.instrumentation.record_output(span, facts, status=status)


def record_input(value: object, *, kind: str = "prompt") -> None:
    operation = _active_operation()
    if operation is not None:
        operation.instrumentation.record_input(operation.span, value, kind=kind)


def record_output(value: object, *, status: str) -> None:
    operation = _active_operation()
    if operation is not None:
        operation.instrumentation.record_output(operation.span, value, status=status)


def record_skill_event(item: object) -> None:
    operation = _active_operation()
    if operation is None or not isinstance(item, HarnessEvent) or item.thread_id != operation.thread_id:
        return
    event = item.event
    if not isinstance(event, HarnessExtensionEvent) or event.kind != "context":
        return
    payload = event.payload
    if not isinstance(payload, dict):
        return
    if payload.get("type") == "skills_catalog_resolved":
        names, count = payload.get("skills"), payload.get("skill_count")
        if isinstance(names, list) and all(isinstance(name, str) for name in names) and isinstance(count, int):
            operation.skills.catalog([name for name in names if isinstance(name, str)], count=count)
    elif payload.get("type") == "skill_accessed":
        name = payload.get("skill_name")
        if isinstance(name, str):
            operation.skills.access(name)


def _configuration_label(value: str) -> str | None:
    if "\x00" in value or "://" in value or value.startswith(("/", "\\\\")):
        return None
    try:
        return value if 0 < len(value.encode("utf-8")) <= 256 else None
    except UnicodeEncodeError:
        return None


def _configuration_items(values: Sequence[str]) -> dict[str, JsonValue]:
    items: list[JsonValue] = [label for value in values[:16] if (label := _configuration_label(value)) is not None]
    return {"count": len(values), "items": items, "omitted": len(values) - len(items)}


def _configuration_summary(
    composition: ResolvedRunComposition, capability_ids: Collection[str]
) -> dict[str, JsonValue]:
    node = composition.root
    settings: dict[str, JsonValue] = {}
    for key in _MODEL_NUMERIC_SETTINGS:
        value = node.model.settings.get(key)
        if not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value):
            settings[key] = value
    parallel = node.model.settings.get("parallel_tool_calls")
    if isinstance(parallel, bool):
        settings["parallel_tool_calls"] = parallel
    for key, allowed in _MODEL_ENUM_SETTINGS.items():
        value = node.model.settings.get(key)
        if isinstance(value, str) and value in allowed:
            settings[key] = value
    return {
        "generation_digest": composition.generation_digest,
        "thread_configuration_version": composition.thread_configuration_version,
        "package_prompt_revision": _configuration_label(composition.package_prompt_revision),
        "agent": {"id": _configuration_label(node.source_id), "kind": node.source_kind},
        "model": {
            "id": _configuration_label(node.model.model_id),
            "route": _configuration_label(node.model.route),
            "authentication_kind": node.model.authentication.kind,
            "settings": settings,
        },
        "capabilities": _configuration_items(sorted(capability_ids)),
        "harness_plugins": _configuration_items([item.plugin_id for item in node.harness_plugins]),
        "mcp_servers": _configuration_items([item.server_id for item in node.mcp_servers]),
        "tools": {"mode": "default"}
        if node.tools is None
        else {"mode": "allowlist", **_configuration_items(node.tools)},
        "subagents": _configuration_items([item.name for item in node.children]),
        "environment": {
            "profile": _configuration_label(composition.environment_profile.profile_id),
            "provider": _configuration_label(composition.environment_profile.provider_key),
            "adapter": _configuration_label(composition.environment_profile.adapter_key),
        },
        "run_extensions": _configuration_items([item.extension_id for item in composition.environment_run_extensions]),
    }


def finish_operation(span: Span, *, status: str, run_id: str | None = None, error_code: str | None = None) -> None:
    if not span.is_recording():
        return
    span.set_attribute("a13n.ui.operation.status", status)
    span.set_attribute("langfuse.observation.metadata.operation_status", status)
    if run_id is not None:
        span.set_attribute("a13n.run.id", run_id)
        span.set_attribute("langfuse.observation.metadata.run_id", run_id)
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
