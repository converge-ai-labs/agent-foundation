"""The five provider resource tables (model, environment, connector, web, memory) and their shared shape.

Each kind keeps its own table so the resources that use it hold typed foreign keys; only the columns are
shared. A provider belongs to one workspace, and a row referencing one does so through `(workspace_id, id)`, so
the database refuses a provider of another workspace.
"""

from typing import Any, ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.providers.registry import ProviderKind


class ProviderRow(Stamped, Base):
    """A kind maps its own table and names its registry kind, resource kind and ID prefix; nothing else differs."""

    __abstract__ = True
    PROVIDER_KIND: ClassVar[ProviderKind]
    # How errors and audit events name the resource.
    KIND: ClassVar[str]
    ID_PREFIX: ClassVar[str]

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    # Selects the registered definition whose schemas validate `config` and `credential`.
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    # Encrypted, write-only envelope bound to this row.
    credential: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # A model provider's extra request headers: name -> write-only envelope bound to this row and that name.
    extra_headers: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            UniqueConstraint("workspace_id", "id"),
            ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
            rules(identity_guarded(cls.__tablename__)),
        )


class ModelProviderRow(ProviderRow):
    __tablename__ = "model_providers"
    PROVIDER_KIND = "model"
    KIND: ClassVar[str] = "model_provider"
    ID_PREFIX = "mprov"


class EnvironmentProviderRow(ProviderRow):
    __tablename__ = "environment_providers"
    PROVIDER_KIND = "environment"
    KIND: ClassVar[str] = "environment_provider"
    ID_PREFIX = "eprov"


class ConnectorProviderRow(ProviderRow):
    __tablename__ = "connector_providers"
    PROVIDER_KIND = "connector"
    KIND: ClassVar[str] = "connector_provider"
    ID_PREFIX = "cprov"


class WebProviderRow(ProviderRow):
    __tablename__ = "web_providers"
    PROVIDER_KIND = "web"
    KIND: ClassVar[str] = "web_provider"
    ID_PREFIX = "wprov"


class MemoryProviderRow(ProviderRow):
    __tablename__ = "memory_providers"
    PROVIDER_KIND = "memory"
    KIND: ClassVar[str] = "memory_provider"
    ID_PREFIX = "memprov"
