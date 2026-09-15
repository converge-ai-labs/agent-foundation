"""App-owned saved assistant output validation and durable comment workflows."""

from __future__ import annotations

from datetime import datetime

from pydantic_ai.messages import ModelResponse, TextPart

from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.output_comment_models import (
    ChildOutputLocation,
    CommentEdit,
    CommentModel,
    CommentPage,
    CommentPublication,
    OutputComment,
    RootOutputLocation,
    SavedChildOutputPage,
    SavedOutputTarget,
    SavedOutputView,
)
from a13n_harness_ui.storage import LocalStore, StoredChildCheckpoint, StoredContinuation
from a13n_harness_ui.storage.comments import target_key
from a13n_harness_ui.storage.objects import ObjectRef
from a13n_harness_ui.thread_projection import _decode_cursor, _encode_cursor


class _CommentCursor(CommentModel):
    root_thread_id: str
    target_key: str | None
    newest_first: bool = False
    created_at: datetime
    comment_id: str


class _ChildOutputCursor(CommentModel):
    parent_thread_id: str
    execution_id: str
    source_id: str
    position: int


class OutputComments:
    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def publish(self, root_thread_id: str, publication: CommentPublication) -> OutputComment:
        await self._store.comments.require_root(root_thread_id)
        # Reconcile before reading a source: a committed publication survives source
        # movement, source damage, reconnect and loss of the original response.
        existing = await self._store.comments.reconcile(root_thread_id, publication)
        if existing is not None:
            return existing
        source = await self._source(root_thread_id, publication.target)
        text = await self._text(publication.target, source)
        selection = publication.selection
        if selection is not None and text[selection.start : selection.end] != selection.quote:
            raise ThreadError("Comment selection does not match saved output.", code="comment_selection_invalid")
        return await self._store.comments.publish(root_thread_id, publication, source)

    async def edit(self, root_thread_id: str, comment_id: str, edit: CommentEdit) -> OutputComment:
        await self._store.comments.require_root(root_thread_id)
        return await self._store.comments.edit(root_thread_id, comment_id, edit)

    async def delete(self, root_thread_id: str, comment_id: str, *, expected_version: int) -> None:
        await self._store.comments.require_root(root_thread_id)
        if expected_version < 1:
            raise ThreadError("Comment version must be positive.", code="comment_version_invalid")
        await self._store.comments.delete(root_thread_id, comment_id, expected_version=expected_version)

    async def get(self, root_thread_id: str, comment_id: str) -> OutputComment:
        await self._store.comments.require_root(root_thread_id)
        comment = await self._store.comments.get(root_thread_id, comment_id)
        if comment is None:
            raise ThreadError("Comment does not exist in this Thread.", code="comment_missing")
        return comment

    async def list(
        self,
        root_thread_id: str,
        *,
        target: SavedOutputTarget | None = None,
        cursor: str | None = None,
        limit: int = 20,
        newest_first: bool = False,
    ) -> CommentPage:
        await self._store.comments.require_root(root_thread_id)
        if not 1 <= limit <= 100:
            raise ThreadError("Comment page is outside supported bounds.", code="comment_page_invalid")
        key = target_key(target) if target is not None else None
        after = None
        if cursor is not None:
            decoded = _decode_cursor(cursor, _CommentCursor, code="comment_cursor_invalid")
            if (
                decoded.root_thread_id != root_thread_id
                or decoded.target_key != key
                or decoded.newest_first != newest_first
            ):
                raise ThreadError("Comment cursor belongs to another query.", code="comment_cursor_mismatch")
            after = (decoded.created_at, decoded.comment_id)
        records = await self._store.comments.list(
            root_thread_id, target=target, after=after, limit=limit + 1, newest_first=newest_first
        )
        visible = records[:limit]
        next_cursor = None
        if len(records) > limit:
            last = visible[-1]
            next_cursor = _encode_cursor(
                _CommentCursor(
                    root_thread_id=root_thread_id,
                    target_key=key,
                    newest_first=newest_first,
                    created_at=last.created_at,
                    comment_id=last.comment_id,
                )
            )
        return CommentPage(comments=visible, next_cursor=next_cursor)

    async def output(
        self, root_thread_id: str, target: SavedOutputTarget, *, offset: int = 0, limit: int = 64 * 1024
    ) -> SavedOutputView:
        if offset < 0 or not 1 <= limit <= 64 * 1024:
            raise ThreadError("Output window is outside supported bounds.", code="comment_output_page_invalid")
        await self._store.comments.require_root(root_thread_id)
        source = await self._source(root_thread_id, target)
        text = await self._text(target, source)
        if offset > len(text):
            raise ThreadError("Output offset is outside saved text.", code="comment_output_page_invalid")
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
            raise ThreadError("Child output page is outside supported bounds.", code="comment_output_page_invalid")
        head = await self._store.child_executions.get(execution_id)
        if head is None or head.parent_thread_id != parent_thread_id or head.selected_checkpoint is None:
            raise ThreadError(
                "Saved child output is unavailable in this parent scope.", code="comment_source_unavailable"
            )
        checkpoint = await self._store.objects.read_model(head.selected_checkpoint, StoredChildCheckpoint)
        if checkpoint.execution_id != execution_id or checkpoint.child_thread_id != head.child_thread_id:
            raise ThreadError("Saved child output identity is invalid.", code="comment_source_unavailable")
        position = 0
        if cursor is not None:
            decoded = _decode_cursor(cursor, _ChildOutputCursor, code="comment_cursor_invalid")
            if (
                decoded.parent_thread_id != parent_thread_id
                or decoded.execution_id != execution_id
                or decoded.source_id != head.selected_checkpoint.logical_digest
                or decoded.position < 0
            ):
                raise StoreConflictError("Child output cursor selection changed.", code="comment_target_stale")
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
            values.append(SavedOutputView(target=target, text=text, offset=0, total_characters=len(text)))
        if position > len(values):
            raise ThreadError("Child output cursor is outside saved output.", code="comment_cursor_invalid")
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
        retained = await self._store.comments.retained_source(root_thread_id, target)
        if retained is not None:
            return retained
        if isinstance(target.location, RootOutputLocation):
            thread = await self._store.threads.get(root_thread_id)
            source = thread.continuation if thread is not None else None
            if target.producing_thread_id != root_thread_id:
                raise ThreadError("Root output belongs to another Thread.", code="comment_target_invalid")
        else:
            head = await self._store.child_executions.get(target.location.execution_id)
            if head is None or head.child_thread_id != target.producing_thread_id:
                raise ThreadError("Child output identity is invalid.", code="comment_target_invalid")
            thread = await self._store.threads.get(head.child_thread_id)
            seen: set[str] = set()
            while thread is not None and thread.parent_thread_id is not None and thread.thread_id not in seen:
                seen.add(thread.thread_id)
                thread = await self._store.threads.get(thread.parent_thread_id)
            if thread is None or thread.thread_id != root_thread_id:
                raise ThreadError("Child output belongs to another Thread family.", code="comment_target_invalid")
            source = head.selected_checkpoint
        if source is None or source.logical_digest != target.source_id:
            raise StoreConflictError(
                "Saved output selection changed or is unavailable; refetch.", code="comment_target_stale"
            )
        return source

    async def _text(self, target: SavedOutputTarget, source: ObjectRef) -> str:
        location = target.location
        if isinstance(location, RootOutputLocation):
            continuation = await self._store.objects.read_model(source, StoredContinuation)
            history = continuation.harness_state.message_history
            if continuation.harness_state.thread_id == target.producing_thread_id and location.message < len(history):
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
        raise ThreadError("Target is not saved visible assistant text.", code="comment_target_invalid")
