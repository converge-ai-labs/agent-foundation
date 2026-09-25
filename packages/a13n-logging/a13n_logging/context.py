"""Fields bound to the current context and added to every record the configured handlers emit."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_FIELDS: ContextVar[Mapping[str, object]] = ContextVar("a13n_logging_fields", default=MappingProxyType({}))


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Add `fields` to every record logged in this context until the block exits.

    Tasks started inside the block inherit the fields; concurrent tasks never see each other's. An inner block
    adds to the outer one's fields and replaces a field of the same name.
    """
    token = _FIELDS.set(MappingProxyType({**_FIELDS.get(), **fields}))
    try:
        yield
    finally:
        _FIELDS.reset(token)


class ContextFilter(logging.Filter):
    """Add the bound fields to a record. A field the call passes in `extra`, or a standard record attribute of the
    same name, wins."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in _FIELDS.get().items():
            if name not in record.__dict__:
                setattr(record, name, value)
        return True
