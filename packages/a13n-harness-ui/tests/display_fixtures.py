"""Explicit compact display fixtures for saved-state tests."""

from collections.abc import Sequence

from a13n_harness_ui.display import baseline
from a13n_stream_protocol.display import DisplayScope, DisplaySnapshot
from a13n_stream_protocol.projector import DisplayProjector
from pydantic_ai.messages import ModelMessage


def display_snapshot(
    messages: Sequence[ModelMessage] = (), *, thread_id: str = "thread_one", completed: bool = True
) -> DisplaySnapshot:
    projector = DisplayProjector(baseline("run-fixture"))
    projector.scope(DisplayScope(id="run-fixture", run_id="run-fixture", thread_id=thread_id))
    for index, message in enumerate(messages):
        projector.reconcile_message("run-fixture", index, message)
    if completed:
        projector.finish_scope("run-fixture", "completed")
    return projector.capture()


def display_capture(messages: Sequence[ModelMessage] = (), *, thread_id: str = "thread_one"):
    from a13n_stream_protocol.session import DisplayCapture

    return DisplayCapture(DisplayProjector(display_snapshot(messages, thread_id=thread_id)))
