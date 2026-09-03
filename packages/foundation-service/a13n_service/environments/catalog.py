"""Safe public projection and exact locks for the selected Provider catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from a13n_environment_provider import (
    EnvironmentProviderCatalog,
    EnvironmentProviderError,
    EnvironmentProviderRegistration,
)
from a13n_environment_provider import (
    __version__ as environment_provider_version,
)
from pydantic import BaseModel

from .domain import (
    EnvironmentConnectionSpec,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderLock,
    validated_target_key,
)
from .errors import EnvironmentManagementError, environment_provider_not_found
from .providers import FoundationEnvironmentAttachProvider


@dataclass(frozen=True, slots=True)
class ValidatedEnvironmentConnection:
    spec: EnvironmentConnectionSpec
    value: BaseModel
    target_key: str


@dataclass(frozen=True, slots=True)
class FoundationEnvironmentProviderRegistration:
    provider: FoundationEnvironmentAttachProvider
    provider_lock: EnvironmentProviderLock

    def __post_init__(self) -> None:
        if self.provider.provider_key != self.provider_lock.provider_key:
            raise ValueError("Foundation Environment Provider registration key does not match its lock")
        if not self.provider.connection_versions:
            raise ValueError("Foundation Environment Provider must expose at least one connection version")


class FoundationEnvironmentProviderCatalog:
    def __init__(self, registrations: Iterable[FoundationEnvironmentProviderRegistration]) -> None:
        attachments: dict[str, FoundationEnvironmentAttachProvider] = {}
        entries: dict[str, EnvironmentProviderCatalogEntry] = {}
        for registration in registrations:
            provider = registration.provider
            if not isinstance(provider, FoundationEnvironmentAttachProvider):
                raise ValueError("Environment Provider has no Foundation attachment capability")
            key = provider.provider_key
            if key in attachments:
                raise ValueError(f"Foundation Environment Provider {key!r} is registered more than once")
            attachments[key] = provider
            entries[key] = _entry(registration.provider_lock, provider.connection_versions)
        self._attachments = attachments
        self._entries = entries

    @classmethod
    def from_environment_provider_catalog(
        cls,
        providers: EnvironmentProviderCatalog,
    ) -> FoundationEnvironmentProviderCatalog:
        generic_registrations = {item.provider_key: item for item in providers.registrations}
        if set(generic_registrations) != set(providers):
            raise ValueError("Environment Provider catalog registrations are incomplete")
        registrations = []
        for key in providers:
            provider = providers[key]
            if not isinstance(provider, FoundationEnvironmentAttachProvider) or provider.provider_key != key:
                raise ValueError(f"Environment Provider {key!r} has no Foundation attachment capability")
            registrations.append(
                FoundationEnvironmentProviderRegistration(
                    provider=provider,
                    provider_lock=_provider_lock(generic_registrations[key]),
                )
            )
        return cls(registrations)

    def entries(self) -> tuple[EnvironmentProviderCatalogEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def entry(self, provider_key: str) -> EnvironmentProviderCatalogEntry:
        entry = self._entries.get(provider_key)
        if entry is None:
            raise environment_provider_not_found()
        return entry

    def attachment(self, provider_key: str) -> FoundationEnvironmentAttachProvider:
        self.entry(provider_key)
        return self._attachments[provider_key]

    def validate_connection(self, spec: EnvironmentConnectionSpec) -> ValidatedEnvironmentConnection:
        entry = self.entry(spec.provider_key)
        if spec.schema_version not in entry.connection_versions:
            raise EnvironmentManagementError(
                "provider_schema_unsupported",
                "The Environment Provider connection version is unsupported.",
                status_code=400,
            )
        provider = self.attachment(spec.provider_key)
        try:
            value = provider.validate_connection(
                schema_version=spec.schema_version,
                parameters=spec.parameters,
            )
            normalized = EnvironmentConnectionSpec(
                provider_key=spec.provider_key,
                schema_version=spec.schema_version,
                parameters=value.model_dump(mode="json"),
            )
            target_key = validated_target_key(provider.target_key(connection=value))
        except EnvironmentProviderError as error:
            safe = error.safe_projection()
            raise EnvironmentManagementError(safe.code, safe.message, status_code=400) from error
        except (TypeError, ValueError) as error:
            raise EnvironmentManagementError(
                "provider_connection_invalid",
                "The Environment Provider connection is invalid.",
                status_code=400,
            ) from error
        return ValidatedEnvironmentConnection(spec=normalized, value=value, target_key=target_key)


def _entry(
    provider_lock: EnvironmentProviderLock,
    connection_versions: frozenset[str],
) -> EnvironmentProviderCatalogEntry:
    return EnvironmentProviderCatalogEntry(
        provider_key=provider_lock.provider_key,
        connection_versions=tuple(sorted(connection_versions)),
        provider_lock=provider_lock,
    )


def _provider_lock(registration: EnvironmentProviderRegistration) -> EnvironmentProviderLock:
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
    return EnvironmentProviderLock(
        provider_key=registration.provider_key,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        builtin=registration.builtin,
        registration_digest_sha256=digest,
    )
