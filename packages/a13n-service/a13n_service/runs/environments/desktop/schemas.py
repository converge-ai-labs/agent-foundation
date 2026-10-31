"""Target control metadata and separately authorized, sensitive viewer access."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class Control(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    version: int
    generation: int
    episode: int
    control: Literal["automation", "draining", "human", "returning"]
    controller_id: str | None
    controller_thread_id: str | None
    expires_at: datetime | None
    failure: str | None


class Takeover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    lease_seconds: int | None = Field(default=None, ge=1, strict=True)


class Renew(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1, strict=True)
    episode: int = Field(ge=1, strict=True)
    lease_seconds: int | None = Field(default=None, ge=1, strict=True)


class OpenViewer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["view", "control"] = "view"


class Viewer(BaseModel):
    mode: Literal["view", "control"]
    websocket_url: str = Field(repr=False)
    password: str = Field(repr=False)
    expires_at: datetime
    generation: int
    episode: int


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
        raise ValueError("Desktop route must use WSS (WS only on loopback), without credentials, query or fragment")
    return value
