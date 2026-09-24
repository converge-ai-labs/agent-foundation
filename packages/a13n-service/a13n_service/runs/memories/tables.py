"""Thread memory mounts."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, rules


class ThreadMemoryRow(Base):
    """A memory mounted on a thread; acceptance copies the set into `runs.memory_mounts`. Deleting the memory
    unmounts it."""

    __tablename__ = "thread_memories"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "name"),
        UniqueConstraint("thread_id", "memory_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "memory_id"], ["memories.workspace_id", "memories.id"], ondelete="CASCADE"
        ),
        Index("ix_thread_memories_memory", "memory_id"),
        CheckConstraint("access IN ('read', 'write')", name="access"),
        rules(
            # Mount edits are visible thread changes, so they advance the thread version like inbox edits.
            "CREATE TRIGGER touch_threads_on_memory_insert AFTER INSERT ON thread_memories"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_memory_delete AFTER DELETE ON thread_memories"
            " REFERENCING OLD TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
        ),
    )
    thread_id: Mapped[str] = mapped_column(String(72))
    memory_id: Mapped[str] = mapped_column(String(72))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    access: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
