"""remove legacy service toolsets

Revision ID: fed344f2d755
Revises: 4d51a94777d9

Removes the configuration, traces and findings toolsets, which platform tools replace, from stored agent
revisions and from the toolset overrides of run and inbox options. A rewritten revision also gets the digest of
its new configuration. These rows are otherwise immutable, so each table's triggers are disabled only around its
update, inside the migration transaction; the rewrite changes no identity, state or pointer they guard, and leaves
no deferred check pending. The updates lock only rows that name a removed toolset. Older builds fill a missing
toolset with its defaults, so they still read the rewritten rows; downgrade changes nothing.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "fed344f2d755"
down_revision = "4d51a94777d9"
branch_labels = None
depends_on = None

_REMOVED = ("configuration", "traces", "findings")
_NAMES_REMOVED = "'{configuration,traces,findings}'::text[]"


@contextmanager
def _unguarded(table: str) -> Iterator[None]:
    op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    yield
    op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def upgrade() -> None:
    bind = op.get_bind()
    rewrite = sa.text("UPDATE agent_revisions SET config = :config, digest = :digest WHERE id = :id").bindparams(
        sa.bindparam("config", type_=JSONB)
    )
    with _unguarded("agent_revisions"):
        stored = bind.execute(
            sa.text(f"SELECT id, config FROM agent_revisions WHERE config->'toolsets' ?| {_NAMES_REMOVED}")
        )
        for revision_id, config in stored.all():
            for key in _REMOVED:
                config["toolsets"].pop(key, None)
            # The revision digest: SHA-256 of the canonical JSON it stores.
            digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            bind.execute(rewrite, {"id": revision_id, "config": config, "digest": digest})
    for table in ("runs", "inbox_entries"):
        with _unguarded(table):
            op.execute(
                f"UPDATE {table} SET options = jsonb_set(options, '{{overrides,toolsets}}',"
                f" (options #> '{{overrides,toolsets}}') - {_NAMES_REMOVED})"
                f" WHERE options #> '{{overrides,toolsets}}' ?| {_NAMES_REMOVED}"
            )


def downgrade() -> None:
    pass
