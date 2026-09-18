"""Bounded cancellation protection for Memory-owned resource teardown."""

from collections.abc import Iterator
from contextlib import contextmanager

from a13n_logging import get_logger
from anyio import move_on_after

_logger = get_logger(__name__)


@contextmanager
def bounded_cleanup() -> Iterator[None]:
    with move_on_after(1, shield=True) as scope:
        yield
    if scope.cancel_called:
        _logger.warning("Memory resource cleanup timed out")
