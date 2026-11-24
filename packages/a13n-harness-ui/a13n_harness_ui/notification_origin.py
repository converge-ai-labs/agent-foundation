"""Browser attribution for notification delivery through a submitted operation."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated

from fastapi import Header
from pydantic import Field

BrowserId = Annotated[str, Field(pattern=r"^browser_[0-9a-f]{32}$")]
origin_browser: ContextVar[str | None] = ContextVar("origin_browser", default=None)


@contextmanager
def notification_origin(browser_id: str | None) -> Iterator[None]:
    token = origin_browser.set(browser_id)
    try:
        yield
    finally:
        origin_browser.reset(token)


async def browser_submission(
    x_harness_browser: Annotated[BrowserId | None, Header()] = None,
) -> AsyncIterator[None]:
    """Apply a browser's routing label only on routes that can initiate work."""
    with notification_origin(x_harness_browser):
        yield
