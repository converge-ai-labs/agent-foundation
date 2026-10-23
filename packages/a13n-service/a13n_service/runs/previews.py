"""Bounded PostgreSQL projections of input text; never transfer the source payload to build a preview."""

from sqlalchemy import Text, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.runs.tables import InboxEntryRow, RunRow


def input_text(limit: int) -> ColumnElement[str]:
    # Both accepted messages and resume input use ordered content parts. Internal
    # delivery payloads without content contribute no text.
    content = func.coalesce(
        InboxEntryRow.payload["content"], RunRow.resume["input"]["content"], literal([], type_=JSONB)
    )
    parts = func.jsonb_array_elements(content).table_valued("value", with_ordinality="ordinal").render_derived()
    part = cast(parts.c.value, JSONB)
    text = (
        select(func.string_agg(part["text"].astext, aggregate_order_by(literal("\n"), parts.c.ordinal)))
        .where(part["type"].astext == "text")
        .correlate(InboxEntryRow, RunRow)
        .scalar_subquery()
    )
    return cast(func.left(text, limit), Text)
