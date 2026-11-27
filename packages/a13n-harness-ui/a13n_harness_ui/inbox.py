"""Inbox delivery: intentional items from ordinary roots, read in one human place."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from uuid import uuid4

from a13n_logging import get_logger
from sqlalchemy.exc import SQLAlchemyError

from a13n_harness_ui.inbox_models import (
    InboxGroup,
    InboxItem,
    InboxItemMutation,
    InboxKind,
    InboxLocation,
    InboxPage,
    ThreadInboxPost,
)
from a13n_harness_ui.live import HarnessUiSummaryHub, InboxNotice, RootOperationNotice
from a13n_harness_ui.notification_origin import origin_browser
from a13n_harness_ui.notifications import reply_brief
from a13n_harness_ui.storage.runtime import LocalStore
from a13n_harness_ui.thread_projection import ThreadProjectionService

_MCP_CALL_PREFIX = "mcp_"


class InboxController:
    def __init__(
        self,
        *,
        store: LocalStore,
        projections: ThreadProjectionService,
        summaries: HarnessUiSummaryHub,
        auto_mark_read: Callable[[], Awaitable[bool]],
        notify: Callable[[str, RootOperationNotice | InboxNotice], None] | None = None,
    ) -> None:
        self._store = store
        self._projections = projections
        self._summaries = summaries
        self._auto_mark_read = auto_mark_read
        self._notify = notify

    async def post(self, *, thread_id: str, tool_call_id: str, kind: InboxKind, title: str, message: str) -> InboxItem:
        item, created = await self._store.inbox.post(
            thread_id=thread_id, tool_call_id=tool_call_id, kind=kind, title=title, message=message
        )
        if not created:
            return item
        notice = InboxNotice(
            origin_browser_id=origin_browser.get(),
            item_id=item.item_id,
            kind=kind,
            title=title,
            brief=reply_brief(message) or title,
        )
        await self._summaries.publish(kind="inbox", root_thread_id=thread_id, thread_id=thread_id, inbox=notice)
        if self._notify is not None:
            self._notify(thread_id, notice)
        return item

    async def post_from_mcp(self, *, thread_id: str, kind: InboxKind, title: str, message: str) -> InboxItem:
        # A stateless MCP call has no durable Agent tool-call ID or transcript turn.
        return await self.post(
            thread_id=thread_id,
            tool_call_id=f"{_MCP_CALL_PREFIX}{uuid4().hex}",
            kind=kind,
            title=title,
            message=message,
        )

    async def page(self, *, state: Literal["open", "done"], before: str | None, limit: int) -> InboxPage:
        items, cursor = await self._store.inbox.list(state=state, before=before, limit=limit)
        counts = await self._store.inbox.unread_counts()
        return InboxPage(
            items=items, next_cursor=cursor, unread_count=counts.needs_you + counts.updates, unread_by_group=counts
        )

    async def mark_group_read(self, group: InboxGroup) -> None:
        await self._store.inbox.mark_group_read(group)
        await self._summaries.publish(kind="inbox")

    async def thread_posts(self, thread_id: str) -> tuple[ThreadInboxPost, ...]:
        return tuple(
            ThreadInboxPost(
                item_id=item_id,
                tool_call_id=call_id,
                has_transcript=not call_id.startswith(_MCP_CALL_PREFIX),
                created_at=created_at,
                read_at=read_at,
                done_at=done_at,
            )
            for item_id, call_id, created_at, read_at, done_at in await self._store.inbox.thread_posts(thread_id)
        )

    async def mark_thread_read(self, thread_id: str, *, item_ids: tuple[str, ...]) -> None:
        if await self._auto_mark_read() and await self._store.inbox.mark_thread_read(thread_id, item_ids=item_ids):
            await self._summaries.publish(kind="inbox", root_thread_id=thread_id, thread_id=thread_id)

    async def acknowledge_owner_input(self, thread_id: str, *, through: datetime) -> None:
        # The input is already accepted. An Inbox write must not turn its receipt into a rejection.
        try:
            if await self._auto_mark_read() and await self._store.inbox.mark_thread_read_through(
                thread_id, through=through
            ):
                await self._summaries.publish(kind="inbox", root_thread_id=thread_id, thread_id=thread_id)
        except SQLAlchemyError:
            get_logger(__name__).exception("Could not mark Thread Inbox items read", extra={"thread_id": thread_id})

    async def mark_group_done(self, group: InboxGroup) -> None:
        await self._store.inbox.mark_group_done(group)
        await self._summaries.publish(kind="inbox")

    async def update(self, item_id: str, mutation: InboxItemMutation) -> InboxItem:
        item = await self._store.inbox.update(item_id, read=mutation.read, done=mutation.done)
        await self._summaries.publish(kind="inbox", root_thread_id=item.thread_id, thread_id=item.thread_id)
        return item

    async def location(self, item_id: str) -> InboxLocation:
        item = await self._store.inbox.get(item_id)
        if item.tool_call_id.startswith(_MCP_CALL_PREFIX):
            return InboxLocation(thread_id=item.thread_id)
        continuation_id, turn_id = await self._projections.tool_call_turn(item.thread_id, item.tool_call_id)
        return InboxLocation(thread_id=item.thread_id, continuation_id=continuation_id, turn_id=turn_id)
