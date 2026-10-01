"""Shared stream-protocol boundary for Agent Foundation surfaces."""

from importlib.metadata import version

from a13n_stream_protocol.content import tool_result_content
from a13n_stream_protocol.display import (
    BlockAppend,
    BlockPut,
    BlocksRemove,
    DisplayBlock,
    DisplayDelta,
    DisplayGap,
    DisplayPosition,
    DisplayScope,
    DisplaySnapshot,
    DisplayState,
    Producer,
    ScopePut,
)
from a13n_stream_protocol.fragments import CustomEventAssembler, fragment_custom_event
from a13n_stream_protocol.messages import AUTHORED_INPUT_EVENT_NAMES, ContentMetadata
from a13n_stream_protocol.observer import (
    AguiEventProcessor,
    AguiObservationError,
    HarnessAguiConverter,
    HarnessAguiObserver,
    HarnessAguiStreamConverter,
    HarnessAguiStreamObserver,
)
from a13n_stream_protocol.projector import DisplayProjector
from a13n_stream_protocol.session import DisplayCapture, DisplaySession

__version__ = version("a13n-stream-protocol")

__all__ = [
    "AUTHORED_INPUT_EVENT_NAMES",
    "AguiEventProcessor",
    "AguiObservationError",
    "BlockAppend",
    "BlockPut",
    "BlocksRemove",
    "ContentMetadata",
    "CustomEventAssembler",
    "DisplayBlock",
    "DisplayCapture",
    "DisplayDelta",
    "DisplayGap",
    "DisplayPosition",
    "DisplayProjector",
    "DisplayScope",
    "DisplaySession",
    "DisplaySnapshot",
    "DisplayState",
    "HarnessAguiConverter",
    "HarnessAguiObserver",
    "HarnessAguiStreamConverter",
    "HarnessAguiStreamObserver",
    "Producer",
    "ScopePut",
    "__version__",
    "fragment_custom_event",
    "tool_result_content",
]
