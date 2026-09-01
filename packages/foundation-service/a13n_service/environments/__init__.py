"""Foundation-owned Environment management and Agent selection resolution."""

from .domain import (
    Environment,
    EnvironmentAccess,
    EnvironmentCollection,
    EnvironmentCredentialBinding,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderCatalogEntryCollection,
    EnvironmentProviderLock,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentRevisionCollection,
)
from .errors import EnvironmentManagementError

__all__ = [
    "Environment",
    "EnvironmentAccess",
    "EnvironmentCollection",
    "EnvironmentCredentialBinding",
    "EnvironmentManagementError",
    "EnvironmentProviderCatalogEntry",
    "EnvironmentProviderCatalogEntryCollection",
    "EnvironmentProviderLock",
    "EnvironmentProviderSelection",
    "EnvironmentRevision",
    "EnvironmentRevisionCollection",
]
