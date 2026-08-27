"""Canonical SQLAlchemy metadata for Agent UI-owned SQLite tables."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for every Agent UI table included in the local migration history."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def agent_ui_metadata() -> MetaData:
    """Return the complete explicitly imported Agent UI metadata registry."""

    from a13n_ui.storage import models as _models  # noqa: F401

    return Base.metadata


__all__ = ["NAMING_CONVENTION", "Base", "agent_ui_metadata"]
