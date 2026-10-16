"""Human assistance state is public; connection credentials appear only in access replies."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mount: str = Field(min_length=1, max_length=63)
    reason: str = Field(min_length=1, max_length=2048)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{20,72}$")
    epoch: int = Field(ge=0, strict=True)


class Completion(Control):
    outcome: Literal["completed", "abandoned"] = "completed"
    note: str = Field(default="", max_length=2048)


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    tool_call_id: str | None
    cause: Literal["agent", "user"]
    environment_id: str
    mount: str
    reason: str
    state: str
    epoch: int
    controller_id: str | None
    deadline: datetime
    lease_expires_at: datetime | None
    failure: str | None
    outcome: dict | None


class Access(BaseModel):
    assistance: View
    mode: Literal["view", "control"]
    websocket_url: str = Field(repr=False)
    password: str = Field(repr=False)
    expires_in_seconds: float


def viewer_url(value: str | None) -> str:
    if value is None:
        raise ValueError("Desktop has no browser route")
    parsed = urlsplit(value)
    if (
        (
            parsed.scheme != "wss"
            and not (parsed.scheme == "ws" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        )
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Desktop route must use WSS (WS is allowed only on loopback), without credentials, query or fragment"
        )
    return value
