"""Memories and the PostgreSQL file store behind file memories; deleting a memory deletes its store in the same
transaction. A record memory keeps its records in its Memory Provider's backend, under its namespace."""

from datetime import datetime
from typing import ClassVar, Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.resources.providers.tables import provider_in_scope

type MemoryKind = Literal["file", "record"]


class MemoryRow(Stamped, Base):
    __tablename__ = "memories"
    KIND: ClassVar[str] = "memory"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key"),
        UniqueConstraint("workspace_id", "id"),
        # A backend namespace belongs to one memory.
        UniqueConstraint("provider_id", "namespace"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["organization_id", "provider_id"], ["memory_providers.organization_id", "memory_providers.id"]
        ),
        CheckConstraint("kind IN ('file', 'record')", name="kind"),
        # The Service stores file memories in PostgreSQL itself; Memory Providers back record memories.
        CheckConstraint("(type = 'postgres') = (kind = 'file')", name="type"),
        CheckConstraint(
            "(type = 'postgres') = (provider_id IS NULL) AND (provider_id IS NULL) = (namespace IS NULL)",
            name="provider",
        ),
        CheckConstraint("kind = 'file' OR always_load = '[]'::jsonb", name="always_load"),
        rules(identity_guarded("memories"), *provider_in_scope("memories", "provider_id", "memory_providers")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    kind: Mapped[MemoryKind] = mapped_column(String)
    type: Mapped[str]
    provider_id: Mapped[str | None] = mapped_column(String(72))
    # The record memory's namespace in its provider's backend, such as a mem0 `user_id`.
    namespace: Mapped[str | None]
    # NULL inherits the default guide; "" means none.
    guide: Mapped[str | None]
    # File paths whose content leads the memory's context, chosen by the owner, never the model.
    always_load: Mapped[list] = mapped_column(JSONB)
    labels: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class MemoryFileStoreRow(Base):
    """A PostgreSQL file memory's write lock, change sequence and byte counters. Every write locks it first, so
    changes are numbered without gaps and the byte total is exact."""

    __tablename__ = "memory_file_stores"
    __table_args__ = (
        CheckConstraint("pruned_through_seq <= seq", name="pruned"),
        CheckConstraint("content_bytes >= 0 AND history_bytes >= 0 AND file_count >= 0", name="counters"),
    )
    memory_id: Mapped[str] = mapped_column(String(72), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True)
    # The last change's number; each change takes the next one.
    seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # Revisions up to this number may be gone, so a change cursor below it cannot list what changed since.
    pruned_through_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    content_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    history_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    file_count: Mapped[int] = mapped_column(server_default=text("0"))


class MemoryFileRow(Base):
    """A file's current content. A move keeps the row and its ID; `version` is the number of its last change."""

    __tablename__ = "memory_files"
    KIND: ClassVar[str] = "memory_file"
    __table_args__ = (UniqueConstraint("memory_id", "path"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(72), ForeignKey("memory_file_stores.memory_id", ondelete="CASCADE"))
    path: Mapped[str]
    content: Mapped[str]
    size: Mapped[int]
    version: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str | None]
    # Who made the last change: a run's principal with its run ID, or a person through the API.
    updated_by_run_id: Mapped[str | None] = mapped_column(String(72))
    updated_by_principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryFileRevisionRow(Base):
    """One change to one path, holding the content it replaced; also the memory's change feed.

    A move writes two: `move_out` at the source and `move_in` at the destination, each naming the other path.
    """

    __tablename__ = "memory_file_revisions"
    __table_args__ = (
        PrimaryKeyConstraint("memory_id", "seq"),
        Index("ix_memory_file_revisions_path", "memory_id", "path", "seq"),
        Index("ix_memory_file_revisions_run", "run_id", postgresql_where=text("run_id IS NOT NULL")),
        CheckConstraint("op IN ('create', 'update', 'delete', 'move_out', 'move_in')", name="op"),
        CheckConstraint("(op IN ('move_out', 'move_in')) = (moved_path IS NOT NULL)", name="moved_path"),
        CheckConstraint("(op IN ('create', 'move_in')) = (previous_content IS NULL)", name="previous_content"),
    )
    memory_id: Mapped[str] = mapped_column(String(72), ForeignKey("memory_file_stores.memory_id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(BigInteger)
    path: Mapped[str]
    op: Mapped[str]
    moved_path: Mapped[str | None]
    previous_content: Mapped[str | None]
    run_id: Mapped[str | None] = mapped_column(String(72))
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
