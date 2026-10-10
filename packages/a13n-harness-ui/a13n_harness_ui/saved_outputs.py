"""App-owned inspection of saved root and child output, without historical pins."""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart

from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.saved_output_models import (
    ChildOutputLocation,
    RootOutputLocation,
    SavedChildOutputPage,
    SavedOutputModel,
    SavedOutputTarget,
    SavedOutputView,
)
from a13n_harness_ui.storage import LocalStore, StoredChildCheckpoint
from a13n_harness_ui.storage.objects import ObjectRef
from a13n_harness_ui.thread_projection import _decode_cursor, _encode_cursor


class _ChildOutputCursor(SavedOutputModel):
    parent_thread_id: str
    execution_id: str
    source_id: str
    position: int


class SavedOutputs:
    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def output(
        self, root_thread_id: str, target: SavedOutputTarget, *, offset: int = 0, limit: int = 64 * 1024
    ) -> SavedOutputView:
        if offset < 0 or not 1 <= limit <= 64 * 1024:
            raise ThreadError("Output window is outside supported bounds.", code="saved_output_output_page_invalid")
        root = await self._store.threads.get(root_thread_id)
        if root is None or root.parent_thread_id is not None:
            raise ThreadError("A root Thread is required.", code="thread_missing")
        source = await self._source(root_thread_id, target)
        text = await self._text(target, source)
        if offset > len(text):
            raise ThreadError("Output offset is outside saved text.", code="saved_output_output_page_invalid")
        end = min(len(text), offset + limit)
        return SavedOutputView(
            target=target,
            text=text[offset:end],
            offset=offset,
            total_characters=len(text),
            next_offset=end if end < len(text) else None,
        )

    async def child_outputs(
        self, parent_thread_id: str, execution_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> SavedChildOutputPage:
        if not 1 <= limit <= 20:
            raise ThreadError("Child output page is outside supported bounds.", code="saved_output_output_page_invalid")
        head = await self._store.child_executions.get(execution_id)
        if head is None or head.parent_thread_id != parent_thread_id or head.selected_checkpoint is None:
            raise ThreadError(
                "Saved child output is unavailable in this parent scope.", code="saved_output_source_unavailable"
            )
        checkpoint = await self._store.objects.read_model(head.selected_checkpoint, StoredChildCheckpoint)
        if checkpoint.execution_id != execution_id or checkpoint.child_thread_id != head.child_thread_id:
            raise ThreadError("Saved child output identity is invalid.", code="saved_output_source_unavailable")
        position = 0
        if cursor is not None:
            decoded = _decode_cursor(cursor, _ChildOutputCursor, code="saved_output_cursor_invalid")
            if (
                decoded.parent_thread_id != parent_thread_id
                or decoded.execution_id != execution_id
                or decoded.source_id != head.selected_checkpoint.logical_digest
                or decoded.position < 0
            ):
                raise StoreConflictError("Child output cursor selection changed.", code="saved_output_target_stale")
            position = decoded.position
        values: list[SavedOutputView] = []
        for index, activity in enumerate(checkpoint.display.activities):
            if activity.kind == "text" and activity.text is not None:
                target = SavedOutputTarget(
                    producing_thread_id=head.child_thread_id,
                    source_id=head.selected_checkpoint.logical_digest,
                    location=ChildOutputLocation(execution_id=execution_id, activity=index),
                )
                values.append(
                    SavedOutputView(target=target, text=activity.text, offset=0, total_characters=len(activity.text))
                )
        if checkpoint.display.final_answer is not None:
            text = checkpoint.display.final_answer
            target = SavedOutputTarget(
                producing_thread_id=head.child_thread_id,
                source_id=head.selected_checkpoint.logical_digest,
                location=ChildOutputLocation(execution_id=execution_id),
            )
            end = min(len(text), 64 * 1024)
            # Human inspection starts with the latest saved result, not an event log.
            values.insert(
                0,
                SavedOutputView(
                    target=target,
                    text=text[:end],
                    offset=0,
                    total_characters=len(text),
                    next_offset=end if end < len(text) else None,
                ),
            )
        if position > len(values):
            raise ThreadError("Child output cursor is outside saved output.", code="saved_output_cursor_invalid")
        end = min(len(values), position + limit)
        next_cursor = (
            _encode_cursor(
                _ChildOutputCursor(
                    parent_thread_id=parent_thread_id,
                    execution_id=execution_id,
                    source_id=head.selected_checkpoint.logical_digest,
                    position=end,
                )
            )
            if end < len(values)
            else None
        )
        return SavedChildOutputPage(
            source_id=head.selected_checkpoint.logical_digest,
            outputs=tuple(values[position:end]),
            total=len(values),
            next_cursor=next_cursor,
        )

    async def _source(self, root_thread_id: str, target: SavedOutputTarget) -> ObjectRef:
        if isinstance(target.location, RootOutputLocation):
            thread = await self._store.threads.get(root_thread_id)
            source = thread.continuation if thread is not None else None
            if target.producing_thread_id != root_thread_id:
                raise ThreadError("Root output belongs to another Thread.", code="saved_output_target_invalid")
        else:
            head = await self._store.child_executions.get(target.location.execution_id)
            if head is None or head.child_thread_id != target.producing_thread_id:
                raise ThreadError("Child output identity is invalid.", code="saved_output_target_invalid")
            thread = await self._store.threads.get(head.child_thread_id)
            seen: set[str] = set()
            while thread is not None and thread.parent_thread_id is not None and thread.thread_id not in seen:
                seen.add(thread.thread_id)
                thread = await self._store.threads.get(thread.parent_thread_id)
            if thread is None or thread.thread_id != root_thread_id:
                raise ThreadError("Child output belongs to another Thread family.", code="saved_output_target_invalid")
            source = head.selected_checkpoint
        if source is None or source.logical_digest != target.source_id:
            raise StoreConflictError(
                "Saved output selection changed or is unavailable; refetch.", code="saved_output_target_stale"
            )
        return source

    async def _text(self, target: SavedOutputTarget, source: ObjectRef) -> str:
        location = target.location
        if isinstance(location, RootOutputLocation):
            continuation = await self._store.read_continuation(target.producing_thread_id, source)
            from a13n_harness_ui.display_projection import original_text

            if continuation.harness_state.thread_id != target.producing_thread_id:
                raise ThreadError("Root output belongs to another Thread.", code="saved_output_target_invalid")
            display = continuation.display_history
            if display is not None:
                text = original_text(display, location.message, location.part)
                if text is not None:
                    return text
                raise ThreadError("Target is not saved visible assistant text.", code="saved_output_target_invalid")
            history = continuation.harness_state.message_history
            if location.message < len(history):
                message = history[location.message]
                if isinstance(message, ModelResponse) and location.part < len(message.parts):
                    part = message.parts[location.part]
                    if isinstance(part, TextPart):
                        return part.content
        else:
            checkpoint = await self._store.objects.read_model(source, StoredChildCheckpoint)
            if (
                checkpoint.execution_id == location.execution_id
                and checkpoint.child_thread_id == target.producing_thread_id
            ):
                if location.activity is None and checkpoint.display.final_answer is not None:
                    return checkpoint.display.final_answer
                if location.activity is not None and location.activity < len(checkpoint.display.activities):
                    activity = checkpoint.display.activities[location.activity]
                    if activity.kind == "text" and activity.text is not None:
                        return activity.text
        raise ThreadError("Target is not saved visible assistant text.", code="saved_output_target_invalid")
