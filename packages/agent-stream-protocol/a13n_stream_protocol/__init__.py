"""Shared stream-protocol boundary for Agent Foundation surfaces."""

from importlib.metadata import version

from a13n_stream_protocol.observer import (
    AguiEventProcessor,
    AguiObservationError,
    HarnessAguiObserver,
)

__version__ = version("a13n-stream-protocol")

__all__ = [
    "AguiEventProcessor",
    "AguiObservationError",
    "HarnessAguiObserver",
    "__version__",
]
