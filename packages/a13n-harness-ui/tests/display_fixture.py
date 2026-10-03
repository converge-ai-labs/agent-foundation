"""Feed presentation fixtures through the same compact fold as the Host."""

from collections.abc import Mapping
from weakref import WeakKeyDictionary

from a13n_harness_ui.interactive.rendering import StreamRenderer
from a13n_stream_protocol.display import DisplayFold, Tail

_folds: WeakKeyDictionary[StreamRenderer, dict[str, DisplayFold]] = WeakKeyDictionary()


def feed_display(
    renderer: StreamRenderer,
    event_type: str,
    payload: Mapping[str, object] | None,
    *,
    child: bool = False,
    run_id: str = "root",
    execution_id: str | None = None,
) -> None:
    if payload is None:
        renderer.ingest(None)
        return
    folds = _folds.setdefault(renderer, {})
    if run_id not in folds:
        folds[run_id] = DisplayFold(run_id, Tail(), attempt=0, page_items=128, page_bytes=262144, retain_complete=True)
    fold = folds[run_id]
    # Older presentation fixtures omit IDs or dump Python field names. Supply
    # stable fixture identities; the production fold still owns all semantics.
    aliases = {
        "message_id": "messageId",
        "tool_call_id": "toolCallId",
        "tool_call_name": "toolCallName",
        "subagent_run_id": "subagentRunId",
        "entity_id": "entityId",
    }
    event = {aliases.get(key, key): value for key, value in payload.items()}
    if event_type.startswith(("TEXT_MESSAGE", "REASONING_MESSAGE")):
        event.setdefault("messageId", "default")
    if event_type.startswith("TOOL_CALL"):
        event.setdefault("toolCallId", "unknown")
    for observed in fold.fold([{"type": event_type, **event}]):
        renderer.ingest(observed.changes, child=child, run_id=run_id, execution_id=execution_id)
    renderer.gap |= fold.assembler.gap
