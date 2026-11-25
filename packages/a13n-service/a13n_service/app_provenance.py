"""App ownership carried by native Service rows."""

from dataclasses import dataclass

from sqlalchemy import CheckConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import trigger
from a13n_service.infra.errors import invalid

APP_ID_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class AppProvenance:
    app_id: str
    app_context: dict[str, str] | None = None

    def __post_init__(self) -> None:
        context = self.app_context
        if context is None:
            return
        if (
            not isinstance(context, dict)
            or len(context) > 16
            or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or not 1 <= len(key) <= 64
                or len(value) > 256
                or "\0" in key
                or "\0" in value
                for key, value in context.items()
            )
        ):
            raise invalid("app_context", "must contain at most 16 bounded string pairs")
        object.__setattr__(self, "app_context", dict(context))


class AppOwned:
    app_id: Mapped[str | None] = mapped_column(String(APP_ID_MAX_LENGTH))
    app_context: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))


def app_context_requires_owner() -> CheckConstraint:
    return CheckConstraint("app_context IS NULL OR app_id IS NOT NULL", name="app_context_owner")


def guard_app_provenance(table: str, *columns: str) -> str:
    # The same generic immutability function also guards whole immutable rows.
    changed = " OR ".join(f"OLD.{column} IS DISTINCT FROM NEW.{column}" for column in columns)
    return trigger(table, "refuse_mutation", when=changed)
