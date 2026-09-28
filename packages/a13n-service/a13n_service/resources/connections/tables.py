"""Workspace connections: one tool source with one write-only credential and at most one remote operation."""

from datetime import datetime
from typing import ClassVar, Literal

from sqlalchemy import (
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

type ConnectionAuth = Literal["none", "bearer", "headers", "oauth", "account"]
type ConnectionStatus = Literal["pending", "ready", "reauthorization_required"]
type OperationKind = Literal["setup", "complete", "refresh", "revoke"]


class ConnectionRow(Stamped, Base):
    __tablename__ = "connections"
    KIND: ClassVar[str] = "connection"
    __table_args__ = (
        UniqueConstraint("organization_id", "workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "connector_provider_id"],
            ["connector_providers.workspace_id", "connector_providers.id"],
        ),
        CheckConstraint("auth IN ('none', 'bearer', 'headers', 'oauth', 'account')", name="auth"),
        CheckConstraint("status IN ('pending', 'ready', 'reauthorization_required')", name="status"),
        CheckConstraint("operation_kind IN ('setup', 'complete', 'refresh', 'revoke')", name="operation_kind"),
        CheckConstraint("(connector_provider_id IS NULL) = (auth <> 'account')", name="account_provider"),
        CheckConstraint("(type = 'mcp') = (connector_provider_id IS NULL)", name="connector_type"),
        CheckConstraint("status <> 'ready' OR auth = 'none' OR credential IS NOT NULL", name="ready_credential"),
        CheckConstraint("client_secret IS NULL OR auth = 'oauth'", name="client_secret"),
        CheckConstraint("(tokens IS NOT NULL) = (auth = 'oauth' AND credential IS NOT NULL)", name="tokens"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_kind IS NULL) AND (operation_id IS NULL) = (operation_deadline IS NULL)",
            name="operation",
        ),
        CheckConstraint(
            '("authorization" IS NULL) = (oauth_state_hash IS NULL)'
            ' AND ("authorization" IS NULL) = (authorization_expires_at IS NULL)',
            name="authorization_state",
        ),
        Index("ix_connections_workspace_id", "workspace_id", "id"),
        Index(
            "uq_connections_operation", "operation_id", unique=True, postgresql_where=text("operation_id IS NOT NULL")
        ),
        Index(
            "uq_connections_oauth_state",
            "oauth_state_hash",
            unique=True,
            postgresql_where=text("oauth_state_hash IS NOT NULL"),
        ),
        Index(
            "ix_connections_operation_deadline",
            "operation_deadline",
            postgresql_where=text("operation_deadline IS NOT NULL"),
        ),
        rules(
            identity_guarded("connections"),
            # What the Service maintains on its own keeps the ETag: tokens renewed under the credential's grant,
            # the outstanding operation and its failure, the pending browser flow and test outcomes.
            unversioned=(
                "tokens",
                "expires_at",
                "operation_id",
                "operation_kind",
                "operation_deadline",
                "failure",
                "authorization",
                "oauth_state_hash",
                "authorization_expires_at",
                "last_test",
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    # `mcp`, or the type of the connector provider serving the connection.
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    auth: Mapped[ConnectionAuth] = mapped_column(String)
    # Encrypted, write-only envelope: entered headers, the OAuth client an authorization was granted to, or a
    # connector account reference. It changes only when a person provides, authorizes or revokes a credential,
    # or when the credential is lost.
    credential: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Encrypted OAuth access and refresh tokens of the credential's grant; renewal replaces them.
    tokens: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # When the OAuth access token expires.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Encrypted, write-only secret of an OAuth client registered in advance; it outlives the tokens it obtains.
    client_secret: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    connector_provider_id: Mapped[str | None]
    status: Mapped[ConnectionStatus] = mapped_column(String)
    # The last failed operation: its identity, kind and a safe reason.
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # Encrypted pending browser authorization, found by the hash of its one-use callback state.
    authorization: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64))
    authorization_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_id: Mapped[str | None] = mapped_column(String(72))
    operation_kind: Mapped[OperationKind | None] = mapped_column(String)
    operation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The latest test's outcome and the connection version it tested.
    last_test: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
