"""Credential-free timings for one Modal desktop management dispatch."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from a13n_logging import get_logger

_operation: ContextVar[tuple[str, str] | None] = ContextVar("modal_desktop_operation", default=None)
_logger = get_logger(__name__)


@contextmanager
def measure(stage: str) -> Iterator[None]:
    context = _operation.get()
    if context is None:
        yield
        return
    started = perf_counter()
    outcome = "interrupted"
    try:
        yield
        outcome = "ok"
    except Exception:
        outcome = "error"
        raise
    finally:
        # No exception text, arguments, guest output, routes or credentials.
        _logger.info(
            "Modal desktop stage finished",
            extra={
                "desktop_operation_id": context[0],
                "desktop_operation": context[1],
                "stage": stage,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "outcome": outcome,
            },
        )


@contextmanager
def desktop_operation(operation_id: str, operation: str) -> Iterator[None]:
    token = _operation.set((operation_id, operation))
    try:
        with measure("dispatch"):
            yield
    finally:
        _operation.reset(token)
