"""Labels: a bounded free-form string map on heads, sessions, threads and runs, filterable on list."""

from typing import Annotated, Any

from pydantic import Field, StringConstraints
from sqlalchemy import ColumnElement, true

from a13n_service.infra.errors import invalid

MAX_KEY_CHARS = 128
MAX_VALUE_CHARS = 128

_Key = Annotated[str, StringConstraints(min_length=1, max_length=MAX_KEY_CHARS)]
Labels = Annotated[dict[_Key, Annotated[str, StringConstraints(max_length=MAX_VALUE_CHARS)]], Field(max_length=32)]


def label_filter(column: Any, selectors: list[str]) -> ColumnElement[bool]:
    """`?label=k:v` selectors on a JSONB labels column; every selector must match."""
    if len(selectors) > 8:
        raise invalid("label", "at most 8 label selectors")
    wanted: dict[str, str] = {}
    for selector in selectors:
        key, separator, value = selector.partition(":")
        if not separator or not key or len(key) > MAX_KEY_CHARS or len(value) > MAX_VALUE_CHARS:
            raise invalid("label", "selectors are key:value")
        wanted[key] = value
    return column.contains(wanted) if wanted else true()
