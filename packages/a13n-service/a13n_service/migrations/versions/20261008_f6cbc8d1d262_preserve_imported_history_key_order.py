"""preserve imported history key order

Revision ID: f6cbc8d1d262
Revises: ce6627932e2f

The transactional type change rewrites threads under ACCESS EXCLUSIVE locking;
use a maintenance window for large installations and retain bounded DDL timeouts.
Existing JSONB history has already lost its submitted key order. Older writers
bind JSONB and can still insert normalized seeds, so upgrade all writers before
relying on preservation. Downgrade loses newly preserved key order.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6cbc8d1d262"
down_revision = "ce6627932e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_threads_imported_history"), "threads", type_="check")
    op.alter_column(
        "threads",
        "message_history",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        postgresql_using="message_history::json",
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.create_check_constraint(
        op.f("ck_threads_imported_history"), "threads", "origin = 'new' OR message_history::jsonb = '[]'::jsonb"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_thread_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.message_history::text IS DISTINCT FROM OLD.message_history::text
            THEN RAISE EXCEPTION 'thread initial history is immutable'; END IF;
            RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_threads_imported_history"), "threads", type_="check")
    op.alter_column(
        "threads",
        "message_history",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="message_history::jsonb",
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.create_check_constraint(
        op.f("ck_threads_imported_history"), "threads", "origin = 'new' OR message_history = '[]'::jsonb"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_thread_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.message_history IS DISTINCT FROM OLD.message_history
            THEN RAISE EXCEPTION 'thread initial history is immutable'; END IF;
            RETURN NEW;
        END $$
    """)
