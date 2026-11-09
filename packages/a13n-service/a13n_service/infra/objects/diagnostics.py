"""Bounded S3 timing summaries; never retain object names, URLs or payloads."""

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from time import monotonic
from typing import Any

from aiobotocore.httpsession import AIOHTTPSession
from aiohttp import TraceConfig

CURRENT: ContextVar["Operation | None"] = ContextVar("s3_operation", default=None)
BUCKETS_MS = (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


class Operation:
    def __init__(self):
        self.started = monotonic()
        self.phase = "sdk_request"
        self.milliseconds: dict[str, float] = defaultdict(float)
        self.active: dict[str, float] = {}
        self.attempts = 0
        self.status: int | None = None
        self.payload_bytes: int | None = None

    def start(self, name: str) -> None:
        self.active[name] = monotonic()

    def end(self, name: str) -> None:
        started = self.active.pop(name, None)
        if started is not None:
            self.milliseconds[name] += (monotonic() - started) * 1000

    @contextmanager
    def timing(self, name: str):
        self.phase = name
        self.start(name)
        try:
            yield
        finally:
            self.end(name)

    def fields(self) -> dict[str, Any]:
        elapsed = dict(self.milliseconds)
        now = monotonic()
        for name, started in self.active.items():
            elapsed[name] = elapsed.get(name, 0) + (now - started) * 1000
        return dict(
            duration_ms=(now - self.started) * 1000,
            phase=self.phase,
            phase_milliseconds=elapsed,
            http_attempts=self.attempts,
            http_status=self.status,
            payload_bytes=self.payload_bytes,
        )


class Diagnostics:
    def __init__(self):
        self.operations: dict[str, dict[str, Any]] = {}

    def record(self, operation: str, outcome: str, fields: dict[str, Any]) -> None:
        row = self.operations.setdefault(
            operation,
            dict(
                calls=0,
                outcomes={},
                milliseconds=0.0,
                max_milliseconds=0.0,
                histogram=[0] * (len(BUCKETS_MS) + 1),
                phase_milliseconds={},
                http_attempts=0,
                payload_bytes=0,
                max_payload_bytes=0,
            ),
        )
        row["calls"] += 1
        row["outcomes"][outcome] = row["outcomes"].get(outcome, 0) + 1
        elapsed = fields["duration_ms"]
        row["milliseconds"] += elapsed
        row["max_milliseconds"] = max(row["max_milliseconds"], elapsed)
        index = next((i for i, bound in enumerate(BUCKETS_MS) if elapsed <= bound), len(BUCKETS_MS))
        row["histogram"][index] += 1
        for phase, value in fields["phase_milliseconds"].items():
            row["phase_milliseconds"][phase] = row["phase_milliseconds"].get(phase, 0) + value
        row["http_attempts"] += fields["http_attempts"]
        size = fields["payload_bytes"] or 0
        row["payload_bytes"] += size
        row["max_payload_bytes"] = max(row["max_payload_bytes"], size)

    def snapshot(self) -> dict[str, Any]:
        return dict(histogram_bounds_ms=list(BUCKETS_MS), operations=deepcopy(self.operations))


class ObjectTrace(TraceConfig):
    """Identifies this observer without retaining a client or operation."""


def trace_config() -> TraceConfig:
    trace = ObjectTrace()
    for name in ("connection_queued", "connection_create", "dns_resolvehost", "request"):

        async def start(_session, _context, _params, name=name):
            if (operation := CURRENT.get()) is not None:
                operation.start(name)
                if name == "request":
                    operation.attempts += 1

        async def end(_session, _context, params, name=name):
            if (operation := CURRENT.get()) is not None:
                operation.end(name)
                if name == "request":
                    operation.status = params.response.status

        getattr(trace, "on_" + name + "_start").append(start)
        getattr(trace, "on_" + name + "_end").append(end)

    async def failed(_session, _context, _params):
        if (operation := CURRENT.get()) is not None:
            # Close incomplete phases before a possible SDK retry starts.
            for name in tuple(operation.active):
                if name not in ("sdk_request", "body_read"):
                    operation.end(name)

    trace.on_request_exception.append(failed)
    trace.freeze()
    return trace


class ObservedSession(AIOHTTPSession):
    """Keep the SDK transport and pool; attach aiohttp's public trace hooks once."""

    async def _get_session(self, proxy_url):
        session = await super()._get_session(proxy_url)
        # The SDK creates sessions before returning them; no request can use this
        # new session between the return and this synchronous installation.
        if not any(isinstance(trace, ObjectTrace) for trace in session.trace_configs):
            session.trace_configs.append(trace_config())
        return session
