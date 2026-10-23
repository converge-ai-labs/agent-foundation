"""Daytona timings without routes, credentials, request bodies or guest output."""

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from types import CoroutineType
from typing import Any
from uuid import uuid4

from a13n_logging import get_logger
from pydantic import JsonValue

from a13n_harness.providers.environment.native.http import NativeHTTP

_stage: ContextVar[str] = ContextVar("daytona_stage", default="transport")
_operation: ContextVar[str | None] = ContextVar("daytona_operation", default=None)
_logger = get_logger(__name__)


@contextmanager
def measure(stage: str) -> Iterator[None]:
    operation = _operation.set(_operation.get() or "dyt_" + uuid4().hex)
    parent = _stage.get()
    phase = _stage.set(stage)
    started = perf_counter()
    outcome = "interrupted"
    try:
        yield
        outcome = "ok"
    except Exception:
        outcome = "error"
        raise
    finally:
        _logger.info(
            "Daytona stage finished",
            extra={
                "daytona_operation_id": _operation.get(),
                "stage": stage,
                "parent_stage": parent,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "outcome": outcome,
            },
        )
        _stage.reset(phase)
        _operation.reset(operation)


def timed[**P, R](stage: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, CoroutineType[Any, Any, R]]]:
    def decorate(function: Callable[P, Awaitable[R]]) -> Callable[P, CoroutineType[Any, Any, R]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with measure(stage):
                return await function(*args, **kwargs)

        return wrapped

    return decorate


def _route(path: str) -> str:
    # Only fixed labels reach logs, including for unknown or malformed routes.
    if path == "api-keys/current":
        return "account"
    if path == "snapshots" or path.startswith("snapshots/"):
        return "snapshot"
    if path.endswith("/process/execute"):
        return "toolbox.exec"
    if path.startswith("sandbox"):
        for suffix in ("signed-preview-url", "preview-url", "toolbox-proxy-url", "start", "stop"):
            if path.endswith("/" + suffix):
                return "sandbox." + suffix
        return "sandbox"
    return "other"


class TimedHTTP(NativeHTTP):
    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        params: dict[str, str] | None = None,
        missing: bool = False,
        headers: dict[str, str] | None = None,
    ) -> JsonValue:
        verb = method if method in {"GET", "POST", "DELETE", "PUT", "PATCH"} else "OTHER"
        with measure("api." + verb + "." + _route(path)):
            return await super().request(method, path, body=body, params=params, missing=missing, headers=headers)
