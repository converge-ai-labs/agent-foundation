"""Explicit discovery and trust selection for Connector Providers."""

from __future__ import annotations

import importlib.metadata
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from .errors import ConnectorProviderError
from .provider import (
    ConnectorConnectionProvider,
    ConnectorEventProvider,
    ConnectorPollingProvider,
    ConnectorProvider,
    ConnectorProviderMetadata,
    ConnectorToolProvider,
    ConnectorWebhookProvider,
)

CONNECTOR_PROVIDER_ENTRY_POINT_GROUP = "a13n_service.connector_providers"
_MAX_KEY_LENGTH = 200
_MAX_METADATA_TEXT_LENGTH = 2_000
_MAX_SCHEMA_VERSIONS = 100
_MAX_DIAGNOSTIC_LENGTH = 200


class ConnectorProviderTrust(BaseModel):
    """One deployment-selected Provider and its exact installed artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_key: str = Field(min_length=1, max_length=_MAX_KEY_LENGTH)
    distribution_name: str = Field(min_length=1, max_length=_MAX_KEY_LENGTH)
    distribution_version: str = Field(min_length=1, max_length=_MAX_KEY_LENGTH)

    @field_validator("provider_key", "distribution_name", "distribution_version")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must not contain surrounding whitespace")
        return value


@dataclass(frozen=True, slots=True)
class ConnectorProviderReference:
    """Installed entry-point metadata read without importing Provider code."""

    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class ConnectorProviderRegistration:
    """One selected Provider and its verified process-local provenance."""

    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str
    distribution_name: str
    distribution_version: str
    metadata: ConnectorProviderMetadata


class ConnectorProviderCatalog(Mapping[str, ConnectorProvider]):
    """Immutable catalog of deployment-trusted Connector Providers."""

    __slots__ = ("_providers", "_registrations")

    def __init__(
        self,
        entries: Sequence[tuple[ConnectorProviderRegistration, ConnectorProvider]],
    ) -> None:
        providers = {registration.provider_key: provider for registration, provider in entries}
        if len(providers) != len(entries):
            raise ConnectorProviderError(
                "Connector Provider keys must be unique.",
                code="provider_duplicate",
            )
        self._providers = MappingProxyType(providers)
        self._registrations = tuple(registration for registration, _provider in entries)

    def __getitem__(self, provider_key: str) -> ConnectorProvider:
        return self._providers[provider_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    @property
    def registrations(self) -> tuple[ConnectorProviderRegistration, ...]:
        return self._registrations

    def require(self, provider_key: str) -> ConnectorProvider:
        key = _validate_provider_key(provider_key)
        provider = self._providers.get(key)
        if provider is None:
            raise ConnectorProviderError(
                "The required Connector Provider is unavailable.",
                code="provider_unavailable",
                details={"provider_key": key},
            )
        return provider

    def registration(self, provider_key: str) -> ConnectorProviderRegistration:
        key = _validate_provider_key(provider_key)
        for registration in self._registrations:
            if registration.provider_key == key:
                return registration
        raise ConnectorProviderError(
            "The required Connector Provider is unavailable.",
            code="provider_unavailable",
            details={"provider_key": key},
        )


def discover_connector_provider_references() -> tuple[ConnectorProviderReference, ...]:
    """Return deterministic installed metadata without importing target code."""

    references = tuple(_entry_point_reference(entry_point) for entry_point in _entry_points())
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.provider_key,
                item.distribution_name or "",
                item.distribution_version or "",
                item.import_target,
            ),
        )
    )


def build_connector_provider_catalog(
    trusted: Sequence[ConnectorProviderTrust],
) -> ConnectorProviderCatalog:
    """Load only exact deployment-selected entry points into one catalog."""

    selections = tuple(trusted)
    _require_unique_selections(selections)
    if not selections:
        return ConnectorProviderCatalog(())

    selected_keys = {selection.provider_key for selection in selections}
    discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
    for entry_point in _entry_points():
        key = _entry_point_name(entry_point)
        if key in selected_keys:
            discovered.setdefault(key, []).append(entry_point)

    for selection in selections:
        matches = discovered.get(selection.provider_key, [])
        if not matches:
            raise ConnectorProviderError(
                "A selected Connector Provider entry point was not found.",
                code="provider_unavailable",
                details={"provider_key": selection.provider_key},
            )
        if len(matches) != 1:
            raise ConnectorProviderError(
                "A Connector Provider key is supplied by more than one distribution.",
                code="provider_duplicate",
                details={"provider_key": selection.provider_key},
            )

    entries = tuple(_load_entry_point(selection, discovered[selection.provider_key][0]) for selection in selections)
    return ConnectorProviderCatalog(entries)


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    try:
        return tuple(importlib.metadata.entry_points(group=CONNECTOR_PROVIDER_ENTRY_POINT_GROUP))
    except Exception:
        raise ConnectorProviderError(
            "Connector Provider entry-point metadata could not be enumerated.",
            code="provider_load_failed",
        ) from None


def _entry_point_name(entry_point: importlib.metadata.EntryPoint) -> str:
    try:
        return _validate_provider_key(entry_point.name)
    except Exception:
        raise ConnectorProviderError(
            "Connector Provider entry-point metadata could not be read.",
            code="provider_load_failed",
        ) from None


def _entry_point_reference(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None = None,
) -> ConnectorProviderReference:
    try:
        provider_key = _validate_provider_key(entry_point.name)
        import_target = entry_point.value
        distribution = entry_point.dist
        distribution_name: str | None = None
        distribution_version: str | None = None
        if distribution is not None:
            name = distribution.metadata.get("Name")
            if isinstance(name, str) and name:
                distribution_name = name
            version = distribution.version
            if isinstance(version, str) and version:
                distribution_version = version
    except Exception:
        raise ConnectorProviderError(
            "Connector Provider entry-point metadata could not be read.",
            code="provider_load_failed",
            details=_metadata_failure_details(entry_point, selected_key=selected_key),
        ) from None
    return ConnectorProviderReference(
        provider_key=provider_key,
        import_target=import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def _load_entry_point(
    trust: ConnectorProviderTrust,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[ConnectorProviderRegistration, ConnectorProvider]:
    reference = _entry_point_reference(entry_point, selected_key=trust.provider_key)
    _verify_artifact(trust, reference)
    try:
        loaded = entry_point.load()
    except Exception:
        raise ConnectorProviderError(
            "A selected Connector Provider could not be loaded.",
            code="provider_load_failed",
            details=_reference_details(reference),
        ) from None
    if not isinstance(loaded, type) or not issubclass(loaded, ConnectorProvider):
        raise ConnectorProviderError(
            "A Connector Provider entry point must load a ConnectorProvider class.",
            code="provider_target_invalid",
            details=_reference_details(reference),
        )
    provider_type = cast(type[ConnectorProvider], loaded)
    try:
        provider = provider_type()
    except Exception:
        raise ConnectorProviderError(
            "A Connector Provider class must support safe no-argument construction.",
            code="provider_load_failed",
            details=_reference_details(reference),
        ) from None
    metadata = _provider_metadata(provider, reference)
    registration = ConnectorProviderRegistration(
        provider_key=trust.provider_key,
        class_module=provider_type.__module__,
        class_qualname=provider_type.__qualname__,
        import_target=reference.import_target,
        distribution_name=trust.distribution_name,
        distribution_version=trust.distribution_version,
        metadata=metadata,
    )
    return registration, provider


def _verify_artifact(trust: ConnectorProviderTrust, reference: ConnectorProviderReference) -> None:
    if (
        reference.distribution_name is None
        or _normalize_distribution_name(reference.distribution_name)
        != _normalize_distribution_name(trust.distribution_name)
        or reference.distribution_version != trust.distribution_version
    ):
        raise ConnectorProviderError(
            "The selected Connector Provider does not match its trusted artifact.",
            code="provider_not_trusted",
            details=_reference_details(reference),
        )


def _provider_metadata(
    provider: ConnectorProvider,
    reference: ConnectorProviderReference,
) -> ConnectorProviderMetadata:
    try:
        metadata = provider.metadata
    except Exception:
        raise ConnectorProviderError(
            "Connector Provider metadata could not be read.",
            code="provider_load_failed",
            details=_reference_details(reference),
        ) from None
    if not isinstance(metadata, ConnectorProviderMetadata):
        raise ConnectorProviderError(
            "Connector Provider metadata is invalid.",
            code="provider_metadata_invalid",
            details=_reference_details(reference),
        )
    _validate_metadata(metadata, reference)
    _validate_capability_methods(provider, metadata, reference)
    return _detach_metadata(metadata)


def _validate_metadata(metadata: ConnectorProviderMetadata, reference: ConnectorProviderReference) -> None:
    if (
        not _bounded_text(metadata.display_name)
        or not _bounded_text(metadata.description, allow_empty=True)
        or not _bounded_text(metadata.contract_version)
    ):
        raise _metadata_invalid(reference)
    schemas = metadata.provider_config_schemas
    if not schemas or len(schemas) > _MAX_SCHEMA_VERSIONS:
        raise _metadata_invalid(reference)
    for version, schema in schemas.items():
        if not _bounded_text(version) or not isinstance(schema, Mapping):
            raise _metadata_invalid(reference)
        try:
            encoded = json.dumps(schema, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raise _metadata_invalid(reference) from None
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise _metadata_invalid(reference)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            raise _metadata_invalid(reference) from None
    modes = metadata.connection_setup_modes
    if len(set(modes)) != len(modes) or any(not _bounded_text(mode) for mode in modes):
        raise _metadata_invalid(reference)
    if metadata.capabilities.connections != bool(modes):
        raise _metadata_invalid(reference)


def _validate_capability_methods(
    provider: ConnectorProvider,
    metadata: ConnectorProviderMetadata,
    reference: ConnectorProviderReference,
) -> None:
    if metadata.capabilities.tools != isinstance(provider, ConnectorToolProvider):
        raise _metadata_invalid(reference)
    if metadata.capabilities.connections != isinstance(provider, ConnectorConnectionProvider):
        raise _metadata_invalid(reference)
    if metadata.capabilities.events != isinstance(provider, ConnectorEventProvider):
        raise _metadata_invalid(reference)
    if metadata.capabilities.event_delivery == "webhook" and not isinstance(provider, ConnectorWebhookProvider):
        raise _metadata_invalid(reference)
    if metadata.capabilities.event_delivery == "polling" and not isinstance(provider, ConnectorPollingProvider):
        raise _metadata_invalid(reference)


def _detach_metadata(metadata: ConnectorProviderMetadata) -> ConnectorProviderMetadata:
    schemas = {
        version: json.loads(
            json.dumps(schema, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        for version, schema in metadata.provider_config_schemas.items()
    }
    return ConnectorProviderMetadata(
        display_name=metadata.display_name,
        description=metadata.description,
        contract_version=metadata.contract_version,
        provider_config_schemas=MappingProxyType(schemas),
        capabilities=metadata.capabilities,
        connection_setup_modes=tuple(metadata.connection_setup_modes),
    )


def _metadata_invalid(reference: ConnectorProviderReference) -> ConnectorProviderError:
    return ConnectorProviderError(
        "Connector Provider metadata is invalid.",
        code="provider_metadata_invalid",
        details=_reference_details(reference),
    )


def _bounded_text(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and len(value) <= _MAX_METADATA_TEXT_LENGTH
        and (allow_empty or bool(value))
    )


def _validate_provider_key(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > _MAX_KEY_LENGTH:
        raise ConnectorProviderError(
            "Connector Provider keys must be bounded non-blank strings without surrounding whitespace.",
            code="provider_key_invalid",
        )
    return value


def _require_unique_selections(selections: Sequence[ConnectorProviderTrust]) -> None:
    seen: set[str] = set()
    for selection in selections:
        key = _validate_provider_key(selection.provider_key)
        if key in seen:
            raise ConnectorProviderError(
                "A Connector Provider key was selected more than once.",
                code="provider_duplicate",
                details={"provider_key": key},
            )
        seen.add(key)


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _metadata_failure_details(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None,
) -> dict[str, JsonValue] | None:
    provider_key = selected_key
    if provider_key is None:
        try:
            candidate = entry_point.name
        except Exception:
            candidate = None
        if isinstance(candidate, str) and candidate:
            provider_key = candidate
    if provider_key is None:
        return None
    return {"provider_key": provider_key[:_MAX_DIAGNOSTIC_LENGTH]}


def _reference_details(reference: ConnectorProviderReference) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "provider_key": reference.provider_key[:_MAX_DIAGNOSTIC_LENGTH],
    }
    if reference.distribution_name is not None:
        details["distribution_name"] = reference.distribution_name[:_MAX_DIAGNOSTIC_LENGTH]
    if reference.distribution_version is not None:
        details["distribution_version"] = reference.distribution_version[:_MAX_DIAGNOSTIC_LENGTH]
    return details
