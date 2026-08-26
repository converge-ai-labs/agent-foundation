"""Shared stream-protocol boundary for Converge Agent surfaces."""

from importlib.metadata import version

from converge_agent_stream_protocol.observer import (
    AguiEventProcessor,
    AguiObservationError,
    HarnessAguiObserver,
)

__version__ = version("converge-agent-stream-protocol")

__all__ = [
    "AguiEventProcessor",
    "AguiObservationError",
    "HarnessAguiObserver",
    "__version__",
]
