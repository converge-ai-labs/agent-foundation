"""Inbox delivery: intentional items from ordinary roots, read in one human place."""

from collections.abc import Callable
from typing import Literal
from uuid import uuid4

from a13n_harness_ui.inbox_models import InboxGroup, InboxItem, InboxItemMutation, InboxKind, InboxLocation, InboxPage
from a13n_harness_ui.live import HarnessUiSummaryHub, InboxNotice, RootOperationNotice
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
        notify: Callable[[str, RootOperationNotice | InboxNotice], None] | None = None,
    ) -> None:
        self._store = store
        self._projections = projections
        self._summaries = summaries
        self._notify = notify

    async def post(self, *, thread_id: str, tool_call_id: str, kind: InboxKind, title: str, message: str) -> InboxItem:
        item, created = await self._store.inbox.post(
            thread_id=thread_id, tool_call_id=tool_call_id, kind=kind, title=title, message=message
        )
        if not created:
            return item
        notice = InboxNotice(item_id=item.item_id, kind=kind, title=title, brief=reply_brief(message) or title)
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
