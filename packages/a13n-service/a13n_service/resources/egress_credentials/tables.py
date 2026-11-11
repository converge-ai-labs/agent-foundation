"""Encrypted workspace credentials; Sessions retain only references and application evidence."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
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

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules, trigger


class CredentialRow(Stamped, Base):
    __tablename__ = "egress_credentials"
    KIND: ClassVar[str] = "egress_credential"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        CheckConstraint("credential_version > 0 AND version > 0", name="versions"),
        CheckConstraint(
            "(state = 'active' AND value IS NOT NULL AND revoked_at IS NULL) OR "
            "(state = 'revoked' AND value IS NULL AND revoked_at IS NOT NULL)",
            name="state",
        ),
        Index("ix_egress_credentials_workspace_created", "workspace_id", "created_at", "id"),
        rules(
            identity_guarded("egress_credentials"),
            """
            CREATE FUNCTION guard_egress_credential() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.allowed_hosts IS DISTINCT FROM OLD.allowed_hosts
                THEN RAISE EXCEPTION 'credential injection hosts are immutable'; END IF;
                IF OLD.state = 'revoked' AND ROW(NEW.state, NEW.value, NEW.revoked_at)
                    IS DISTINCT FROM ROW(OLD.state, OLD.value, OLD.revoked_at)
                THEN RAISE EXCEPTION 'credential revocation is final'; END IF;
                IF NEW.credential_version < OLD.credential_version
                    OR NEW.credential_version > OLD.credential_version + 1
                    OR (NEW.state IS DISTINCT FROM OLD.state AND NEW.credential_version <> OLD.credential_version + 1)
                THEN RAISE EXCEPTION 'invalid credential version transition'; END IF;
                RETURN NEW;
            END $$
            """,
            trigger("egress_credentials", "guard_egress_credential"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    allowed_hosts: Mapped[list[str]] = mapped_column(JSONB)
    value: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    state: Mapped[str] = mapped_column(server_default=text("'active'"))
    credential_version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
