"""Foundation-owned Environment management and Agent selection resolution."""

from .catalog import FoundationEnvironmentProviderCatalog, FoundationEnvironmentProviderRegistration
from .domain import (
    Environment,
    EnvironmentAccess,
    EnvironmentCollection,
    EnvironmentConnectionSpec,
    EnvironmentCredentialBinding,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderCatalogEntryCollection,
    EnvironmentProviderLock,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentRevisionCollection,
    EnvironmentRevisionSummary,
    RunEnvironmentBinding,
)
from .errors import EnvironmentManagementError
from .providers import FoundationEnvironmentAttachProvider
from .testing import (
    EnvironmentAttachmentTester,
    EnvironmentRuntimeBuilder,
    EnvironmentSecretValueResolver,
    NativeEnvironmentAttachmentTester,
)

__all__ = [
    "Environment",
    "EnvironmentAccess",
    "EnvironmentAttachmentTester",
    "EnvironmentCollection",
    "EnvironmentConnectionSpec",
    "EnvironmentCredentialBinding",
    "EnvironmentManagementError",
    "EnvironmentProviderCatalogEntry",
    "EnvironmentProviderCatalogEntryCollection",
    "EnvironmentProviderLock",
    "EnvironmentProviderSelection",
    "EnvironmentRevision",
    "EnvironmentRevisionCollection",
    "EnvironmentRevisionSummary",
    "EnvironmentRuntimeBuilder",
    "EnvironmentSecretValueResolver",
    "FoundationEnvironmentAttachProvider",
    "FoundationEnvironmentProviderCatalog",
    "FoundationEnvironmentProviderRegistration",
    "NativeEnvironmentAttachmentTester",
    "RunEnvironmentBinding",
]
