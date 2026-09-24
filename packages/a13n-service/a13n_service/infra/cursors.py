"""Bounded, opaque pagination positions bound to one resource collection and the query that issued them."""

import base64
import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import JsonValue
from sqlalchemy import Select, literal, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from a13n_service.infra.errors import ServiceError


def encode(kind: str, owner: str, *position: JsonValue) -> str:
    return base64.urlsafe_b64encode(json.dumps([kind, owner, *position], separators=(",", ":")).encode()).decode()


def query_owner(*query: object) -> str:
    """The owner a filtered list binds its cursors to: its scope and filters, digested so that any filters fit the
    cursor. A cursor then continues only the query that issued it."""
    return hashlib.sha256(json.dumps(query, default=str).encode()).hexdigest()[:32]


def decode(cursor: str, kind: str, owner: str) -> list[JsonValue]:
    try:
        if len(cursor) > 2048:
            raise ValueError("oversized")
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) < 3 or value[:2] != [kind, owner]:
            raise ValueError("scope")
        return value[2:]
    except (ValueError, TypeError):
        raise ServiceError("invalid_cursor", "Invalid collection cursor") from None


def id_position(cursor: str | None, kind: str, owner: str, *, max_length: int = 72) -> str:
    if cursor is None:
        return ""
    position = decode(cursor, kind, owner)
    if len(position) != 1 or not isinstance(position[0], str) or len(position[0]) > max_length:
        raise ServiceError("invalid_cursor", "Invalid collection cursor")
    return position[0]


def key_page[T](
    items: Sequence[T], key: Callable[[T], str], *, kind: str, owner: str, cursor: str | None, limit: int
) -> tuple[list[T], str | None]:
    """One page of in-memory items already in key order, and the cursor after its last item.

    `owner` binds the cursor, such as to the query that selected the items.
    """
    after = id_position(cursor, kind, owner, max_length=256)
    remaining = [item for item in items if key(item) > after]
    page = remaining[:limit]
    return page, encode(kind, owner, key(page[-1])) if len(remaining) > limit else None


def time_position(cursor: str, kind: str, owner: str) -> tuple[datetime, str]:
    values = decode(cursor, kind, owner)
    try:
        if len(values) != 2 or not isinstance(values[0], str) or not isinstance(values[1], str):
            raise ValueError("position")
        timestamp = datetime.fromisoformat(values[0])
        if timestamp.tzinfo is None or len(values[1]) > 72:
            raise ValueError("position")
        return timestamp, values[1]
    except ValueError:
        raise ServiceError("invalid_cursor", "Invalid collection cursor") from None


async def id_page[T](
    session: AsyncSession,
    query: Select[tuple[T]],
    key: InstrumentedAttribute[str],
    *,
    kind: str,
    owner: str,
    cursor: str | None,
    limit: int,
    max_length: int = 72,
) -> tuple[Sequence[T], str | None]:
    """One page of rows in `key` order, and the cursor after its last row; keys have at most `max_length`
    characters."""
    after = id_position(cursor, kind, owner, max_length=max_length)
    rows = (await session.scalars(query.where(key > after).order_by(key).limit(limit + 1))).all()
    if len(rows) <= limit:
        return rows, None
    return rows[:limit], encode(kind, owner, getattr(rows[limit - 1], key.key))


# The (time, id) columns a keyset page is ordered by.
type KeysetOrder = tuple[InstrumentedAttribute[datetime], InstrumentedAttribute[str]]


async def keyset_page[T](
    session: AsyncSession,
    query: Select[tuple[T]] | Sequence[Select[tuple[T]]],
    order: KeysetOrder,
    *,
    kind: str,
    owner: str,
    cursor: str | None,
    limit: int,
    newest_first: bool,
) -> tuple[Sequence[T], str | None]:
    """One page of rows ordered by (time, id), and the cursor after its last row.

    Disjoint queries of one table page as their union. Each is bounded to the page in its own order before they
    merge, so an index per query keeps a page from sorting either one's whole result, as an `OR` would.
    """
    time, key = order
    position = None if cursor is None else tuple_(*map(literal, time_position(cursor, kind, owner)))

    def bounded[R](part: Select[tuple[R]], columns: KeysetOrder) -> Select[tuple[R]]:
        if position is not None:
            after = tuple_(*columns)
            part = part.where(after < position if newest_first else after > position)
        ordered = (columns[0].desc(), columns[1].desc()) if newest_first else columns
        return part.order_by(*ordered).limit(limit + 1)

    if isinstance(query, Select):
        statement = bounded(query, (time, key))
    else:
        merged = aliased(time.class_, union_all(*(bounded(part, (time, key)) for part in query)).subquery())
        statement = bounded(select(merged), (getattr(merged, time.key), getattr(merged, key.key)))
    rows = (await session.scalars(statement)).all()
    if len(rows) <= limit:
        return rows, None
    last: Any = rows[limit - 1]
    return rows[:limit], encode(kind, owner, getattr(last, time.key).isoformat(), getattr(last, key.key))
