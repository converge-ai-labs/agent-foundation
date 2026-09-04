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
from pydantic import BaseModel, TypeAdapter

from .domain import (
    EnvironmentConnectionSpec,
    EnvironmentProviderCatalogEntry,
    EnvironmentProviderLock,
    EnvironmentTargetIdentity,
    EnvironmentTargetRetentionBehavior,
    SchemaVersion,
    environment_target_identity_digest,
)
from .errors import EnvironmentManagementError, environment_provider_not_found
from .providers import (
    FoundationBuiltinEnvironmentProviderAdapter,
    FoundationEnvironmentAttachProvider,
    FoundationEnvironmentRetentionProvider,
)


@dataclass(frozen=True, slots=True)
class ValidatedEnvironmentConnection:
    spec: EnvironmentConnectionSpec
    value: BaseModel
    identity: EnvironmentTargetIdentity
    target_identity_digest_sha256: str
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
        TypeAdapter(SchemaVersion).validate_python(self.provider.identity_schema_version)
        behavior = EnvironmentTargetRetentionBehavior(self.provider.retention_behavior)
        if behavior is EnvironmentTargetRetentionBehavior.while_execution_active and not isinstance(
            self.provider, FoundationEnvironmentRetentionProvider
        ):
            raise ValueError("Foundation Environment Provider requiring retention must expose ensure_retained_until")


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
            entries[key] = _entry(
                registration.provider_lock,
                provider.connection_versions,
                identity_schema_version=provider.identity_schema_version,
                retention_behavior=EnvironmentTargetRetentionBehavior(provider.retention_behavior),
            )
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
            generic_provider = providers[key]
            generic_registration = generic_registrations[key]
            if isinstance(generic_provider, FoundationEnvironmentAttachProvider):
                provider: FoundationEnvironmentAttachProvider = generic_provider
            elif generic_registration.builtin:
                provider = FoundationBuiltinEnvironmentProviderAdapter.from_provider(generic_provider)
            else:
                raise ValueError(f"Environment Provider {key!r} has no Foundation attachment capability")
            if provider.provider_key != key:
                raise ValueError(f"Environment Provider {key!r} has no Foundation attachment capability")
            registrations.append(
                FoundationEnvironmentProviderRegistration(
                    provider=provider,
                    provider_lock=_provider_lock(generic_registration),
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
            identity = TypeAdapter(EnvironmentTargetIdentity).validate_python(
                provider.target_identity(connection=value)
            )
            target_digest = environment_target_identity_digest(
                provider_key=entry.provider_key,
                identity_schema_version=entry.identity_schema_version,
                identity=identity,
            )
        except EnvironmentProviderError as error:
            safe = error.safe_projection()
            raise EnvironmentManagementError(safe.code, safe.message, status_code=400) from error
        except (TypeError, ValueError) as error:
            raise EnvironmentManagementError(
                "provider_connection_invalid",
                "The Environment Provider connection is invalid.",
                status_code=400,
            ) from error
        return ValidatedEnvironmentConnection(
            spec=normalized,
            value=value,
            identity=identity,
            target_identity_digest_sha256=target_digest,
            target_key=identity.target_key,
        )


def _entry(
    provider_lock: EnvironmentProviderLock,
    connection_versions: frozenset[str],
    *,
    identity_schema_version: str,
    retention_behavior: EnvironmentTargetRetentionBehavior,
) -> EnvironmentProviderCatalogEntry:
    return EnvironmentProviderCatalogEntry(
        provider_key=provider_lock.provider_key,
        connection_versions=tuple(sorted(connection_versions)),
        identity_schema_version=identity_schema_version,
        retention_behavior=retention_behavior,
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
