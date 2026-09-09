"""Readable resource keys and atomic allocation within database uniqueness scopes."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from typing import Annotated, Protocol

from pydantic import AfterValidator, StringConstraints
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.storage import is_unique_conflict

RESOURCE_KEY_MAX_LENGTH = 64
_RANDOM_ATTEMPTS = 8
_RESERVED_KEYS = frozenset(
    {
        "api",
        "assets",
        "confirm-email",
        "connector-setup",
        "forgot-password",
        "invitations",
        "login",
        "new",
        "reset-password",
        "settings",
    }
)
_KEY_CONSTRAINTS = {
    "uq_organizations_key": "organizations.key",
    "uq_workspaces_organization_key": "workspaces.organization_id, workspaces.key",
    "uq_agents_workspace_key": "agents.workspace_id, agents.key",
}


def _unreserved(value: str) -> str:
    if value in _RESERVED_KEYS:
        raise ValueError("key is reserved for application navigation")
    return value


ResourceKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=RESOURCE_KEY_MAX_LENGTH),
    AfterValidator(_unreserved),
]


def key_candidates(name: str, prefix: str) -> Iterator[str]:
    """Try the readable name once, then bounded four-character hex suffixes."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:RESOURCE_KEY_MAX_LENGTH].rstrip("-")
    if base and base not in _RESERVED_KEYS:
        yield base
    base = (base or prefix)[: RESOURCE_KEY_MAX_LENGTH - 5].rstrip("-")
    for _ in range(_RANDOM_ATTEMPTS):
        yield f"{base}-{secrets.token_hex(2)}"


class _KeyedRecord(Protocol):
    name: Mapped[str]
    key: Mapped[str]


def _is_key_conflict(error: IntegrityError) -> bool:
    return any(
        is_unique_conflict(error, constraint=constraint, sqlite_columns=columns)
        for constraint, columns in _KEY_CONSTRAINTS.items()
    )


def _key_conflict() -> ApplicationError:
    return ApplicationError("resource_key_conflict", "This key is already in use.", category=ErrorCategory.conflict)


async def insert_with_key(
    session: AsyncSession, record: _KeyedRecord, *, prefix: str, requested: str | None = None
) -> None:
    """Insert a new resource; retry only generated-key uniqueness conflicts.

    The caller owns the outer transaction. Savepoint rollback removes a failed
    candidate without discarding authorization, idempotency, or related writes.
    """
    candidates = iter((requested,)) if requested is not None else key_candidates(record.name, prefix)
    for candidate in candidates:
        record.key = candidate
        try:
            async with session.begin_nested():
                session.add(record)
                await session.flush()
            return
        except IntegrityError as error:
            if not _is_key_conflict(error):
                raise
            if requested is not None:
                raise _key_conflict() from error
    raise ApplicationError(
        "resource_key_exhausted",
        "Could not allocate a free key. Choose an explicit key or try again.",
        category=ErrorCategory.conflict,
    )


async def flush_key_change(session: AsyncSession) -> None:
    """Surface manual key conflicts without changing the requested key."""
    try:
        await session.flush()
    except IntegrityError as error:
        if _is_key_conflict(error):
            raise _key_conflict() from error
        raise
