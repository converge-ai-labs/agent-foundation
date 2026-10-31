"""Revocable handback inputs at acceptance and durable incorporation boundaries."""

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserRow, ThreadBrowserRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import InboxEntryRow

KINDS = frozenset({"browser_handback", "desktop_handback"})


class StaleNotice(ServiceError):
    """Discard an obsolete input without discarding the Thread's other work."""

    def __init__(self, entry_id: str, kind: Literal["browser", "desktop"] = "browser"):
        super().__init__(
            "conflict",
            f"{kind.capitalize()} handback is no longer current",
            {"kind": "inbox_entry", "id": entry_id, "reason": f"{kind}_notice_stale"},
        )
        self.entry_id = entry_id


async def require_notices(session: AsyncSession, runtime: Runtime, entries: Sequence[InboxEntryRow]) -> None:
    from a13n_service.runs.browsers.delivery import require_notice as browser_notice
    from a13n_service.runs.environments.desktop.delivery import require_notices as desktop_notices

    handbacks = [entry for entry in entries if entry.kind in KINDS]
    if not handbacks:
        return
    # Acceptance freezes the desired Browser before Environment mounts. Include it
    # even for a desktop-only notice, and acquire all Browser locks before any
    # Environment/control lock when a checkpoint incorporates mixed notices.
    desired = await session.scalars(
        select(ThreadBrowserRow.browser_id).where(
            ThreadBrowserRow.thread_id.in_({entry.thread_id for entry in handbacks})
        )
    )
    origins = await session.scalars(
        select(BrowserBlockedRow.browser_id).where(
            BrowserBlockedRow.id.in_(
                [entry.payload["registration_id"] for entry in handbacks if entry.kind == "browser_handback"]
            )
        )
    )
    for browser_id in sorted(set(desired) | set(origins)):
        await lock(session, BrowserRow, browser_id)
    for entry in handbacks:
        if entry.kind == "browser_handback":
            await browser_notice(session, runtime, entry)
    await desktop_notices(session, runtime, [entry for entry in handbacks if entry.kind == "desktop_handback"])


async def require_notice(session: AsyncSession, runtime: Runtime, entry: InboxEntryRow) -> None:
    await require_notices(session, runtime, [entry])


async def suppress(session: AsyncSession, thread_id: str) -> None:
    from a13n_service.runs.browsers.delivery import suppress as browsers
    from a13n_service.runs.environments.desktop.delivery import suppress as desktops

    await browsers(session, thread_id)
    await desktops(session, thread_id)
