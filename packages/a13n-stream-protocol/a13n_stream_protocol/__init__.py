"""Shared stream-protocol boundary for Agent Foundation surfaces."""
# `_EXPORTS` owns the surface; these imports serve type checkers.

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from a13n_stream_protocol.content import tool_result_content
    from a13n_stream_protocol.fragments import CustomEventAssembler, fragment_custom_event
    from a13n_stream_protocol.messages import AUTHORED_INPUT_EVENT_NAMES, ContentMetadata
    from a13n_stream_protocol.observer import (
        AguiEventProcessor,
        AguiObservationError,
        HarnessAguiObserver,
        HarnessAguiStreamObserver,
    )

__version__ = version("a13n-stream-protocol")

_EXPORTS = {
    "content": ("tool_result_content",),
    "fragments": ("CustomEventAssembler", "fragment_custom_event"),
    "messages": ("AUTHORED_INPUT_EVENT_NAMES", "ContentMetadata"),
    "observer": ("AguiEventProcessor", "AguiObservationError", "HarnessAguiObserver", "HarnessAguiStreamObserver"),
}

__all__ = [
    "AUTHORED_INPUT_EVENT_NAMES",
    "AguiEventProcessor",
    "AguiObservationError",
    "ContentMetadata",
    "CustomEventAssembler",
    "HarnessAguiObserver",
    "HarnessAguiStreamObserver",
    "__version__",
    "fragment_custom_event",
    "tool_result_content",
]


def __getattr__(name: str) -> Any:
    for module, names in _EXPORTS.items():
        if name in names:
            value = getattr(import_module(f".{module}", __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
