"""Discover trusted Model Providers and isolate invalid registrations."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points, version
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, JsonValue, ValidationError

from a13n_harness_ui.connectors.forms import validate_form
from a13n_harness_ui.model_accounts.models import Provider
from a13n_harness_ui.model_adapters import _SUPPORTED_PROVIDERS

from .sdk import ModelProviderDeclaration, ModelProviderPlugin

if TYPE_CHECKING:
    from a13n_harness_ui.composition.models import DependencyProvenance

ENTRY_POINT_GROUP = "a13n_harness_ui.model_providers"
_RESERVED_KEYS = _SUPPORTED_PROVIDERS | frozenset(item.value for item in Provider)


def _validate_configuration_schema(schema: dict[str, Any]) -> None:
    validate_form(schema)

    def check(node: dict[str, Any]) -> None:
        if node.get("writeOnly") or node.get("format") in ("password", "secret"):
            raise ValueError("Secret fields belong to the OAuth account, not model configuration")
        if node.get("type") == "object" and node.get("additionalProperties") not in (None, False):
            raise ValueError("Open objects are not supported in model configuration")
        for child in node.get("properties", {}).values():
            check(child)
        for child in node.get("anyOf", ()):
            check(child)
        if isinstance(node.get("items"), dict):
            check(node["items"])

    if schema.get("type") != "object":
        raise ValueError("Model configuration must be an object")
    check(schema)
    for node in schema.get("$defs", {}).values():
        check(node)


@dataclass(frozen=True, slots=True)
class Registration:
    plugin: ModelProviderPlugin
    declaration: ModelProviderDeclaration
    implementation: str
    distribution_name: str
    distribution_version: str
    source: Literal["entry_point", "embedded"]
    configuration_schema: dict[str, Any] | None

    def provenance(self) -> DependencyProvenance:
        from a13n_harness_ui.composition.models import DependencyProvenance

        implementation = type(self.plugin)
        return DependencyProvenance(
            kind="model_provider",
            key=self.declaration.provider_key,
            source="installed" if self.source == "entry_point" else "host",
            class_module=implementation.__module__,
            class_qualname=implementation.__qualname__,
            import_target=self.implementation,
            distribution_name=self.distribution_name,
            distribution_version=self.distribution_version,
        )

    def configuration(self, values: dict[str, JsonValue]) -> BaseModel | None:
        validator = self.plugin.configuration_model
        if validator is None:
            if values:
                raise ValueError("This Model Provider accepts no model configuration")
            return None
        try:
            return validator.model_validate(values, extra="forbid")
        except ValidationError as exc:
            locations = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors(include_input=False))
            raise ValueError(f"Invalid Model Provider configuration: {locations or 'invalid value'}") from None


class ModelProviderCatalog:
    def __init__(
        self, plugins: tuple[ModelProviderPlugin, ...] = (), *, installed: tuple[EntryPoint, ...] | None = None
    ) -> None:
        self.registrations: dict[str, Registration] = {}
        self.diagnostics: list[str] = []
        conflicts: set[str] = set()

        def register(plugin: object, label: str, entry: EntryPoint | None = None) -> None:
            try:
                if not isinstance(plugin, ModelProviderPlugin):
                    raise TypeError("Expected a ModelProviderPlugin subclass")
                declaration = plugin.declaration()
                if not isinstance(declaration, ModelProviderDeclaration):
                    raise TypeError("Expected a ModelProviderDeclaration")
                declaration = ModelProviderDeclaration.model_validate(declaration.model_dump())
                key = declaration.provider_key
                if entry is not None and key != entry.name:
                    raise ValueError("The provider key must match the entry-point name")
                if key in _RESERVED_KEYS:
                    raise ValueError("This provider key is reserved for a built-in provider")
                requested = []
                if "device" in declaration.login_methods:
                    requested.extend(("start_device_login", "poll_device_login"))
                if declaration.loopback_callback is not None:
                    requested.extend(("begin_callback_login", "complete_callback_login"))
                if declaration.supports_model_discovery:
                    requested.append("discover_models")
                if declaration.supports_usage:
                    requested.append("fetch_usage")
                for name in requested:
                    if getattr(type(plugin), name) is getattr(ModelProviderPlugin, name):
                        raise ValueError(f"Declared capability requires {name}")
                validator = plugin.configuration_model
                schema = None
                if validator is not None:
                    if not isinstance(validator, type) or not issubclass(validator, BaseModel):
                        raise TypeError("Expected a Pydantic model for configuration_model")
                    schema = validator.model_json_schema()
                    _validate_configuration_schema(schema)
                dist = getattr(entry, "dist", None) if entry is not None else None
                if key in self.registrations or key in conflicts:
                    self.registrations.pop(key, None)
                    conflicts.add(key)
                    self.diagnostics.append(f"{key}: duplicate Model Provider key; remove conflicting registrations.")
                    return
                self.registrations[key] = Registration(
                    plugin,
                    declaration,
                    entry.value if entry is not None else label,
                    dist.name if dist is not None else "a13n-harness-ui",
                    dist.version if dist is not None else version("a13n-harness-ui"),
                    "entry_point" if entry is not None else "embedded",
                    schema,
                )
            except ValidationError as exc:
                fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors(include_input=False))
                self.diagnostics.append(f"{label}: invalid Model Provider declaration ({fields or 'invalid fields'}).")
            except Exception as exc:
                # Plugin exceptions can contain arbitrary provider data; never publish their text.
                self.diagnostics.append(f"{label}: invalid Model Provider registration ({type(exc).__name__}).")

        for plugin in plugins:
            register(plugin, f"{type(plugin).__module__}:{type(plugin).__qualname__}")
        for entry in installed if installed is not None else entry_points(group=ENTRY_POINT_GROUP):
            try:
                factory = entry.load()
                if not isinstance(factory, type) or not issubclass(factory, ModelProviderPlugin):
                    raise TypeError("Model Provider entry points must load a plugin subclass")
                plugin = factory()
            except Exception as exc:
                self.diagnostics.append(f"{entry.name}: could not load Model Provider ({type(exc).__name__}).")
            else:
                register(plugin, entry.name, entry)

    def get(self, provider_key: str) -> Registration | None:
        return self.registrations.get(provider_key)
