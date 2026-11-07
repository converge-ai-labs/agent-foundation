"""Thread mounts and the environment records they reference."""

import json
from datetime import datetime

from a13n_harness.environment.models import FILE_EXECUTION_ACTIONS
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, rules
from a13n_service.resources.environment_instances.tables import CLIENT_IDENTITY as CLIENT_IDENTITY
from a13n_service.resources.environment_instances.tables import OPERATIONS as OPERATIONS
from a13n_service.resources.environment_instances.tables import STATUSES as STATUSES
from a13n_service.resources.environment_instances.tables import EnvironmentRow as EnvironmentRow


class ThreadEnvironmentRow(Base):
    """A thread's desired mount; acceptance copies the set into `runs.environment_mounts`."""

    __tablename__ = "thread_environments"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "name"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "environment_id"], ["environments.workspace_id", "environments.id"]),
        Index("ix_thread_environments_environment", "environment_id"),
        rules(
            # Mount edits are visible thread changes, so they advance the thread version like inbox edits.
            "CREATE TRIGGER touch_threads_on_mount_insert AFTER INSERT ON thread_environments"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_mount_update AFTER UPDATE ON thread_environments"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_mount_delete AFTER DELETE ON thread_environments"
            " REFERENCING OLD TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
        ),
    )
    thread_id: Mapped[str] = mapped_column(String(72))
    environment_id: Mapped[str] = mapped_column(String(72))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    session_binding: Mapped[str] = mapped_column(server_default=text("'default'"))
    working_directory: Mapped[str | None]
    permission_ceiling: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'" + json.dumps({"operations": sorted(FILE_EXECUTION_ACTIONS)}) + "'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadSessionBindingRow(Base):
    """Desired startup policy and admitted Session head for one logical binding."""

    __tablename__ = "thread_session_bindings"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "environment_id", "name"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "environment_id"], ["environments.workspace_id", "environments.id"]),
    )
    thread_id: Mapped[str] = mapped_column(String(72))
    environment_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str]
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    configuration: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    head: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class ThreadExecutionContextRow(Base):
    """Worker affinity and generation shared by a Thread's Session bindings."""

    __tablename__ = "thread_execution_contexts"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
    )
    thread_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    worker_id: Mapped[str | None]
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
