from __future__ import annotations

import importlib.metadata
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import NoReturn, cast

from pydantic import BaseModel, ValidationError

from .errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from .management import EnvironmentManager, EnvironmentProviderRuntime
from .models import EnvironmentProviderSpec

ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP = "a13n_environment_provider.providers"
_RESERVED_BUILTIN_KEYS = frozenset(
    {
        "a13n.direct-local",
        "a13n.local-envd",
        "a13n.docker",
        "a13n.e2b",
    }
)


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentProviderSpec:
    spec: EnvironmentProviderSpec
    configuration: BaseModel
    factory: EnvironmentProviderFactory


@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryReference:
    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryRegistration:
    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None


class EnvironmentProviderFactory(ABC):
    """Inert trusted factory for one Environment provider key."""

    @classmethod
    @abstractmethod
    def provider_key(cls) -> str: ...

    @classmethod
    @abstractmethod
    def supported_schema_versions(cls) -> frozenset[str]: ...

    @classmethod
    @abstractmethod
    def configuration_model(cls, schema_version: str) -> type[BaseModel]: ...

    @abstractmethod
    def create_manager(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentManager: ...


class EnvironmentProviderFactoryCatalog(Mapping[str, EnvironmentProviderFactory]):
    """Immutable snapshot of explicitly selected provider factories."""

    __slots__ = ("_factories", "_registrations")

    def __init__(
        self,
        entries: Sequence[tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]],
    ) -> None:
        factories = {registration.provider_key: factory for registration, factory in entries}
        if len(factories) != len(entries):
            _raise_duplicate("Environment provider factory keys must be unique.")
        self._factories = MappingProxyType(factories)
        self._registrations = tuple(registration for registration, _factory in entries)

    def __getitem__(self, provider_key: str) -> EnvironmentProviderFactory:
        return self._factories[provider_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._factories)

    def __len__(self) -> int:
        return len(self._factories)

    @property
    def registrations(self) -> tuple[EnvironmentProviderFactoryRegistration, ...]:
        return self._registrations

    def require(self, provider_key: str) -> EnvironmentProviderFactory:
        key = _validate_provider_key(provider_key)
        factory = self._factories.get(key)
        if factory is None:
            _raise_missing(key, "The required Environment provider factory is not selected.")
        return factory

    def resolve_spec(self, spec: EnvironmentProviderSpec) -> ResolvedEnvironmentProviderSpec:
        if not isinstance(spec, EnvironmentProviderSpec):
            raise EnvironmentProviderError(
                "Provider specifications must be EnvironmentProviderSpec values.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            )
        factory = self.require(spec.provider_key)
        try:
            versions = factory.supported_schema_versions()
        except Exception as exc:
            raise _factory_failure(spec.provider_key, "Factory schema versions could not be read.") from exc
        if not versions or any(not isinstance(version, str) or not version for version in versions):
            raise _factory_target_error(spec.provider_key, "Factory schema versions are invalid.")
        if spec.schema_version not in versions:
            raise EnvironmentProviderError(
                f"Provider {spec.provider_key!r} does not support schema version {spec.schema_version!r}.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(
                    provider_key=spec.provider_key,
                    schema_version=spec.schema_version,
                ),
            )
        try:
            model_type = factory.configuration_model(spec.schema_version)
        except EnvironmentProviderError:
            raise
        except Exception as exc:
            raise _factory_failure(spec.provider_key, "Factory configuration model lookup failed.") from exc
        if (
            not isinstance(model_type, type)
            or not issubclass(model_type, BaseModel)
            or model_type.model_config.get("frozen") is not True
            or model_type.model_config.get("extra") != "forbid"
        ):
            raise _factory_target_error(
                spec.provider_key,
                "Factory configuration models must be frozen strict Pydantic models.",
            )
        try:
            configuration = model_type.model_validate(spec.parameters)
        except ValidationError as exc:
            raise EnvironmentProviderError(
                f"Provider configuration is invalid for {spec.provider_key!r} schema {spec.schema_version!r}.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(
                    provider_key=spec.provider_key,
                    schema_version=spec.schema_version,
                ),
                details={"validation_error_count": exc.error_count()},
            ) from exc
        return ResolvedEnvironmentProviderSpec(spec=spec, configuration=configuration, factory=factory)

    def create_manager(
        self,
        spec: EnvironmentProviderSpec,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentManager:
        resolved = self.resolve_spec(spec)
        try:
            manager = resolved.factory.create_manager(resolved.configuration, runtime=runtime)
        except EnvironmentProviderError:
            raise
        except Exception as exc:
            raise EnvironmentProviderError(
                f"Provider factory {spec.provider_key!r} failed to construct a Manager.",
                code="provider_factory_failed",
                category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=EnvironmentProviderErrorContext(
                    provider_key=spec.provider_key,
                    schema_version=spec.schema_version,
                ),
            ) from exc
        if not isinstance(manager, EnvironmentManager):
            raise _factory_target_error(
                spec.provider_key,
                "Provider factory returned a value that is not an EnvironmentManager.",
            )
        return manager


def discover_environment_provider_factory_references() -> tuple[EnvironmentProviderFactoryReference, ...]:
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


def build_environment_provider_factory_catalog(
    *,
    builtin_keys: Iterable[str] = (),
    extension_keys: Iterable[str] = (),
    explicit_factories: Iterable[EnvironmentProviderFactory] = (),
) -> EnvironmentProviderFactoryCatalog:
    builtins = tuple(_validate_provider_key(key) for key in builtin_keys)
    extensions = tuple(_validate_provider_key(key) for key in extension_keys)
    _require_unique(builtins)
    _require_unique(extensions)

    explicit = tuple(explicit_factories)
    explicit_keys: list[str] = []
    for factory in explicit:
        if not isinstance(factory, EnvironmentProviderFactory):
            raise _factory_target_error(None, "Explicit factories must be EnvironmentProviderFactory instances.")
        explicit_keys.append(_factory_key(factory))
    _require_unique(tuple(explicit_keys))

    collisions = (
        (set(builtins) & set(extensions))
        | (set(builtins) & set(explicit_keys))
        | (set(extensions) & set(explicit_keys))
    )
    if collisions:
        _raise_duplicate(
            "Environment provider factory selections collide.",
            provider_key=sorted(collisions)[0],
        )
    reserved_extensions = set(extensions) & _RESERVED_BUILTIN_KEYS
    if reserved_extensions:
        _raise_duplicate(
            "An extension cannot shadow a built-in Environment provider key.",
            provider_key=sorted(reserved_extensions)[0],
        )
    reserved_explicit = set(explicit_keys) & _RESERVED_BUILTIN_KEYS
    if reserved_explicit:
        _raise_duplicate(
            "An explicit factory cannot shadow a built-in Environment provider key.",
            provider_key=sorted(reserved_explicit)[0],
        )

    entries: list[tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]] = []
    available_builtins = _builtin_factory_types()
    for key in builtins:
        factory_type = available_builtins.get(key)
        if factory_type is None:
            _raise_missing(key, "The selected built-in Environment provider is not implemented.")
        factory = factory_type()
        entries.append((_registration(key, factory), factory))

    if extensions:
        selected = set(extensions)
        discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
        for entry_point in _entry_points():
            if entry_point.name in selected:
                discovered.setdefault(entry_point.name, []).append(entry_point)
        for key in extensions:
            matches = discovered.get(key, [])
            if not matches:
                _raise_missing(key, "A selected Environment provider extension was not found.")
            if len(matches) != 1:
                _raise_duplicate(
                    "An Environment provider extension key is supplied more than once.",
                    provider_key=key,
                )
        for key in extensions:
            entries.append(_load_entry_point(key, discovered[key][0]))

    entries.extend((_registration(key, factory), factory) for key, factory in zip(explicit_keys, explicit, strict=True))
    return EnvironmentProviderFactoryCatalog(entries)


def _builtin_factory_types() -> dict[str, type[EnvironmentProviderFactory]]:
    from .direct_local.manager import DirectLocalEnvironmentProviderFactory

    return {DirectLocalEnvironmentProviderFactory.provider_key(): DirectLocalEnvironmentProviderFactory}


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    return tuple(importlib.metadata.entry_points(group=ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP))


def _entry_point_reference(entry_point: importlib.metadata.EntryPoint) -> EnvironmentProviderFactoryReference:
    key = _validate_provider_key(entry_point.name)
    target = entry_point.value
    if not isinstance(target, str) or not target or len(target) > 500:
        raise _factory_target_error(key, "Environment provider entry-point target is invalid.")
    distribution = entry_point.dist
    name = None
    version = None
    if distribution is not None:
        raw_name = distribution.metadata.get("Name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name[:200]
        raw_version = distribution.version
        if isinstance(raw_version, str) and raw_version:
            version = raw_version[:100]
    return EnvironmentProviderFactoryReference(
        provider_key=key,
        import_target=target,
        distribution_name=name,
        distribution_version=version,
    )


def _load_entry_point(
    provider_key: str,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]:
    reference = _entry_point_reference(entry_point)
    context = EnvironmentProviderErrorContext(
        provider_key=provider_key,
        distribution_name=reference.distribution_name,
        distribution_version=reference.distribution_version,
    )
    try:
        loaded = entry_point.load()
    except Exception as exc:
        raise EnvironmentProviderError(
            f"Environment provider extension {provider_key!r} could not be loaded.",
            code="provider_factory_load_failed",
            category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            context=context,
        ) from exc
    if not isinstance(loaded, type) or not issubclass(loaded, EnvironmentProviderFactory):
        raise _factory_target_error(provider_key, "Entry point must load an EnvironmentProviderFactory class.", context)
    factory_type = cast(type[EnvironmentProviderFactory], loaded)
    try:
        factory = factory_type()
    except Exception as exc:
        raise EnvironmentProviderError(
            f"Environment provider extension {provider_key!r} requires a safe no-argument constructor.",
            code="provider_factory_load_failed",
            category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            context=context,
        ) from exc
    if _factory_key(factory) != provider_key:
        raise _factory_target_error(provider_key, "Entry-point name and factory provider key do not match.", context)
    return (
        EnvironmentProviderFactoryRegistration(
            provider_key=provider_key,
            class_module=factory_type.__module__,
            class_qualname=factory_type.__qualname__,
            import_target=reference.import_target,
            distribution_name=reference.distribution_name,
            distribution_version=reference.distribution_version,
        ),
        factory,
    )


def _registration(
    provider_key: str,
    factory: EnvironmentProviderFactory,
) -> EnvironmentProviderFactoryRegistration:
    factory_type = type(factory)
    return EnvironmentProviderFactoryRegistration(
        provider_key=provider_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=None,
        distribution_name=None,
        distribution_version=None,
    )


def _factory_key(factory: EnvironmentProviderFactory) -> str:
    try:
        return _validate_provider_key(factory.provider_key())
    except EnvironmentProviderError:
        raise
    except Exception as exc:
        raise _factory_target_error(None, "Environment provider factory key could not be read.") from exc


def _validate_provider_key(value: object) -> str:
    if not isinstance(value, str):
        raise EnvironmentProviderError(
            "Environment provider keys must be bounded namespaced identifiers.",
            code="provider_spec_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        )
    try:
        return EnvironmentProviderSpec(provider_key=value, schema_version="1").provider_key
    except ValidationError as exc:
        raise EnvironmentProviderError(
            "Environment provider keys must be bounded namespaced identifiers.",
            code="provider_spec_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        ) from exc


def _require_unique(keys: tuple[str, ...]) -> None:
    if len(keys) != len(set(keys)):
        _raise_duplicate("An Environment provider factory key was selected more than once.")


def _raise_missing(provider_key: str, description: str) -> NoReturn:
    raise EnvironmentProviderError(
        description,
        code="provider_factory_missing",
        category=EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=provider_key),
    )


def _raise_duplicate(description: str, *, provider_key: str | None = None) -> NoReturn:
    raise EnvironmentProviderError(
        description,
        code="provider_factory_duplicate",
        category=EnvironmentProviderErrorCategory.CONFLICT,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=provider_key),
    )


def _factory_target_error(
    provider_key: str | None,
    description: str,
    context: EnvironmentProviderErrorContext | None = None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_factory_target_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=context or EnvironmentProviderErrorContext(provider_key=provider_key),
    )


def _factory_failure(provider_key: str, description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_factory_failed",
        category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        context=EnvironmentProviderErrorContext(provider_key=provider_key),
    )
