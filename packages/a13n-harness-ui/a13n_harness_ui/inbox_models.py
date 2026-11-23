"""Inbox values shared by storage, model tools and the WebUI API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InboxKind = Literal["result", "needs_human", "milestone"]


class InboxModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class InboxItem(InboxModel):
    item_id: str
    thread_id: str
    tool_call_id: str
    kind: InboxKind
    title: str
    message: str
    created_at: datetime
    read_at: datetime | None = None
    done_at: datetime | None = None
    thread_title: str | None = None
    thread_archived: bool = False
    project_id: str | None = None


class InboxPage(InboxModel):
    items: tuple[InboxItem, ...]
    next_cursor: str | None = None
    unread_count: int = Field(ge=0)


class InboxItemMutation(InboxModel):
    read: bool | None = None
    done: bool | None = None


class InboxLocation(InboxModel):
    """Where the posting tool call sits in the Thread's currently selected history."""

    thread_id: str
    continuation_id: str | None = None
    turn_id: str | None = None
