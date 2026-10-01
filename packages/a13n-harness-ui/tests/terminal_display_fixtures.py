"""Declarative shared-block fixtures for terminal layout tests, not an event fold.

Each call supplies complete field values. Native ordering, chunking and tool
pairing are tested at the shared projector and through the real App.
"""

from weakref import WeakKeyDictionary

from a13n_stream_protocol.display import (
    BlockPut,
    DisplayBlock,
    DisplayPosition,
    DisplayScope,
    DisplaySnapshot,
    DisplayState,
    Producer,
    ScopePut,
)

_states = WeakKeyDictionary()


def state_for(renderer, run_id="root"):
    states = _states.setdefault(renderer, {})
    if run_id not in states:
        states[run_id] = DisplayState(
            DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id=run_id, generation="fixture")))
        )
    return states[run_id]


def present_block(
    renderer, key, kind, content=None, *, status="succeeded", run_id="root", child=False, execution_id=None
):
    state = state_for(renderer, run_id)
    identifier = f"{run_id}:{key}"
    previous = state.blocks.get(identifier)
    revision = previous.revision if previous is not None else 0
    values = {
        **(
            previous.content
            if previous is not None
            and not (
                status == "pending" and (content or {}).get("arguments_complete") is False and "name" in (content or {})
            )
            else {}
        ),
        **(content or {}),
    }
    operations = []
    if run_id not in state.scopes:
        operations.append(ScopePut(scope=DisplayScope(id=run_id, run_id=run_id, thread_id="thread-" + run_id)))
    operations.append(
        BlockPut(
            expected_revision=revision,
            block=DisplayBlock(
                id=identifier, scope_id=run_id, kind=kind, revision=revision + 1, status=status, content=values
            ),
        )
    )
    state.publish(operations)
    renderer.display_blocks(state, (identifier,), child=child, execution_id=execution_id)
    return state.blocks[identifier]


def present_tool(renderer, call_id="call-1", *, run_id="root", child=False, execution_id=None, status=None, **content):
    if status is None:
        previous = state_for(renderer, run_id).blocks.get(f"{run_id}:tool:{call_id}")
        status = previous.status if previous is not None else "pending"
        if "result" in content:
            status = "failed" if content.get("outcome") in {"denied", "failed"} or content.get("retry") else "succeeded"
    return present_block(
        renderer,
        f"tool:{call_id}",
        "tool_chunk",
        {"tool_call_id": call_id, **content},
        status=status,
        run_id=run_id,
        child=child,
        execution_id=execution_id,
    )


def present_text(
    renderer,
    text=None,
    *,
    message_id="default",
    kind="text",
    status="running",
    metadata=None,
    run_id="root",
    child=False,
    execution_id=None,
):
    content = {}
    if text is not None:
        content["text"] = text
    if metadata is not None:
        content["metadata"] = metadata
    return present_block(
        renderer,
        f"{kind}:{message_id}",
        kind,
        content,
        status=status,
        run_id=run_id,
        child=child,
        execution_id=execution_id,
    )


def present_context(
    renderer, operation_id, *, operation="compaction", status="running", run_id="root", child=False, **value
):
    return present_block(
        renderer,
        f"execution:{operation_id}",
        "extension",
        {
            "name": "a13n.display.context_operation",
            "value": {"operation_id": operation_id, "operation": operation, **value},
        },
        status=status,
        run_id=run_id,
        child=child,
    )


def present_summary(renderer, operation_id, text, *, kind="compaction", files=(), run_id="root", child=False):
    return present_block(
        renderer,
        f"context:{operation_id}",
        "context_summary",
        {"operation_id": operation_id, "kind": kind, "text": text, "files": list(files)},
        run_id=run_id,
        child=child,
    )


def present_native_result(renderer, part, *, run_id="root", child=False, execution_id=None):
    """Exercise the real native result projector, not a terminal result fold."""
    from a13n_stream_protocol.projector import DisplayProjector
    from pydantic_ai.messages import FunctionToolResultEvent, RetryPromptPart, ToolReturnPart

    state = state_for(renderer, run_id)
    if run_id not in state.scopes:
        state.publish((ScopePut(scope=DisplayScope(id=run_id, run_id=run_id, thread_id="thread-" + run_id)),))
    values = dict(part)
    kind = values.pop("part_kind", "tool-return")
    values.setdefault("tool_name", "tool")
    native = RetryPromptPart(**values) if kind == "retry-prompt" else ToolReturnPart(**values)
    projector = DisplayProjector(state.capture())
    delta = projector.observe(run_id, 0, FunctionToolResultEvent(native))
    if delta is not None:
        state.apply(delta)
    renderer.display_blocks(state, (f"{run_id}:tool:{native.tool_call_id}",), child=child, execution_id=execution_id)


def present_input(renderer, content, *, run_id="root"):
    from a13n_harness.content import input_request
    from a13n_stream_protocol.projector import DisplayProjector

    state = state_for(renderer, run_id)
    if run_id not in state.scopes:
        state.publish((ScopePut(scope=DisplayScope(id=run_id, run_id=run_id, thread_id="thread-" + run_id)),))
    projector = DisplayProjector(state.capture())
    delta = projector.reconcile_message(run_id, 0, input_request(content))
    if delta is not None:
        state.apply(delta)
    renderer.display_blocks(state, tuple(state.blocks))
    return state


def capture_fixture(renderer):
    """A real producer hook and shared applicator for Harness-to-terminal tests."""
    from a13n_stream_protocol.display import BlockAppend, BlocksRemove
    from a13n_stream_protocol.projector import DisplayProjector
    from a13n_stream_protocol.session import DisplayCapture

    state = DisplayState(
        DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="producer", generation="fixture")))
    )

    def receive(delta):
        state.apply(delta)
        removed = tuple(identifier for op in delta.operations if isinstance(op, BlocksRemove) for identifier in op.ids)
        surviving = renderer.remove_blocks(state, removed)
        changed = tuple(
            op.block.id if isinstance(op, BlockPut) else op.id
            for op in delta.operations
            if isinstance(op, BlockPut | BlockAppend)
        )
        renderer.display_blocks(state, tuple(dict.fromkeys((*changed, *surviving))))

    return DisplayCapture(DisplayProjector(state.capture(), publish=receive))
