"""Typed current-context Lark native-action contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=40_000)]
BoundedPageToken = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LarkActionBinding(_StrictModel):
    chat_id: str = Field(min_length=1, max_length=256, repr=False)
    message_id: str = Field(min_length=1, max_length=256, repr=False)
    discussion_id: str = Field(min_length=1, max_length=256, repr=False)
    chat_type: Literal["p2p", "group"]
    reply_mode: Literal["auto", "thread", "main"]


class LarkTextContent(_StrictModel):
    kind: Literal["text"] = "text"
    text: BoundedText = Field(repr=False)


class LarkPostContent(_StrictModel):
    kind: Literal["post"] = "post"
    title: str | None = Field(default=None, max_length=1024, repr=False)
    paragraphs: tuple[BoundedText, ...] = Field(min_length=1, max_length=64, repr=False)


type LarkReplyContent = Annotated[LarkTextContent | LarkPostContent, Field(discriminator="kind")]


class LarkForcedReplyArguments(_StrictModel):
    content: LarkReplyContent


class LarkAutoReplyArguments(_StrictModel):
    content: LarkReplyContent
    placement: Literal["thread", "main"] | None = None


type LarkReplyArguments = LarkForcedReplyArguments | LarkAutoReplyArguments


class LarkReplyReceipt(_StrictModel):
    message_id: str = Field(min_length=1, max_length=256, repr=False)
    root_id: str | None = Field(default=None, max_length=256, repr=False)
    thread_id: str | None = Field(default=None, max_length=256, repr=False)
    request_id: str = Field(min_length=1, max_length=128)


class LarkReplySucceeded(_StrictModel):
    kind: Literal["succeeded"] = "succeeded"
    receipt: LarkReplyReceipt


class LarkReplyOutcomeUnknown(_StrictModel):
    kind: Literal["outcome_unknown"] = "outcome_unknown"
    request_id: str = Field(min_length=1, max_length=128)


type LarkReplyOutcome = LarkReplySucceeded | LarkReplyOutcomeUnknown


class LarkListMembersArguments(_StrictModel):
    limit: int = Field(default=50, ge=1, le=100)
    page_token: BoundedPageToken | None = None


class LarkMember(_StrictModel):
    open_id: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=512)


class LarkMemberPage(_StrictModel):
    items: tuple[LarkMember, ...]
    page_token: str | None = Field(default=None, max_length=2048)
    has_more: bool


class LarkReadMessagesArguments(_StrictModel):
    scope: Literal["conversation", "discussion"]
    limit: int = Field(default=20, ge=1, le=50)
    order: Literal["asc", "desc"] = "asc"
    start_time: int | None = Field(default=None, ge=0)
    end_time: int | None = Field(default=None, ge=0)
    page_token: BoundedPageToken | None = None

    @model_validator(mode="after")
    def ordered_time_range(self) -> LarkReadMessagesArguments:
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError("start_time must not follow end_time")
        return self


class LarkMessage(_StrictModel):
    message_id: str = Field(min_length=1, max_length=256, repr=False)
    message_type: str = Field(min_length=1, max_length=128)
    sender_open_id: str | None = Field(default=None, max_length=256)
    text: str | None = Field(default=None, max_length=40_000, repr=False)
    create_time: str | None = Field(default=None, max_length=32)


class LarkMessagePage(_StrictModel):
    items: tuple[LarkMessage, ...]
    page_token: str | None = Field(default=None, max_length=2048)
    has_more: bool
