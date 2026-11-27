"""Inbox values shared by storage, model tools and the WebUI API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

InboxKind = Literal["result", "needs_human", "milestone"]
InboxGroup = Literal["needs_you", "updates"]
InboxTitle = Annotated[str, Field(min_length=1, max_length=120)]
InboxMessage = Annotated[str, Field(min_length=1, max_length=4096)]


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


class InboxUnreadCounts(InboxModel):
    needs_you: int = Field(ge=0)
    updates: int = Field(ge=0)


class InboxPage(InboxModel):
    items: tuple[InboxItem, ...]
    next_cursor: str | None = None
    unread_count: int = Field(ge=0)
    unread_by_group: InboxUnreadCounts


class ThreadInboxPost(InboxModel):
    item_id: str
    tool_call_id: str
    has_transcript: bool
    created_at: datetime
    read_at: datetime | None
    done_at: datetime | None


class ThreadInboxMarkReadRequest(InboxModel):
    item_ids: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = Field(min_length=1)


class InboxMarkReadRequest(InboxModel):
    group: InboxGroup


class InboxMarkDoneRequest(InboxModel):
    group: InboxGroup


class InboxItemMutation(InboxModel):
    read: bool | None = None
    done: bool | None = None


class InboxLocation(InboxModel):
    """Where the posting tool call sits in the Thread's currently selected history."""

    thread_id: str
    continuation_id: str | None = None
    turn_id: str | None = None
