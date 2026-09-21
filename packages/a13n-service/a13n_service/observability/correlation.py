"""Task-local correlation events shared by Service domain boundaries."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar

_run_acceptance_observer: ContextVar[Callable[[str], None] | None] = ContextVar(
    "a13n_service_run_acceptance_observer", default=None
)


@contextmanager
def bind_run_acceptance_observer(observer: Callable[[str], None]) -> Generator[None]:
    token = _run_acceptance_observer.set(observer)
    try:
        yield
    finally:
        _run_acceptance_observer.reset(token)


def publish_run_acceptance(run_id: str) -> None:
    observer = _run_acceptance_observer.get()
    if observer is not None:
        observer(run_id)


__all__ = ["bind_run_acceptance_observer", "publish_run_acceptance"]
