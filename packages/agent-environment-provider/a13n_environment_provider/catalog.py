"""Explicit package discovery for trusted Environment Providers."""

from __future__ import annotations

import importlib.metadata
import inspect
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from .errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from .management import EnvironmentProvider

ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP = "a13n_environment_provider.providers"
_MAX_PROVIDER_KEY_LENGTH = 128
_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
_BUILTIN_PROVIDER_KEYS = frozenset(
    {
        "a13n.direct-local",
        "a13n.local-envd",
        "a13n.docker",
    }
)


@dataclass(frozen=True, slots=True)
class EnvironmentProviderReference:
    """Installed entry-point metadata without importing its target."""

    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderRegistration:
    """One validated Environment Provider and its process-local provenance."""

    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None
    builtin: bool


class EnvironmentProviderCatalog(Mapping[str, EnvironmentProvider]):
    """Immutable caller-owned snapshot of selected Environment Providers."""

    __slots__ = ("_providers", "_registrations")

    def __init__(self) -> None:
        self._providers: Mapping[str, EnvironmentProvider] = MappingProxyType({})
        self._registrations: tuple[EnvironmentProviderRegistration, ...] = ()

    @classmethod
    def _from_validated_entries(
        cls,
        entries: Sequence[tuple[EnvironmentProviderRegistration, EnvironmentProvider]],
    ) -> EnvironmentProviderCatalog:
        providers: dict[str, EnvironmentProvider] = {}
        registrations: list[EnvironmentProviderRegistration] = []
        for registration, provider in entries:
            if not isinstance(registration, EnvironmentProviderRegistration):
                raise _catalog_error(
                    "Environment Provider catalog registrations are invalid.",
                    code="provider_catalog_target_invalid",
                )
            if not isinstance(provider, EnvironmentProvider):
                raise _catalog_error(
                    "Environment Provider catalog entries must implement EnvironmentProvider.",
                    code="provider_catalog_target_invalid",
                    provider_key=registration.provider_key,
                )
            key = _provider_key(provider)
            if registration.provider_key != key:
                raise _catalog_error(
                    "Environment Provider registration and Provider keys do not match.",
                    code="provider_catalog_key_invalid",
                    provider_key=registration.provider_key,
                )
            if key in providers:
                raise _catalog_error(
                    "Environment Provider catalog keys must be unique.",
                    code="provider_catalog_duplicate",
                    provider_key=key,
                )
            providers[key] = provider
            registrations.append(registration)
        catalog = cls()
        catalog._providers = MappingProxyType(providers)
        catalog._registrations = tuple(registrations)
        return catalog

    def __getitem__(self, provider_key: str) -> EnvironmentProvider:
        return self._providers[provider_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    @property
    def registrations(self) -> tuple[EnvironmentProviderRegistration, ...]:
        """Return registrations in built-in, extension, then explicit order."""

        return self._registrations

    def require(self, provider_key: str) -> EnvironmentProvider:
        """Return one selected Provider or fail with a stable Provider error."""

        key = _validate_provider_key(provider_key)
        provider = self._providers.get(key)
        if provider is None:
            raise _catalog_error(
                "The required Environment Provider is not selected.",
                code="provider_catalog_missing",
                provider_key=key,
            )
        return provider


def discover_environment_provider_references() -> tuple[EnvironmentProviderReference, ...]:
    """Discover deterministic Provider metadata without importing target code."""

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


def build_environment_provider_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_providers: Iterable[EnvironmentProvider] = (),
) -> EnvironmentProviderCatalog:
    """Build an immutable catalog without importing unselected entry points."""

    builtins = tuple(_validate_provider_key(value) for value in builtin_keys)
    extensions = tuple(_validate_provider_key(value) for value in extension_keys)
    _require_unique_keys(builtins)
    _require_unique_keys(extensions)

    unknown_builtins = sorted(set(builtins) - _BUILTIN_PROVIDER_KEYS)
    if unknown_builtins:
        raise _catalog_error(
            "A selected built-in Environment Provider does not exist.",
            code="provider_catalog_missing",
            provider_key=unknown_builtins[0],
        )

    explicit_entries: list[tuple[EnvironmentProviderRegistration, EnvironmentProvider]] = []
    explicit_keys: set[str] = set()
    for provider in explicit_providers:
        if not isinstance(provider, EnvironmentProvider):
            raise _catalog_error(
                "Explicit Environment Providers must implement EnvironmentProvider.",
                code="provider_catalog_target_invalid",
            )
        key = _provider_key(provider)
        if key in explicit_keys:
            raise _catalog_error(
                "An explicit Environment Provider key was supplied more than once.",
                code="provider_catalog_duplicate",
                provider_key=key,
            )
        explicit_keys.add(key)
        explicit_entries.append((_registration(key, provider, builtin=False), provider))

    selected_groups = (set(builtins), set(extensions), explicit_keys)
    collisions = sorted(
        key for key in set().union(*selected_groups) if sum(key in group for group in selected_groups) > 1
    )
    if collisions:
        raise _catalog_error(
            "Built-in, extension, and explicit Environment Provider keys must not collide.",
            code="provider_catalog_duplicate",
            provider_key=collisions[0],
        )

    discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
    if extensions:
        selected_extensions = set(extensions)
        for entry_point in _entry_points():
            entry_point_name = _entry_point_name(entry_point)
            if entry_point_name in selected_extensions:
                discovered.setdefault(entry_point_name, []).append(entry_point)

        missing = [key for key in extensions if key not in discovered]
        if missing:
            raise _catalog_error(
                "A selected Environment Provider entry point was not found.",
                code="provider_catalog_missing",
                provider_key=missing[0],
            )
        for key in extensions:
            if len(discovered[key]) != 1:
                raise _catalog_error(
                    "An Environment Provider key is supplied by more than one distribution.",
                    code="provider_catalog_duplicate",
                    provider_key=key,
                )

    builtin_entries = tuple(_load_builtin_provider(key) for key in builtins)
    extension_entries = tuple(_load_entry_point(key, discovered[key][0]) for key in extensions)
    return EnvironmentProviderCatalog._from_validated_entries((*builtin_entries, *extension_entries, *explicit_entries))


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    try:
        return tuple(importlib.metadata.entry_points(group=ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP))
    except Exception:
        raise _catalog_error(
            "Environment Provider entry-point metadata could not be enumerated.",
            code="provider_catalog_load_failed",
        ) from None


def _entry_point_name(entry_point: importlib.metadata.EntryPoint) -> str:
    try:
        name = entry_point.name
    except Exception:
        raise _catalog_error(
            "Environment Provider entry-point metadata could not be read.",
            code="provider_catalog_load_failed",
        ) from None
    if not isinstance(name, str):
        raise _catalog_error(
            "Environment Provider entry-point metadata is invalid.",
            code="provider_catalog_key_invalid",
        )
    return name


def _entry_point_reference(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None = None,
) -> EnvironmentProviderReference:
    try:
        provider_key = _validate_provider_key(entry_point.name)
        import_target = entry_point.value
        if not isinstance(import_target, str) or not import_target or import_target != import_target.strip():
            raise ValueError("invalid import target")
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
    except EnvironmentProviderError:
        raise
    except Exception:
        raise _catalog_error(
            "Environment Provider entry-point metadata could not be read.",
            code="provider_catalog_load_failed",
            provider_key=_safe_metadata_key(entry_point, selected_key=selected_key),
        ) from None
    return EnvironmentProviderReference(
        provider_key=provider_key,
        import_target=import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def _load_builtin_provider(
    provider_key: str,
) -> tuple[EnvironmentProviderRegistration, EnvironmentProvider]:
    if provider_key == "a13n.direct-local":
        from .direct_local.provider import DirectLocalEnvironmentProvider

        provider_type: type[EnvironmentProvider] = DirectLocalEnvironmentProvider
    elif provider_key == "a13n.local-envd":
        from .local_envd.provider import LocalEnvdEnvironmentProvider

        provider_type = LocalEnvdEnvironmentProvider
    elif provider_key == "a13n.docker":
        from .docker.factory import DockerEnvironmentProvider

        provider_type = DockerEnvironmentProvider
    else:  # pragma: no cover - guarded by catalog preflight
        raise _catalog_error(
            "A selected built-in Environment Provider does not exist.",
            code="provider_catalog_missing",
            provider_key=provider_key,
        )
    try:
        provider = provider_type()
    except Exception:
        raise _catalog_error(
            "A built-in Environment Provider could not be constructed.",
            code="provider_catalog_load_failed",
            provider_key=provider_key,
        ) from None
    actual_key = _provider_key(provider)
    if actual_key != provider_key:
        raise _catalog_error(
            "A built-in Environment Provider key does not match its catalog key.",
            code="provider_catalog_key_invalid",
            provider_key=provider_key,
        )
    return _registration(provider_key, provider, builtin=True), provider


def _load_entry_point(
    provider_key: str,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[EnvironmentProviderRegistration, EnvironmentProvider]:
    reference = _entry_point_reference(entry_point, selected_key=provider_key)
    try:
        loaded = entry_point.load()
    except Exception:
        raise _catalog_error(
            "A selected Environment Provider could not be loaded.",
            code="provider_catalog_load_failed",
            reference=reference,
        ) from None
    if not isinstance(loaded, type) or not issubclass(loaded, EnvironmentProvider) or inspect.isabstract(loaded):
        raise _catalog_error(
            "An Environment Provider entry point must load a concrete EnvironmentProvider class.",
            code="provider_catalog_target_invalid",
            reference=reference,
        )
    provider_type = cast(type[EnvironmentProvider], loaded)
    try:
        provider = provider_type()
    except Exception:
        raise _catalog_error(
            "An Environment Provider class must support safe no-argument construction.",
            code="provider_catalog_load_failed",
            reference=reference,
        ) from None
    actual_key = _provider_key(provider, reference=reference)
    if actual_key != provider_key:
        raise _catalog_error(
            "Environment Provider entry-point name and Provider key do not match.",
            code="provider_catalog_key_invalid",
            reference=reference,
        )
    registration = _registration(
        provider_key,
        provider,
        import_target=reference.import_target,
        distribution_name=reference.distribution_name,
        distribution_version=reference.distribution_version,
        builtin=False,
    )
    return registration, provider


def _provider_key(
    provider: EnvironmentProvider,
    *,
    reference: EnvironmentProviderReference | None = None,
) -> str:
    try:
        return _validate_provider_key(provider.key)
    except EnvironmentProviderError:
        raise _catalog_error(
            "Environment Provider key is invalid.",
            code="provider_catalog_key_invalid",
            reference=reference,
        ) from None
    except Exception:
        raise _catalog_error(
            "Environment Provider key could not be read.",
            code="provider_catalog_key_invalid",
            reference=reference,
        ) from None


def _registration(
    provider_key: str,
    provider: EnvironmentProvider,
    *,
    import_target: str | None = None,
    distribution_name: str | None = None,
    distribution_version: str | None = None,
    builtin: bool,
) -> EnvironmentProviderRegistration:
    provider_type = type(provider)
    return EnvironmentProviderRegistration(
        provider_key=provider_key,
        class_module=provider_type.__module__,
        class_qualname=provider_type.__qualname__,
        import_target=import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
        builtin=builtin,
    )


def _validate_provider_key(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_PROVIDER_KEY_LENGTH or _KEY_PATTERN.fullmatch(value) is None:
        raise _catalog_error(
            "Environment Provider keys must be bounded lowercase namespaced identifiers.",
            code="provider_catalog_key_invalid",
        )
    return value


def _require_unique_keys(keys: Sequence[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise _catalog_error(
                "An Environment Provider key was selected more than once.",
                code="provider_catalog_duplicate",
                provider_key=key,
            )
        seen.add(key)


def _safe_metadata_key(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None,
) -> str | None:
    if selected_key is not None:
        return selected_key
    try:
        candidate = entry_point.name
    except Exception:
        return None
    if (
        isinstance(candidate, str)
        and len(candidate) <= _MAX_PROVIDER_KEY_LENGTH
        and _KEY_PATTERN.fullmatch(candidate) is not None
    ):
        return candidate
    return None


def _catalog_error(
    description: str,
    *,
    code: str,
    provider_key: str | None = None,
    reference: EnvironmentProviderReference | None = None,
) -> EnvironmentProviderError:
    if reference is not None:
        provider_key = reference.provider_key
        distribution_name = _bounded_context_value(reference.distribution_name, 200)
        distribution_version = _bounded_context_value(reference.distribution_version, 100)
    else:
        distribution_name = None
        distribution_version = None
    safe_provider_key = (
        provider_key
        if provider_key is not None
        and len(provider_key) <= _MAX_PROVIDER_KEY_LENGTH
        and _KEY_PATTERN.fullmatch(provider_key) is not None
        else None
    )
    return EnvironmentProviderError(
        description,
        code=code,
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(
            provider_key=safe_provider_key,
            distribution_name=distribution_name,
            distribution_version=distribution_version,
        ),
    )


def _bounded_context_value(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] or None
