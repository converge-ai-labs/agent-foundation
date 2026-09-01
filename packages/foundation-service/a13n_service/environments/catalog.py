"""Safe public projection and exact locks for the selected Provider catalog."""

from __future__ import annotations

import hashlib
import json

from a13n_environment_provider import (
    EnvironmentProviderCatalog,
    EnvironmentProviderRegistration,
)
from a13n_environment_provider import (
    __version__ as environment_provider_version,
)

from .domain import EnvironmentProviderCatalogEntry, EnvironmentProviderLock
from .errors import environment_provider_not_found


class FoundationEnvironmentProviderCatalog:
    def __init__(self, providers: EnvironmentProviderCatalog) -> None:
        self.providers = providers
        registrations = {item.provider_key: item for item in providers.registrations}
        if set(registrations) != set(providers):
            raise ValueError("Environment Provider catalog registrations are incomplete")
        self._entries = {key: _entry(registrations[key], providers[key].configuration_versions) for key in providers}

    def entries(self) -> tuple[EnvironmentProviderCatalogEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def entry(self, provider_key: str) -> EnvironmentProviderCatalogEntry:
        entry = self._entries.get(provider_key)
        if entry is None:
            raise environment_provider_not_found()
        return entry


def _entry(
    registration: EnvironmentProviderRegistration,
    configuration_versions: frozenset[str],
) -> EnvironmentProviderCatalogEntry:
    distribution_name = registration.distribution_name
    distribution_version = registration.distribution_version
    if registration.builtin:
        distribution_name = "a13n-environment-provider"
        distribution_version = environment_provider_version
    payload = {
        "schema_version": "1",
        "provider_key": registration.provider_key,
        "class_module": registration.class_module,
        "class_qualname": registration.class_qualname,
        "import_target": registration.import_target,
        "distribution_name": distribution_name,
        "distribution_version": distribution_version,
        "builtin": registration.builtin,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    lock = EnvironmentProviderLock(
        provider_key=registration.provider_key,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        builtin=registration.builtin,
        registration_digest_sha256=digest,
    )
    return EnvironmentProviderCatalogEntry(
        provider_key=registration.provider_key,
        configuration_versions=tuple(sorted(configuration_versions)),
        provider_lock=lock,
    )
