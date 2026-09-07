"""Explicit package discovery for trusted Environment extension factories."""

from __future__ import annotations

import importlib.metadata
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_harness._json import require_finite_json

from .extensions import EnvironmentRunExtension
from .models import EnvironmentError

ENVIRONMENT_RUN_EXTENSION_ENTRY_POINT_GROUP = "a13n_harness.environment_run_extensions"
_MAX_EXTENSION_IDENTIFIER_LENGTH = 200
_MAX_DIAGNOSTIC_VALUE_LENGTH = 200
_CONFIGURATION_ADAPTER = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class EnvironmentRunExtensionFactoryReference:
    """Installed entry-point metadata without importing its target."""

    extension_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentRunExtensionFactoryRegistration:
    """One validated extension factory and its process-local provenance."""

    extension_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentRunExtensionFactoryContext:
    """Detached Host-supplied input for one extension factory invocation."""

    extension_key: str
    extension_id: str
    configuration: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        try:
            extension_key = _validate_extension_key(self.extension_key)
            extension_id = _validate_extension_id(self.extension_id)
            configuration = _CONFIGURATION_ADAPTER.validate_python(self.configuration)
            require_finite_json(configuration)
        except (EnvironmentError, ValidationError, ValueError):
            raise EnvironmentError(
                "Environment run extension factory context must contain bounded identifiers and finite JSON configuration.",
                code="environment_extension_factory_context_invalid",
            ) from None
        object.__setattr__(self, "extension_key", extension_key)
        object.__setattr__(self, "extension_id", extension_id)
        object.__setattr__(self, "configuration", MappingProxyType(configuration))


class EnvironmentRunExtensionFactory(ABC):
    """Trusted factory for one fresh pre-entry-inert Environment run extension."""

    @classmethod
    @abstractmethod
    def extension_key(cls) -> str:
        """Return the stable Host-facing extension key."""

    @abstractmethod
    def create_extension(
        self,
        context: EnvironmentRunExtensionFactoryContext,
    ) -> EnvironmentRunExtension:
        """Create one fresh extension without acquiring external resources."""


class EnvironmentRunExtensionFactoryCatalog(Mapping[str, EnvironmentRunExtensionFactory]):
    """Immutable caller-owned snapshot of selected Environment extension factories."""

    __slots__ = ("_factories", "_registrations")

    def __init__(
        self,
        entries: Sequence[tuple[EnvironmentRunExtensionFactoryRegistration, EnvironmentRunExtensionFactory]],
    ) -> None:
        registrations = tuple(registration for registration, _factory in entries)
        factories = {registration.extension_key: factory for registration, factory in entries}
        if len(factories) != len(entries):
            raise EnvironmentError(
                "Environment extension factory keys must be unique.",
                code="environment_extension_factory_duplicate",
            )
        self._registrations = registrations
        self._factories = MappingProxyType(factories)

    def __getitem__(self, extension_key: str) -> EnvironmentRunExtensionFactory:
        return self._factories[extension_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._factories)

    def __len__(self) -> int:
        return len(self._factories)

    @property
    def registrations(self) -> tuple[EnvironmentRunExtensionFactoryRegistration, ...]:
        """Return registrations in selected-then-explicit order."""

        return self._registrations

    def require(self, extension_key: str) -> EnvironmentRunExtensionFactory:
        """Return one selected extension factory or fail with a stable Environment error."""

        key = _validate_extension_key(extension_key)
        factory = self._factories.get(key)
        if factory is None:
            raise EnvironmentError(
                "The required Environment extension factory is not selected.",
                code="environment_extension_factory_missing",
                details={"extension_key": key},
            )
        return factory

    def create_extension(
        self,
        context: EnvironmentRunExtensionFactoryContext,
    ) -> EnvironmentRunExtension:
        """Invoke one selected factory and validate its public return boundary."""

        if not isinstance(context, EnvironmentRunExtensionFactoryContext):
            raise EnvironmentError(
                "Environment run extension factory context is invalid.",
                code="environment_extension_factory_context_invalid",
            )
        detached_context = EnvironmentRunExtensionFactoryContext(
            extension_key=context.extension_key,
            extension_id=context.extension_id,
            configuration=context.configuration,
        )
        key = detached_context.extension_key
        factory = self.require(key)
        details: dict[str, JsonValue] = {
            "extension_key": key,
            "extension_id": detached_context.extension_id,
        }
        try:
            extension = factory.create_extension(detached_context)
        except Exception:
            raise EnvironmentError(
                "Environment run extension factory failed.",
                code="environment_extension_factory_failed",
                details=details,
            ) from None
        if not isinstance(extension, EnvironmentRunExtension):
            raise EnvironmentError(
                "Environment run extension factory returned an invalid extension.",
                code="environment_extension_factory_result_invalid",
                details=details,
            )
        try:
            actual_id = _validate_extension_id(extension.extension_id)
        except Exception:
            raise EnvironmentError(
                "Environment run extension factory returned an invalid extension.",
                code="environment_extension_factory_result_invalid",
                details=details,
            ) from None
        if actual_id != detached_context.extension_id:
            raise EnvironmentError(
                "Environment run extension factory returned an extension with a mismatched ID.",
                code="environment_extension_factory_result_invalid",
                details=details,
            )
        return extension


def discover_environment_run_extension_factory_references() -> tuple[EnvironmentRunExtensionFactoryReference, ...]:
    """Discover deterministic extension-factory metadata without importing target code."""

    references = tuple(_entry_point_reference(entry_point) for entry_point in _entry_points())
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.extension_key,
                item.distribution_name or "",
                item.distribution_version or "",
                item.import_target,
            ),
        )
    )


def build_environment_run_extension_factory_catalog(
    *,
    extension_keys: Iterable[str] = (),
    explicit_factories: Iterable[EnvironmentRunExtensionFactory] = (),
) -> EnvironmentRunExtensionFactoryCatalog:
    """Load an immutable catalog without importing unselected entry points."""

    selected = tuple(_validate_extension_key(value) for value in extension_keys)
    _require_unique_keys(selected)

    explicit_entries: list[tuple[EnvironmentRunExtensionFactoryRegistration, EnvironmentRunExtensionFactory]] = []
    explicit_keys: set[str] = set()
    for factory in explicit_factories:
        if not isinstance(factory, EnvironmentRunExtensionFactory):
            raise EnvironmentError(
                "Explicit Environment extension factories must implement EnvironmentRunExtensionFactory.",
                code="environment_extension_factory_target_invalid",
            )
        key = _extension_key(factory)
        if key in explicit_keys:
            raise EnvironmentError(
                "An Environment extension factory key was supplied more than once.",
                code="environment_extension_factory_duplicate",
                details={"extension_key": key},
            )
        explicit_keys.add(key)
        explicit_entries.append((_explicit_registration(key, factory), factory))

    collisions = sorted(set(selected) & explicit_keys)
    if collisions:
        raise EnvironmentError(
            "Selected and explicit Environment extension factories have colliding keys.",
            code="environment_extension_factory_duplicate",
            details={"extension_key": collisions[0]},
        )

    selected_entries: list[tuple[EnvironmentRunExtensionFactoryRegistration, EnvironmentRunExtensionFactory]] = []
    if selected:
        discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
        selected_set = set(selected)
        for entry_point in _entry_points():
            entry_point_name = _entry_point_name(entry_point)
            if entry_point_name in selected_set:
                discovered.setdefault(entry_point_name, []).append(entry_point)

        missing = [key for key in selected if key not in discovered]
        if missing:
            raise EnvironmentError(
                "A selected Environment extension factory entry point was not found.",
                code="environment_extension_factory_missing",
                details={"extension_key": missing[0]},
            )
        for key in selected:
            matches = discovered[key]
            if len(matches) != 1:
                raise EnvironmentError(
                    "An Environment extension factory key is supplied by more than one distribution.",
                    code="environment_extension_factory_duplicate",
                    details={"extension_key": key},
                )

        for key in selected:
            selected_entries.append(_load_entry_point(key, discovered[key][0]))

    return EnvironmentRunExtensionFactoryCatalog((*selected_entries, *explicit_entries))


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    try:
        return tuple(importlib.metadata.entry_points(group=ENVIRONMENT_RUN_EXTENSION_ENTRY_POINT_GROUP))
    except Exception:
        raise EnvironmentError(
            "Environment run extension entry-point metadata could not be enumerated.",
            code="environment_extension_factory_load_failed",
        ) from None


def _entry_point_name(entry_point: importlib.metadata.EntryPoint) -> str:
    try:
        return entry_point.name
    except Exception:
        raise EnvironmentError(
            "Environment run extension entry-point metadata could not be read.",
            code="environment_extension_factory_load_failed",
        ) from None


def _entry_point_reference(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None = None,
) -> EnvironmentRunExtensionFactoryReference:
    try:
        extension_key = entry_point.name
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
        raise EnvironmentError(
            "Environment run extension entry-point metadata could not be read.",
            code="environment_extension_factory_load_failed",
            details=_metadata_failure_details(entry_point, selected_key=selected_key),
        ) from None
    return EnvironmentRunExtensionFactoryReference(
        extension_key=extension_key,
        import_target=import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def _load_entry_point(
    extension_key: str,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[EnvironmentRunExtensionFactoryRegistration, EnvironmentRunExtensionFactory]:
    reference = _entry_point_reference(entry_point, selected_key=extension_key)
    try:
        loaded = entry_point.load()
    except Exception:
        raise EnvironmentError(
            "A selected Environment extension factory could not be loaded.",
            code="environment_extension_factory_load_failed",
            details=_reference_details(reference),
        ) from None
    if not isinstance(loaded, type) or not issubclass(loaded, EnvironmentRunExtensionFactory):
        raise EnvironmentError(
            "An Environment extension factory entry point must load an EnvironmentRunExtensionFactory class.",
            code="environment_extension_factory_target_invalid",
            details=_reference_details(reference),
        )
    factory_type = cast(type[EnvironmentRunExtensionFactory], loaded)
    try:
        factory = factory_type()
    except Exception:
        raise EnvironmentError(
            "An Environment extension factory class must support safe no-argument construction.",
            code="environment_extension_factory_load_failed",
            details=_reference_details(reference),
        ) from None
    actual_key = _extension_key(factory, reference=reference)
    if actual_key != extension_key:
        raise EnvironmentError(
            "Environment extension factory entry-point name and extension key do not match.",
            code="environment_extension_factory_key_invalid",
            details=_reference_details(reference),
        )
    registration = EnvironmentRunExtensionFactoryRegistration(
        extension_key=extension_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=reference.import_target,
        distribution_name=reference.distribution_name,
        distribution_version=reference.distribution_version,
    )
    return registration, factory


def _extension_key(
    factory: EnvironmentRunExtensionFactory,
    *,
    reference: EnvironmentRunExtensionFactoryReference | None = None,
) -> str:
    try:
        return _validate_extension_key(factory.extension_key())
    except EnvironmentError:
        details = _reference_details(reference) if reference is not None else None
        raise EnvironmentError(
            "Environment extension factory key is invalid.",
            code="environment_extension_factory_key_invalid",
            details=details,
        ) from None
    except Exception:
        details = _reference_details(reference) if reference is not None else None
        raise EnvironmentError(
            "Environment extension factory key could not be read.",
            code="environment_extension_factory_key_invalid",
            details=details,
        ) from None


def _explicit_registration(
    extension_key: str,
    factory: EnvironmentRunExtensionFactory,
) -> EnvironmentRunExtensionFactoryRegistration:
    factory_type = type(factory)
    return EnvironmentRunExtensionFactoryRegistration(
        extension_key=extension_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=None,
        distribution_name=None,
        distribution_version=None,
    )


def _validate_extension_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_EXTENSION_IDENTIFIER_LENGTH
    ):
        raise EnvironmentError(
            "Environment extension factory keys must be bounded non-blank strings without surrounding whitespace.",
            code="environment_extension_factory_key_invalid",
        )
    return value


def _validate_extension_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_EXTENSION_IDENTIFIER_LENGTH
    ):
        raise EnvironmentError(
            "Environment run extension IDs must be bounded non-blank strings without surrounding whitespace.",
            code="environment_extension_id_invalid",
        )
    return value


def _require_unique_keys(keys: Sequence[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise EnvironmentError(
                "An Environment extension factory key was selected more than once.",
                code="environment_extension_factory_duplicate",
                details={"extension_key": key},
            )
        seen.add(key)


def _metadata_failure_details(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None,
) -> dict[str, JsonValue] | None:
    extension_key = selected_key
    if extension_key is None:
        try:
            candidate = entry_point.name
        except Exception:
            candidate = None
        if isinstance(candidate, str) and candidate:
            extension_key = candidate
    if extension_key is None:
        return None
    return {"extension_key": extension_key[:_MAX_DIAGNOSTIC_VALUE_LENGTH]}


def _reference_details(reference: EnvironmentRunExtensionFactoryReference) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "extension_key": reference.extension_key[:_MAX_DIAGNOSTIC_VALUE_LENGTH],
    }
    if reference.distribution_name is not None:
        details["distribution_name"] = reference.distribution_name[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    if reference.distribution_version is not None:
        details["distribution_version"] = reference.distribution_version[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    return details
