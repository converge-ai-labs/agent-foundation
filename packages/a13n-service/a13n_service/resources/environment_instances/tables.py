"""Environment instance records shared by resource selection and execution lifecycle.

An instance's `status` names its outstanding lifecycle operation; `operation_id` and `generation` fence
completions, and the `lease_*` columns are the claim of whoever is dispatching that operation right now.
`renew_at` and `expires_at` schedule the renewal of a ready sandbox whose type ends it unless renewed.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules

STATUSES = ("reserved", "creating", "starting", "ready", "stopping", "stopped", "deleting", "deleted", "disabled")
OPERATIONS = "('creating', 'starting', 'stopping', 'deleting')"
CLIENT_IDENTITY = text("kind = 'external' AND status <> 'deleted'")


class EnvironmentRow(Stamped, Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["environment_providers.workspace_id", "environment_providers.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_id"], ["environment_templates.workspace_id", "environment_templates.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metrics_data_source_id"],
            ["metrics_data_sources.workspace_id", "metrics_data_sources.id"],
            name="fk_environments_metrics_data_source",
        ),
        CheckConstraint(f"status IN {STATUSES}", name="status"),
        CheckConstraint("kind IN ('managed', 'external_http', 'external')", name="kind"),
        CheckConstraint("status <> 'disabled' OR kind = 'external'", name="disabled"),
        CheckConstraint(
            "template_id IS NULL OR status NOT IN ('ready', 'starting', 'stopping', 'stopped') OR handle IS NOT NULL",
            name="handle",
        ),
        CheckConstraint(
            "status <> 'reserved' OR (handle IS NULL AND provider_identity IS NULL AND failure IS NULL)",
            name="reserved",
        ),
        # The status names the outstanding operation; no other state carries one.
        CheckConstraint(f"(status IN {OPERATIONS}) = (operation_id IS NOT NULL)", name="operation"),
        CheckConstraint("(operation_id IS NULL) = (operation_started_at IS NULL)", name="operation_started"),
        CheckConstraint("operation_deadline IS NULL OR operation_id IS NOT NULL", name="operation_deadline"),
        CheckConstraint(
            "operation_id IS NOT NULL OR (next_operation_at IS NULL AND NOT operation_unsettled)",
            name="operation_progress",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_token_hash IS NULL) AND (lease_owner IS NULL) = (lease_expires_at IS NULL)"
            " AND (lease_owner IS NULL OR operation_id IS NOT NULL)",
            name="lease",
        ),
        # External targets are connect-only: never created, started, stopped or destroyed by the Service.
        CheckConstraint("kind = 'managed' OR status IN ('ready', 'disabled', 'deleted')", name="connect_only"),
        # Managed instances come from a template of a provider; external targets are only a device at an endpoint.
        CheckConstraint(
            "(kind = 'managed') = (template_id IS NOT NULL) AND (template_id IS NULL) = (provider_id IS NULL)",
            name="managed",
        ),
        CheckConstraint(
            "(kind <> 'managed') = (device_id IS NOT NULL) AND (kind = 'external_http') = (endpoint IS NOT NULL)"
            " AND (kind = 'managed' OR (provider_identity IS NULL AND handle IS NULL))"
            " AND (kind <> 'external' OR owner_principal_id IS NOT NULL)",
            name="external",
        ),
        # The tombstone of an external target drops its token.
        CheckConstraint("(token IS NOT NULL) = (kind = 'external_http' AND status <> 'deleted')", name="token"),
        Index(
            "uq_environments_client_identity",
            "workspace_id",
            "owner_principal_id",
            "device_id",
            unique=True,
            postgresql_where=CLIENT_IDENTITY,
        ),
        # Only a ready managed sandbox is renewed.
        CheckConstraint(
            "(renew_at IS NULL AND expires_at IS NULL) OR (status = 'ready' AND template_id IS NOT NULL)",
            name="renewal",
        ),
        Index(
            "uq_environments_operation", "operation_id", unique=True, postgresql_where=text("operation_id IS NOT NULL")
        ),
        Index("ix_environments_operations", "updated_at", postgresql_where=text(f"status IN {OPERATIONS}")),
        Index(
            "ix_environments_idle",
            "last_used_at",
            postgresql_where=text("template_id IS NOT NULL AND status IN ('reserved', 'ready', 'stopped')"),
        ),
        Index("ix_environments_renewals", "renew_at", postgresql_where=text("renew_at IS NOT NULL")),
        Index(
            "ix_environments_metrics_data_source",
            "metrics_data_source_id",
            postgresql_where=text("metrics_data_source_id IS NOT NULL"),
        ),
        rules(
            identity_guarded("environments"),
            "CREATE TRIGGER guard_environment_target BEFORE UPDATE ON environments FOR EACH ROW"
            " WHEN (ROW(OLD.kind, OLD.device_id, OLD.owner_principal_id)"
            " IS DISTINCT FROM ROW(NEW.kind, NEW.device_id, NEW.owner_principal_id))"
            " EXECUTE FUNCTION refuse_mutation()",
            # Renewals reschedule themselves every few minutes without changing what a client sees.
            unversioned=("renew_at", "expires_at"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="managed")
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    # NULL only for external targets, which no provider account reaches.
    provider_id: Mapped[str | None]
    # The non-secret provider type and backend locator that give `handle` meaning, frozen before a managed instance's
    # first dispatch and NULL until then; NULL for an external target.
    provider_identity: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # NULL only for external targets, which are connect-only.
    template_id: Mapped[str | None]
    # An external target's native device identity, which every connection expects; the tombstone keeps it.
    device_id: Mapped[str | None]
    # The HTTP(S) origin an external target's daemon serves, and its token: an encrypted, write-only envelope bound
    # to this row and column.
    endpoint: Mapped[str | None]
    token: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # External targets belong to the principal that registered them; workspace-managed sandboxes have no owner.
    owner_principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    name: Mapped[str]
    metrics_data_source_id: Mapped[str | None]
    metrics_labels: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Frozen creation input, encrypted for this row/column; erased after successful
    # creation or deletion. Retries reuse it even if the desired source changes.
    metrics_launch: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Secret-free sampler/exporter receipt, independent of the live desired binding.
    metrics_application: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str]
    # The recipe a managed instance was built from, the provider's portable state for reaching it and the version of
    # the credential that last did; NULL for an external target, which its endpoint and device ID reach.
    handle: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Instance-private Envd credential, encrypted for this row/column before allocation.
    # Reused by retries and fixed-target attachment; erased only after confirmed deletion.
    envd_bootstrap: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Secret-free final provider receipt, including retained storage identities, after the live handle is cleared.
    retirement_state: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Explicit instance policy; NULL preserves the existing live template idle policy.
    reclamation: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    preparation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preparation_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    debug_hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    operation_id: Mapped[str | None]
    operation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The bound of the provider call currently claimed.
    operation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pending operations yield their claim and pace both Worker and Control continuation.
    next_operation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_unsettled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    lease_owner: Mapped[str | None]
    lease_token_hash: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the next renewal of a ready sandbox whose type ends it unless renewed is due, NULL when none is; a renewal
    # in progress pushes it past its own deadline, which is its claim. `expires_at` is the expiry the provider last
    # reported.
    renew_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
