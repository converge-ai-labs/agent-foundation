"""Client-only configuration; identity, ownership and management status belong to environments."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class ClientSettingsRow(Base):
    __tablename__ = "client_environment_settings"
    __table_args__ = (CheckConstraint("credential_version > 0", name="credential_version"),)

    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    credential_version: Mapped[int] = mapped_column(BigInteger)
    default_working_directory: Mapped[str]
    permission_ceiling: Mapped[dict] = mapped_column(JSONB)
    declared_capabilities: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    description: Mapped[str | None]
