"""App-owned public-schema models, independent migration resources, and portable SQL names.

These declarations do not open connections. Apps use ordinary SQLAlchemy models and the host's Storage.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from alembic import op
from sqlalchemy import Enum, MetaData, PrimaryKeyConstraint, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base

APP_PREFIX = "app__"
_APP_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_LABEL = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_LOCAL_NAME = re.compile(r"[a-z][a-z0-9_]*")


class DatabaseRow(Base):
    """A deployment-wide storage identity, retained when the package is uninstalled."""

    __tablename__ = "app_databases"

    app_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(32), unique=True)


@dataclass(frozen=True)
class AppDatabase:
    """One App's metadata and packaged Alembic history.

    `prefix` is an optional SQL-safe label, not a full table name. For example, `reports_1` produces
    `app__reports_1__notes`. An App may supply this value from its deployment configuration before declaring
    models. All serving, migration and worker processes must resolve the same value for a given database.
    """

    app_id: str
    migrations: str
    prefix: str | None = None
    metadata: MetaData = field(init=False, compare=False, repr=False)
    _references: set[str] = field(default_factory=set, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not _APP_ID.fullmatch(self.app_id) or len(self.app_id) > 255:
            raise ValueError(f"Invalid App database ID: {self.app_id}")
        label = self.prefix if self.prefix is not None else re.sub(r"[._-]", "_", self.app_id)
        if not _LABEL.fullmatch(label) or len(label) > 24:
            raise ValueError("App database prefix must be lowercase snake case, start with a letter, and fit 24 bytes")
        package, separator, directory = self.migrations.partition(":")
        path = PurePosixPath(directory)
        if not separator or not package or not directory or path.is_absolute() or ".." in path.parts:
            raise ValueError("App migrations must name package:relative-directory")
        object.__setattr__(self, "prefix", f"{APP_PREFIX}{label}__")
        object.__setattr__(
            self,
            "metadata",
            MetaData(
                naming_convention={
                    "ix": "%(table_name)s__ix_%(column_0_name)s",
                    "uq": "%(table_name)s__uq_%(column_0_N_name)s",
                    "ck": "%(table_name)s__ck_%(constraint_name)s",
                    "fk": "%(table_name)s__fk_%(column_0_N_name)s",
                    "pk": "%(table_name)s__pk",
                }
            ),
        )

    def name(self, local: str) -> str:
        """A stable public-schema identifier. Migrations use `migration_name` instead of freezing this value."""
        if not _LOCAL_NAME.fullmatch(local) or local == "alembic_version":
            raise ValueError(f"Invalid or reserved App database object name: {local}")
        result = f"{self.prefix}{local}"
        if len(result) > 63:
            raise ValueError(f"App database object name exceeds 63 bytes: {local}")
        return result

    @property
    def version_table(self) -> str:
        return f"{self.prefix}alembic_version"

    def owns(self, name: str) -> bool:
        assert self.prefix is not None
        return name.startswith(self.prefix)

    def reference(self, table: Table) -> Table:
        """Copy a native table for string foreign-key resolution, without claiming its DDL."""
        if table.name.startswith(APP_PREFIX):
            raise ValueError("References must target native tables, not another App's namespace")
        if table.key in self._references:
            return self.metadata.tables[table.key]
        copied = table.to_metadata(self.metadata)
        self._references.add(copied.key)
        # Native foreign keys participate in SQLAlchemy's dependency sort too. Keep their targets
        # available for resolution, while the migration filters exclude this whole read-only graph.
        for foreign_key in table.foreign_keys:
            self.reference(foreign_key.column.table)
        return copied

    def validate(self) -> None:
        """Owned metadata cannot accidentally adopt native tables or another App's objects."""
        for table in self.metadata.tables.values():
            if table.key in self._references:
                continue
            if table.schema is not None or not self.owns(table.name) or table.name == self.version_table:
                raise ValueError(f"Table is outside App {self.app_id}'s database namespace: {table.key}")
            if len(table.name.encode()) > 63:
                raise ValueError(f"App table name exceeds 63 bytes: {table.name}")
            for constraint in table.constraints:
                if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint)) and constraint.name is not None:
                    if not self.owns(str(constraint.name)):
                        raise ValueError(f"App key constraint must use its namespace: {constraint.name}")
            for column in table.columns:
                if isinstance(column.type, Enum) and column.type.native_enum:
                    if column.type.name is None or not self.owns(column.type.name):
                        raise ValueError("App enum types must use their database namespace")
            for index in table.indexes:
                if index.name is None or not self.owns(str(index.name)):
                    raise ValueError(f"App index must use its table namespace: {index.name}")


def migration_name(local: str) -> str:
    """Resolve an App-owned identifier inside a revision using the selected deployment prefix.

    Native references stay literal. Raw SQL and hand-edited migrations can call this helper explicitly.
    Generated constraint names may exceed 63 bytes; Alembic's `op.f` preserves SQLAlchemy's standard
    deterministic shortening for those names.
    """
    config = op.get_context().config
    if config is None:
        raise ValueError("migration_name requires an App migration context")
    database = config.attributes["app_database"]
    if not isinstance(database, AppDatabase):
        raise ValueError("migration_name requires an App migration context")
    return f"{database.prefix}{local}"
