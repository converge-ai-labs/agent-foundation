"""The authoring entry point for Provider definitions and explicitly selected plugins."""
# ruff: noqa: F401  # `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import exported_names, load_export

if TYPE_CHECKING:
    from .authentication import Authentication, AuthenticationCase, CredentialMode
    from .catalog import ProviderCatalog, ProviderNotSelected
    from .connector.definition import ConnectorProviderDefinition
    from .definition import ProviderDefinition
    from .environment.definition import EnvironmentProviderDefinition
    from .model.definition import ModelProviderDefinition
    from .plugins import ProviderManifest
    from .web.definition import WebProviderDefinition

_EXPORTS = {
    "a13n_harness.providers.authentication": ("Authentication", "AuthenticationCase", "CredentialMode"),
    "a13n_harness.providers.catalog": ("ProviderCatalog", "ProviderNotSelected"),
    "a13n_harness.providers.connector.definition": ("ConnectorProviderDefinition",),
    "a13n_harness.providers.definition": ("ProviderDefinition",),
    "a13n_harness.providers.environment.definition": ("EnvironmentProviderDefinition",),
    "a13n_harness.providers.model.definition": ("ModelProviderDefinition",),
    "a13n_harness.providers.plugins": ("ProviderManifest",),
    "a13n_harness.providers.web.definition": ("WebProviderDefinition",),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = exported_names(_EXPORTS)  # pyright: ignore[reportUnsupportedDunderAll]
