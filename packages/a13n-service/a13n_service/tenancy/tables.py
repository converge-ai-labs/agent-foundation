"""Organizations, workspaces, principals, their credentials and memberships: grants and pending invitations."""

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

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules, trigger

_GUARD_PRINCIPAL = """
CREATE FUNCTION guard_principal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.kind, NEW.home_workspace_id, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id, OLD.kind, OLD.home_workspace_id, OLD.created_at)
    THEN RAISE EXCEPTION 'principal identity and home workspace are immutable'; END IF;
    RETURN NEW;
END $$
"""


class OrganizationRow(Stamped, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    name: Mapped[str]
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # The icon's object reference (`infra/images.py`).
    image: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class WorkspaceRow(Stamped, Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str]
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # The icon's object reference (`infra/images.py`).
    image: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrincipalRow(Stamped, Base):
    __tablename__ = "principals"
    __table_args__ = (
        CheckConstraint("kind IN ('user', 'service_account')", name="kind"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        CheckConstraint("(kind = 'user') = (email IS NOT NULL)", name="user_email"),
        CheckConstraint("(kind = 'service_account') = (home_workspace_id IS NOT NULL)", name="account_home"),
        CheckConstraint("(kind = 'service_account') = (description IS NOT NULL)", name="account_description"),
        Index("ix_principals_home_workspace_id", "home_workspace_id"),
        rules(_GUARD_PRINCIPAL, trigger("principals", "guard_principal")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    kind: Mapped[str]
    name: Mapped[str]
    email: Mapped[str | None] = mapped_column(unique=True)
    home_workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    status: Mapped[str] = mapped_column(server_default="active")
    description: Mapped[str | None]
    # A user's profile image reference (`infra/images.py`).
    image: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class GrantRow(Base):
    """Created and explicitly removed, never edited: a role change replaces the row."""

    __tablename__ = "grants"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("uq_grants_scope", "principal_id", "organization_id", text("COALESCE(workspace_id, '')"), unique=True),
        Index("ix_grants_scope", "organization_id", "workspace_id"),
        rules(trigger("grants", "refuse_mutation")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    role: Mapped[str]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvitationRow(Stamped, Base):
    """A pending grant for an email address; its link token is stored only as a hash."""

    __tablename__ = "invitations"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        CheckConstraint("accepted_at IS NULL OR revoked_at IS NULL", name="settled_once"),
        CheckConstraint("(accepted_at IS NULL) = (principal_id IS NULL)", name="accepted_by"),
        Index("ix_invitations_scope", "organization_id", "workspace_id"),
        Index("ix_invitations_unaccepted", "expires_at", postgresql_where=text("accepted_at IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    email: Mapped[str]
    role: Mapped[str]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordRow(Base):
    __tablename__ = "passwords"
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    hash: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKeyRow(Stamped, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("ix_api_keys_workspace_id", "workspace_id"),
        # Recording use is not a change of the key, so it keeps the key's ETag.
        rules(identity_guarded("api_keys"), unversioned=("last_used_at",)),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), index=True)
    name: Mapped[str]
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class TokenRow(Base):
    """A login session or a one-use link; the secret is stored only as its hash."""

    __tablename__ = "tokens"
    __table_args__ = (CheckConstraint("kind IN ('session','password_reset','email_change')", name="kind"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), index=True)
    kind: Mapped[str]
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
