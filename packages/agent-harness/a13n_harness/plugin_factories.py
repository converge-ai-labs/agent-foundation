"""Explicit package discovery for trusted Harness plugin factories."""

from __future__ import annotations

import importlib.metadata
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue

from a13n_harness.errors import PluginError
from a13n_harness.plugin_configuration import (
    _detach_extension_object,
    _detach_json_object,
    _validate_identifier,
)
from a13n_harness.plugins import AbstractHarnessPlugin

HARNESS_PLUGIN_ENTRY_POINT_GROUP = "a13n_harness.plugins"
_MAX_PLUGIN_KEY_LENGTH = 200
_MAX_DIAGNOSTIC_VALUE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class HarnessPluginFactoryReference:
    """Installed entry-point metadata without importing its target."""

    plugin_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class HarnessPluginFactoryRegistration:
    """One validated factory and its process-local provenance."""

    plugin_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class HarnessPluginFactoryContext:
    """Detached standardized envelope supplied to one plugin factory invocation."""

    plugin_key: str
    plugin_id: str
    configuration: Mapping[str, JsonValue]
    extensions: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        try:
            plugin_key = _validate_identifier(self.plugin_key, field_name="plugin_key")
            plugin_id = _validate_identifier(self.plugin_id, field_name="plugin_id")
            configuration = _detach_json_object(self.configuration, field_name="factory configuration")
            extensions = _detach_extension_object(self.extensions)
        except PluginError:
            raise PluginError(
                "Harness plugin factory context must contain bounded finite JSON.",
                code="plugin_factory_context_invalid",
            ) from None
        object.__setattr__(self, "plugin_key", plugin_key)
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "configuration", configuration)
        object.__setattr__(self, "extensions", extensions)


class HarnessPluginFactory(ABC):
    """Trusted package boundary that creates concrete Harness plugins."""

    @classmethod
    @abstractmethod
    def plugin_key(cls) -> str:
        """Return the stable configuration-facing factory key."""

    @abstractmethod
    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        """Create one concrete plugin from the standardized factory context."""


class HarnessPluginFactoryCatalog(Mapping[str, HarnessPluginFactory]):
    """Immutable snapshot of selected Harness plugin factories."""

    __slots__ = ("_factories", "_registrations")

    def __init__(
        self,
        entries: Sequence[tuple[HarnessPluginFactoryRegistration, HarnessPluginFactory]],
    ) -> None:
        registrations = tuple(registration for registration, _factory in entries)
        factories = {registration.plugin_key: factory for registration, factory in entries}
        if len(factories) != len(entries):
            raise PluginError(
                "Harness plugin factory keys must be unique.",
                code="plugin_factory_duplicate",
            )
        self._registrations = registrations
        self._factories = MappingProxyType(factories)

    def __getitem__(self, plugin_key: str) -> HarnessPluginFactory:
        return self._factories[plugin_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._factories)

    def __len__(self) -> int:
        return len(self._factories)

    @property
    def registrations(self) -> tuple[HarnessPluginFactoryRegistration, ...]:
        """Return registrations in selected-then-explicit order."""

        return self._registrations

    def require(self, plugin_key: str) -> HarnessPluginFactory:
        """Return one selected factory or fail with a stable plugin error."""

        key = _validate_factory_key(plugin_key)
        factory = self._factories.get(key)
        if factory is None:
            raise PluginError(
                "The required Harness plugin factory is not selected.",
                code="plugin_factory_missing",
                details={"plugin_key": key},
            )
        return factory

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        """Invoke one selected factory and validate its public return boundary."""

        if not isinstance(context, HarnessPluginFactoryContext):
            raise PluginError(
                "Harness plugin factory requires a HarnessPluginFactoryContext.",
                code="plugin_factory_context_invalid",
            )
        key = _validate_factory_key(context.plugin_key)
        factory = self.require(key)
        detached_context = HarnessPluginFactoryContext(
            plugin_key=key,
            plugin_id=context.plugin_id,
            configuration=context.configuration,
            extensions=context.extensions,
        )
        details: dict[str, JsonValue] = {
            "plugin_key": key,
            "plugin_id": detached_context.plugin_id,
        }
        try:
            plugin = factory.create_plugin(detached_context)
        except Exception:
            raise PluginError(
                "Harness plugin factory failed.",
                code="plugin_factory_failed",
                details=details,
            ) from None
        if not isinstance(plugin, AbstractHarnessPlugin):
            raise PluginError(
                "Harness plugin factory returned an invalid plugin.",
                code="plugin_factory_result_invalid",
                details=details,
            )
        try:
            result_plugin_id = plugin.plugin_id
        except Exception:
            raise PluginError(
                "Harness plugin factory returned a plugin with an invalid ID.",
                code="plugin_factory_result_invalid",
                details=details,
            ) from None
        if result_plugin_id != detached_context.plugin_id:
            raise PluginError(
                "Harness plugin factory result ID does not match the requested plugin ID.",
                code="plugin_factory_result_invalid",
                details=details,
            )
        return plugin


def discover_harness_plugin_factory_references() -> tuple[HarnessPluginFactoryReference, ...]:
    """Discover deterministic plugin metadata without importing target code."""

    references = tuple(_entry_point_reference(entry_point) for entry_point in _entry_points())
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.plugin_key,
                item.distribution_name or "",
                item.distribution_version or "",
                item.import_target,
            ),
        )
    )


def build_harness_plugin_factory_catalog(
    *,
    plugin_keys: Iterable[str] = (),
    explicit_factories: Iterable[HarnessPluginFactory] = (),
) -> HarnessPluginFactoryCatalog:
    """Load an immutable catalog without importing unselected entry points."""

    selected = tuple(_validate_factory_key(value) for value in plugin_keys)
    _require_unique_keys(selected)

    explicit_entries: list[tuple[HarnessPluginFactoryRegistration, HarnessPluginFactory]] = []
    explicit_keys: set[str] = set()
    for factory in explicit_factories:
        if not isinstance(factory, HarnessPluginFactory):
            raise PluginError(
                "Explicit Harness plugin factories must implement HarnessPluginFactory.",
                code="plugin_factory_target_invalid",
            )
        key = _factory_key(factory)
        if key in explicit_keys:
            raise PluginError(
                "A Harness plugin factory key was supplied more than once.",
                code="plugin_factory_duplicate",
                details={"plugin_key": key},
            )
        explicit_keys.add(key)
        explicit_entries.append((_explicit_registration(key, factory), factory))

    collisions = sorted(set(selected) & explicit_keys)
    if collisions:
        raise PluginError(
            "Selected and explicit Harness plugin factories have colliding keys.",
            code="plugin_factory_duplicate",
            details={"plugin_key": collisions[0]},
        )

    selected_entries: list[tuple[HarnessPluginFactoryRegistration, HarnessPluginFactory]] = []
    if selected:
        discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
        selected_set = set(selected)
        for entry_point in _entry_points():
            entry_point_name = _entry_point_name(entry_point)
            if entry_point_name in selected_set:
                discovered.setdefault(entry_point_name, []).append(entry_point)

        missing = [key for key in selected if key not in discovered]
        if missing:
            raise PluginError(
                "A selected Harness plugin factory entry point was not found.",
                code="plugin_factory_missing",
                details={"plugin_key": missing[0]},
            )
        for key in selected:
            matches = discovered[key]
            if len(matches) != 1:
                raise PluginError(
                    "A Harness plugin factory key is supplied by more than one distribution.",
                    code="plugin_factory_duplicate",
                    details={"plugin_key": key},
                )

        for key in selected:
            selected_entries.append(_load_entry_point(key, discovered[key][0]))

    return HarnessPluginFactoryCatalog((*selected_entries, *explicit_entries))


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    try:
        return tuple(importlib.metadata.entry_points(group=HARNESS_PLUGIN_ENTRY_POINT_GROUP))
    except Exception:
        raise PluginError(
            "Harness plugin entry-point metadata could not be enumerated.",
            code="plugin_factory_load_failed",
        ) from None


def _entry_point_name(entry_point: importlib.metadata.EntryPoint) -> str:
    try:
        return entry_point.name
    except Exception:
        raise PluginError(
            "Harness plugin entry-point metadata could not be read.",
            code="plugin_factory_load_failed",
        ) from None


def _entry_point_reference(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None = None,
) -> HarnessPluginFactoryReference:
    try:
        plugin_key = entry_point.name
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
        raise PluginError(
            "Harness plugin entry-point metadata could not be read.",
            code="plugin_factory_load_failed",
            details=_metadata_failure_details(entry_point, selected_key=selected_key),
        ) from None
    return HarnessPluginFactoryReference(
        plugin_key=plugin_key,
        import_target=import_target,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def _load_entry_point(
    plugin_key: str,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[HarnessPluginFactoryRegistration, HarnessPluginFactory]:
    reference = _entry_point_reference(entry_point, selected_key=plugin_key)
    try:
        loaded = entry_point.load()
    except Exception:
        raise PluginError(
            "A selected Harness plugin factory could not be loaded.",
            code="plugin_factory_load_failed",
            details=_reference_details(reference),
        ) from None
    if not isinstance(loaded, type) or not issubclass(loaded, HarnessPluginFactory):
        raise PluginError(
            "A Harness plugin entry point must load a HarnessPluginFactory class.",
            code="plugin_factory_target_invalid",
            details=_reference_details(reference),
        )
    factory_type = cast(type[HarnessPluginFactory], loaded)
    try:
        factory = factory_type()
    except Exception:
        raise PluginError(
            "A Harness plugin factory class must support safe no-argument construction.",
            code="plugin_factory_load_failed",
            details=_reference_details(reference),
        ) from None
    actual_key = _factory_key(factory, reference=reference)
    if actual_key != plugin_key:
        raise PluginError(
            "Harness plugin entry-point name and factory key do not match.",
            code="plugin_factory_key_invalid",
            details=_reference_details(reference),
        )
    registration = HarnessPluginFactoryRegistration(
        plugin_key=plugin_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=reference.import_target,
        distribution_name=reference.distribution_name,
        distribution_version=reference.distribution_version,
    )
    return registration, factory


def _factory_key(
    factory: HarnessPluginFactory,
    *,
    reference: HarnessPluginFactoryReference | None = None,
) -> str:
    try:
        return _validate_factory_key(factory.plugin_key())
    except PluginError:
        details = _reference_details(reference) if reference is not None else None
        raise PluginError(
            "Harness plugin factory key is invalid.",
            code="plugin_factory_key_invalid",
            details=details,
        ) from None
    except Exception:
        details = _reference_details(reference) if reference is not None else None
        raise PluginError(
            "Harness plugin factory key could not be read.",
            code="plugin_factory_key_invalid",
            details=details,
        ) from None


def _explicit_registration(
    plugin_key: str,
    factory: HarnessPluginFactory,
) -> HarnessPluginFactoryRegistration:
    factory_type = type(factory)
    return HarnessPluginFactoryRegistration(
        plugin_key=plugin_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=None,
        distribution_name=None,
        distribution_version=None,
    )


def _validate_factory_key(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > _MAX_PLUGIN_KEY_LENGTH:
        raise PluginError(
            "Harness plugin factory keys must be bounded non-blank strings without surrounding whitespace.",
            code="plugin_factory_key_invalid",
        )
    return value


def _require_unique_keys(keys: Sequence[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise PluginError(
                "A Harness plugin factory key was selected more than once.",
                code="plugin_factory_duplicate",
                details={"plugin_key": key},
            )
        seen.add(key)


def _metadata_failure_details(
    entry_point: importlib.metadata.EntryPoint,
    *,
    selected_key: str | None,
) -> dict[str, JsonValue] | None:
    plugin_key = selected_key
    if plugin_key is None:
        try:
            candidate = entry_point.name
        except Exception:
            candidate = None
        if isinstance(candidate, str) and candidate:
            plugin_key = candidate
    if plugin_key is None:
        return None
    return {"plugin_key": plugin_key[:_MAX_DIAGNOSTIC_VALUE_LENGTH]}


def _reference_details(reference: HarnessPluginFactoryReference) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "plugin_key": reference.plugin_key[:_MAX_DIAGNOSTIC_VALUE_LENGTH],
    }
    if reference.distribution_name is not None:
        details["distribution_name"] = reference.distribution_name[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    if reference.distribution_version is not None:
        details["distribution_version"] = reference.distribution_version[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    return details
