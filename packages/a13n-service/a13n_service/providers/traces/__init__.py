"""The trace backend contract: where Harness spans are exported, and a read-only span query against them.

Backends report spans as they stored them; nothing they return is trusted for tenancy, so callers check every
span's correlation attributes themselves. Every read is bounded by a deadline and a response size, and any
backend failure is the one `unavailable` error naming the backend.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar, Literal, Protocol

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http

logger = get_logger(__name__)

# One page of spans; the Harness bounds each span's recorded input and output to 8 KiB.
MAX_RESPONSE_BYTES = 8388608


TraceBackendType = Literal["langfuse", "logfire"]


class SpanEvent(BaseModel):
    """Something the span recorded at one moment, such as an exception."""

    model_config = ConfigDict(frozen=True)

    name: str
    timestamp: datetime
    attributes: dict[str, JsonValue]


class SpanLink(BaseModel):
    """Another span this one relates to, in its own trace or another."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    attributes: dict[str, JsonValue]


class InstrumentationScope(BaseModel):
    """The instrumentation library that recorded the span."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str | None


class Span(BaseModel):
    """One span as the backend stored it; `attributes` are its OpenTelemetry attributes.

    Fields a backend does not keep are null or empty.
    """

    model_config = ConfigDict(frozen=True)

    trace_id: str
    id: str
    parent_id: str | None
    name: str
    # The observation kind the Harness assigns: agent, generation, tool, or span for anything else.
    kind: str
    started_at: datetime
    ended_at: datetime | None
    status: Literal["ok", "error"]
    status_message: str | None
    # The backend's severity name in lower case, such as Langfuse `default` or `error`, or Logfire `info`.
    level: str | None
    model: str | None
    usage: dict[str, int]
    cost_usd: Decimal | None
    input: JsonValue
    output: JsonValue
    attributes: dict[str, JsonValue]
    # The attributes of the process that exported the span, such as `service.name`.
    resource_attributes: dict[str, JsonValue]
    scope: InstrumentationScope | None
    events: list[SpanEvent]
    links: list[SpanLink]
    # The span's trace in the backend's own UI.
    source_url: str | None


class SpanPage(BaseModel):
    items: list[Span]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SpanQuery:
    """Spans started in [started_after, started_before) that carry every one of `attributes` exactly."""

    attributes: Mapping[str, str]
    started_after: datetime
    started_before: datetime
    limit: int
    trace_id: str | None = None
    # Only trace roots: one per attempt, since every attempt's Harness run starts its own trace.
    roots: bool = False
    cursor: str | None = None


class TraceProvider(Protocol):
    """One configured trace backend: its OTLP/HTTP export target and its span query."""

    type: ClassVar[TraceBackendType]

    @property
    def otlp_endpoint(self) -> str: ...

    @property
    def otlp_headers(self) -> dict[str, str]: ...

    async def query(self, query: SpanQuery) -> SpanPage: ...


def unavailable(provider: str) -> ServiceError:
    return ServiceError("unavailable", "The trace backend is unavailable", {"dependency": f"trace:{provider}"})


async def read_json(
    provider: str,
    method: Literal["GET", "POST"],
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
    body: JsonValue = None,
) -> JsonValue:
    """Send one read under a deadline and the response bound, returning the decoded JSON body."""
    policy = EndpointPolicy()
    try:
        with anyio.fail_after(timeout):
            async with open_http(policy, timeout=timeout, max_bytes=MAX_RESPONSE_BYTES) as client:
                response = await client.request(method, url, headers=headers, params=params, json=body)
    except (httpx2.HTTPError, ServiceError, TimeoutError, ValueError) as error:
        logger.warning(
            "Trace backend query failed",
            extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
        )
        raise unavailable(provider) from error
    if response.status_code != 200:
        logger.warning("Trace backend refused a query", extra={"status": response.status_code})
        raise unavailable(provider)
    try:
        return json.loads(response.content)
    except (ValueError, RecursionError) as error:
        raise unavailable(provider) from error


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_json_text(value: JsonValue) -> JsonValue:
    """JSON text as structure, any other value as it is: the Harness and Pydantic AI record span content,
    and backends may return attribute maps, as JSON text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return value
    return value


def parse_json_map(value: JsonValue) -> dict[str, JsonValue]:
    """An attribute map, sent as an object or as JSON text; anything else is empty."""
    decoded = parse_json_text(value)
    return dict(decoded) if isinstance(decoded, dict) else {}
