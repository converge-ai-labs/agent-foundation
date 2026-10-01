"""Shared stream-protocol boundary for Agent Foundation surfaces."""

from importlib.metadata import version

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
