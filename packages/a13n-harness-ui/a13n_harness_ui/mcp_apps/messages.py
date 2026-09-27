"""Single-consumption receipts for user-confirmed App messages into ordinary root input."""

from typing import Literal, Self

from mcp.types import TextContent
from pydantic import Field, model_validator

from a13n_harness_ui.surfaces import RootRunReceipt

from .context import AppContextReference
from .models import AppModel


class AppMessageRequest(AppModel):
    request_key: str = Field(min_length=1, max_length=128)
    role: Literal["user"] = "user"
    content: tuple[TextContent, ...] = Field(min_length=1, max_length=64)
    context: AppContextReference | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.model_dump_json().encode()) > 64 * 1024:
            raise ValueError("App message exceeds 64 KiB.")
        if not any(part.text.strip() for part in self.content):
            raise ValueError("App message must not be blank.")
        return self


class AppMessageReceipt(AppModel):
    request: AppMessageRequest
    view_id: str
    root_thread_id: str
    status: Literal["submitting", "accepted", "failed"]
    receipt: RootRunReceipt | None = None
    reason: str | None = None
