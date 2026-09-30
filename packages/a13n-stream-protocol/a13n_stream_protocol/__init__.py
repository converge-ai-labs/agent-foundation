"""Shared stream-protocol boundary for Agent Foundation surfaces."""

from importlib.metadata import version

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
from a13n_stream_protocol.messages import ContentMetadata
from a13n_stream_protocol.observer import (
    AguiEventProcessor,
    AguiObservationError,
    HarnessAguiConverter,
    HarnessAguiObserver,
)
from a13n_stream_protocol.projector import DisplayProjector
from a13n_stream_protocol.session import DisplayCapture, DisplaySession

__version__ = version("a13n-stream-protocol")

__all__ = [
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
    "Producer",
    "ScopePut",
    "__version__",
    "fragment_custom_event",
]
