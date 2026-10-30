"""Browser lifetime, exact tab ownership, admission and blocked recipients."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class BrowserRow(Stamped, Base):
    __tablename__ = "browsers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["browser_providers.workspace_id", "browser_providers.id"]
        ),
        CheckConstraint(
            "(source = 'managed' AND provider_id IS NOT NULL) OR (source = 'external_cdp' AND provider_id IS NULL)",
            name="source_provider",
        ),
        CheckConstraint("source <> 'external_cdp' OR control = 'automation'", name="source_control"),
        CheckConstraint("status IN ('reserved','creating','ready','deleting','deleted','lost')", name="status"),
        CheckConstraint("control IN ('automation','draining','human','returning')", name="control"),
        CheckConstraint("status <> 'ready' OR handle IS NOT NULL", name="ready_handle"),
        CheckConstraint("(claim_id IS NULL) = (claim_until IS NULL)", name="claim"),
        CheckConstraint("generation > 0 AND episode >= 0", name="generation"),
        Index("ix_browsers_control_due", "control_due_at", postgresql_where=text("control <> 'automation'")),
        rules(identity_guarded("browsers"), unversioned=("control_due_at",)),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    source: Mapped[str] = mapped_column(default="managed", server_default="managed")
    provider_id: Mapped[str | None]
    name: Mapped[str]
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    recipe: Mapped[dict] = mapped_column(JSONB)
    provider_identity: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="reserved")
    generation: Mapped[int] = mapped_column(default=1)
    # Allocation is attempted once; recovery inspects this same Browser identity.
    create_dispatched: Mapped[bool] = mapped_column(default=False)
    handle: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    claim_id: Mapped[str | None]
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[str | None]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idle_seconds: Mapped[int | None]
    control: Mapped[str] = mapped_column(default="automation")
    episode: Mapped[int] = mapped_column(default=0)
    control_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    control_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    controller_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    controller_thread_id: Mapped[str | None] = mapped_column(ForeignKey("threads.id"))
    viewer_id: Mapped[str | None]
    viewer_revoked: Mapped[bool] = mapped_column(default=False)
    viewer: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    human_baseline: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    # Incremented at each operation admission; other clients invalidate references.
    observation_epoch: Mapped[int] = mapped_column(default=0)


class ThreadBrowserRow(Base):
    __tablename__ = "thread_browsers"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "browser_id"], ["browsers.workspace_id", "browsers.id"]),
        CheckConstraint("tab_access IN ('global','thread')", name="tab_access"),
        CheckConstraint("preparation IN ('first_use','run_start')", name="preparation"),
    )
    thread_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    workspace_id: Mapped[str]
    browser_id: Mapped[str]
    tab_access: Mapped[str]
    preparation: Mapped[str]


class BrowserTabRow(Base):
    __tablename__ = "browser_tabs"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "browser_id"], ["browsers.workspace_id", "browsers.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
    )
    browser_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    workspace_id: Mapped[str]
    thread_id: Mapped[str]
    generation: Mapped[int]


class BrowserSelectionRow(Base):
    __tablename__ = "browser_selections"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "browser_id"], ["browsers.workspace_id", "browsers.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
    )
    browser_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    workspace_id: Mapped[str]
    generation: Mapped[int]
    target_id: Mapped[str | None]


class BrowserOperationRow(Base):
    """An admitted call remains unsettled after a crash or unknown protocol outcome."""

    __tablename__ = "browser_operations"
    __table_args__ = (
        Index("ix_browser_operations_unsettled", "browser_id", postgresql_where=text("settled_at IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    browser_id: Mapped[str] = mapped_column(ForeignKey("browsers.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    attempt_id: Mapped[str] = mapped_column(ForeignKey("run_attempts.id"))
    generation: Mapped[int]
    # Null covers inventory/selection changes; exact targets allow unrelated pages to proceed.
    target_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrowserBlockedRow(Base):
    """One originating authority per blocked Thread/episode, not another message queue."""

    __tablename__ = "browser_blocked_threads"
    __table_args__ = (UniqueConstraint("browser_id", "generation", "episode", "thread_id"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    browser_id: Mapped[str] = mapped_column(ForeignKey("browsers.id"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    generation: Mapped[int]
    episode: Mapped[int]
    suppressed: Mapped[bool] = mapped_column(default=False)
    entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbox_entries.id"))
    # Null while the current control episode is active; delivery TTL starts at verified handback.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
