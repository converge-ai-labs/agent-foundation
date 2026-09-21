"""Bounded opt-in configuration for Harness plugin construction."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import JsonValue
from yaml import YAMLError
from yaml import load as yaml_load
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent, CollectionStartEvent, ScalarEvent
from yaml.loader import SafeLoader
from yaml.nodes import MappingNode

from a13n_harness._json import dump_json_bytes
from a13n_harness.errors import PluginError

HARNESS_PLUGIN_CONFIG_ENABLED_ENV = "A13N_HARNESS_PLUGIN_CONFIG_ENABLED"
HARNESS_PLUGIN_CONFIG_JSON_ENV = "A13N_HARNESS_PLUGIN_CONFIG_JSON"
HARNESS_PLUGIN_CONFIG_FILE_ENV = "A13N_HARNESS_PLUGIN_CONFIG_FILE"
DEFAULT_HARNESS_PLUGIN_CONFIG_FILE = "harness-plugins.yaml"

_MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES = 1024 * 1024
_MAX_HARNESS_PLUGIN_COUNT = 128
_MAX_HARNESS_PLUGIN_IDENTIFIER_LENGTH = 200
_MAX_JSON_DEPTH = 64
_MAX_YAML_NODE_COUNT = 10_000
_MAX_YAML_SCALAR_CHARACTERS = 256 * 1024
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_EMPTY_JSON_OBJECT: Mapping[str, JsonValue] = MappingProxyType({})


class _YamlLimitError(YAMLError):
    """The restricted YAML source exceeded a pre-construction resource limit."""


class _StrictYamlLoader(SafeLoader):
    """Safe YAML loader without aliases, anchors, explicit tags, or resource expansion."""

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._composition_depth = 0
        self._node_count = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        self._node_count += 1
        if self._node_count > _MAX_YAML_NODE_COUNT:
            raise _YamlLimitError("YAML contains too many nodes")
        next_depth = self._composition_depth + 1
        if next_depth > _MAX_JSON_DEPTH + 1:
            raise _YamlLimitError("YAML nesting is too deep")
        if isinstance(event, AliasEvent):
            raise ConstructorError(None, None, "YAML aliases are not supported", event.start_mark)
        if isinstance(event, ScalarEvent) and len(event.value) > _MAX_YAML_SCALAR_CHARACTERS:
            raise _YamlLimitError("YAML scalar is too large")
        if isinstance(event, ScalarEvent | CollectionStartEvent):
            if event.anchor is not None:
                raise ConstructorError(None, None, "YAML anchors are not supported", event.start_mark)
            if event.tag is not None:
                raise ConstructorError(None, None, "Explicit YAML tags are not supported", event.start_mark)
        self._composition_depth = next_depth
        try:
            return super().compose_node(parent, index)
        finally:
            self._composition_depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Hashable, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None, "Expected a YAML mapping", node.start_mark)
        value: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConstructorError(None, None, "YAML mapping keys must be strings", key_node.start_mark)
            if key == "<<":
                raise ConstructorError(None, None, "YAML merge keys are not supported", key_node.start_mark)
            if key in value:
                raise ConstructorError(None, None, "Duplicate YAML mapping key", key_node.start_mark)
            value[key] = self.construct_object(value_node, deep=deep)
        return value


@dataclass(frozen=True, slots=True)
class HarnessPluginConfigurationEntry:
    """One ordered plugin instance request in the Harness configuration document."""

    plugin_id: str
    plugin_key: str
    enabled: bool
    configuration: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", _validate_identifier(self.plugin_id, field_name="plugin_id"))
        object.__setattr__(self, "plugin_key", _validate_identifier(self.plugin_key, field_name="plugin_key"))
        if not isinstance(self.enabled, bool):
            raise PluginError(
                "Harness plugin enabled values must be booleans.",
                code="plugin_configuration_invalid",
            )
        object.__setattr__(
            self,
            "configuration",
            _detach_json_object(self.configuration, field_name="configuration"),
        )


@dataclass(frozen=True, slots=True)
class HarnessPluginConfiguration:
    """Validated version-one Harness plugin configuration document."""

    schema_version: Literal["1"]
    plugins: tuple[HarnessPluginConfigurationEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise PluginError(
                "Harness plugin configuration schema version is unsupported.",
                code="plugin_configuration_version_unsupported",
            )
        supplied_plugins = tuple(self.plugins)
        if len(supplied_plugins) > _MAX_HARNESS_PLUGIN_COUNT:
            raise PluginError(
                "Harness plugin configuration contains too many entries.",
                code="plugin_configuration_too_large",
            )
        if not all(isinstance(entry, HarnessPluginConfigurationEntry) for entry in supplied_plugins):
            raise PluginError(
                "Harness plugin configuration entries are invalid.",
                code="plugin_configuration_invalid",
            )
        plugins = tuple(
            HarnessPluginConfigurationEntry(
                plugin_id=entry.plugin_id,
                plugin_key=entry.plugin_key,
                enabled=entry.enabled,
                configuration=entry.configuration,
            )
            for entry in supplied_plugins
        )
        seen_ids: set[str] = set()
        for entry in plugins:
            if entry.plugin_id in seen_ids:
                raise PluginError(
                    "Harness plugin configuration IDs must be unique.",
                    code="plugin_configuration_plugin_id_duplicate",
                    details={"plugin_id": entry.plugin_id},
                )
            seen_ids.add(entry.plugin_id)
        _require_bounded_json(_configuration_json(self.schema_version, plugins))
        object.__setattr__(self, "plugins", plugins)

    @property
    def enabled_plugins(self) -> tuple[HarnessPluginConfigurationEntry, ...]:
        """Return enabled entries in document order."""

        return tuple(entry for entry in self.plugins if entry.enabled)


@dataclass(frozen=True, slots=True)
class HarnessBuildContext:
    """Builder-local configured-plugin state and bounded Host extensions."""

    configured_plugins_enabled: bool = False
    plugin_configuration: HarnessPluginConfiguration | None = None
    extensions: Mapping[str, JsonValue] = field(default_factory=lambda: _EMPTY_JSON_OBJECT)

    def __post_init__(self) -> None:
        if not isinstance(self.configured_plugins_enabled, bool):
            raise PluginError(
                "Configured-plugin enablement must be a boolean.",
                code="plugin_configuration_invalid",
            )
        if self.configured_plugins_enabled:
            if not isinstance(self.plugin_configuration, HarnessPluginConfiguration):
                raise PluginError(
                    "Enabled configured plugins require a configuration document.",
                    code="plugin_configuration_invalid",
                )
            object.__setattr__(
                self,
                "plugin_configuration",
                HarnessPluginConfiguration(
                    schema_version=self.plugin_configuration.schema_version,
                    plugins=self.plugin_configuration.plugins,
                ),
            )
        elif self.plugin_configuration is not None:
            raise PluginError(
                "Disabled configured plugins cannot retain a configuration document.",
                code="plugin_configuration_invalid",
            )
        object.__setattr__(self, "extensions", _detach_extension_object(self.extensions))

    @classmethod
    def from_configuration(
        cls,
        configuration: Mapping[str, JsonValue],
        *,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> HarnessBuildContext:
        """Build an enabled context from one explicit programmatic data document."""

        parsed = _parse_configuration_mapping(configuration)
        return cls(
            configured_plugins_enabled=True,
            plugin_configuration=parsed,
            extensions=extensions or _EMPTY_JSON_OBJECT,
        )

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        *,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> HarnessBuildContext:
        """Build an enabled context from one bounded inline JSON value."""

        parsed = _parse_configuration_json(value)
        return cls(
            configured_plugins_enabled=True,
            plugin_configuration=parsed,
            extensions=extensions or _EMPTY_JSON_OBJECT,
        )

    @classmethod
    def from_yaml(
        cls,
        value: str | bytes,
        *,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> HarnessBuildContext:
        """Build an enabled context from one bounded strict YAML document."""

        parsed = _parse_configuration_yaml(value)
        return cls(
            configured_plugins_enabled=True,
            plugin_configuration=parsed,
            extensions=extensions or _EMPTY_JSON_OBJECT,
        )

    @classmethod
    def from_file(
        cls,
        path: str | PathLike[str],
        *,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> HarnessBuildContext:
        """Build an enabled context from one bounded JSON or strict YAML file."""

        source_path = _configuration_file_path(path)
        suffix = source_path.suffix.lower()
        if suffix not in {".json", ".yaml", ".yml"}:
            raise PluginError(
                "Harness plugin configuration file must use a .json, .yaml, or .yml suffix.",
                code="plugin_configuration_format_unsupported",
            )
        raw = _read_configuration_file(source_path)
        if suffix == ".json":
            return cls.from_json(raw, extensions=extensions)
        return cls.from_yaml(raw, extensions=extensions)

    @classmethod
    def from_environment(
        cls,
        *,
        enabled: bool | None = None,
        environ: Mapping[str, str] | None = None,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> HarnessBuildContext:
        """Resolve the disabled-by-default environment and file source contract."""

        if enabled is not None and not isinstance(enabled, bool):
            raise PluginError(
                "The configured-plugin override must be a boolean or None.",
                code="plugin_configuration_enablement_invalid",
            )
        source = os.environ if environ is None else environ
        resolved_enabled = _parse_enabled(source.get(HARNESS_PLUGIN_CONFIG_ENABLED_ENV)) if enabled is None else enabled
        if not resolved_enabled:
            return cls(extensions=extensions or _EMPTY_JSON_OBJECT)

        if HARNESS_PLUGIN_CONFIG_JSON_ENV in source:
            return cls.from_json(source[HARNESS_PLUGIN_CONFIG_JSON_ENV], extensions=extensions)
        if HARNESS_PLUGIN_CONFIG_FILE_ENV in source:
            return cls.from_file(source[HARNESS_PLUGIN_CONFIG_FILE_ENV], extensions=extensions)
        try:
            default_path = Path.cwd() / DEFAULT_HARNESS_PLUGIN_CONFIG_FILE
        except OSError:
            raise PluginError(
                "The default Harness plugin configuration location could not be resolved.",
                code="plugin_configuration_read_failed",
            ) from None
        return cls.from_file(default_path, extensions=extensions)


def _parse_enabled(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or value != value.strip():
        raise PluginError(
            "The configured-plugin environment value is invalid.",
            code="plugin_configuration_enablement_invalid",
        )
    normalized = value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise PluginError(
        "The configured-plugin environment value is invalid.",
        code="plugin_configuration_enablement_invalid",
    )


def _configuration_file_path(path: str | PathLike[str]) -> Path:
    try:
        return Path(path)
    except (TypeError, ValueError):
        raise PluginError(
            "Harness plugin configuration file path is invalid.",
            code="plugin_configuration_read_failed",
        ) from None


def _read_configuration_file(source_path: Path) -> bytes:
    try:
        with source_path.open("rb") as source:
            raw = source.read(_MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES + 1)
    except FileNotFoundError:
        raise PluginError(
            "Enabled Harness plugin configuration source was not found.",
            code="plugin_configuration_source_missing",
        ) from None
    except OSError:
        raise PluginError(
            "Harness plugin configuration source could not be read.",
            code="plugin_configuration_read_failed",
        ) from None
    if len(raw) > _MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES:
        raise PluginError(
            "Harness plugin configuration source is too large.",
            code="plugin_configuration_too_large",
        )
    return raw


def _parse_configuration_json(value: str | bytes) -> HarnessPluginConfiguration:
    raw = _strict_utf8_bytes(value)
    if len(raw) > _MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES:
        raise PluginError(
            "Harness plugin configuration source is too large.",
            code="plugin_configuration_too_large",
        )
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, UnicodeDecodeError, ValueError):
        raise PluginError(
            "Harness plugin configuration must be valid strict JSON.",
            code="plugin_configuration_invalid",
        ) from None
    if not isinstance(document, dict):
        raise PluginError(
            "Harness plugin configuration must be a JSON object.",
            code="plugin_configuration_invalid",
        )
    return _parse_configuration_mapping(cast(dict[str, JsonValue], document))


def _load_strict_yaml(value: str) -> Any:
    return yaml_load(value, Loader=_StrictYamlLoader)


def _parse_configuration_yaml(value: str | bytes) -> HarnessPluginConfiguration:
    raw = _strict_utf8_bytes(value)
    if len(raw) > _MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES:
        raise PluginError(
            "Harness plugin configuration source is too large.",
            code="plugin_configuration_too_large",
        )
    try:
        document = _load_strict_yaml(raw.decode("utf-8", errors="strict"))
    except _YamlLimitError:
        raise PluginError(
            "Harness plugin configuration source exceeds YAML resource limits.",
            code="plugin_configuration_too_large",
        ) from None
    except (RecursionError, UnicodeDecodeError, YAMLError, OverflowError, TypeError, ValueError):
        raise PluginError(
            "Harness plugin configuration must be valid restricted YAML.",
            code="plugin_configuration_invalid",
        ) from None
    if not isinstance(document, dict):
        raise PluginError(
            "Harness plugin configuration must be a YAML mapping.",
            code="plugin_configuration_invalid",
        )
    return _parse_configuration_mapping(cast(dict[str, JsonValue], document))


def _strict_utf8_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise PluginError(
                "Harness plugin configuration must use UTF-8.",
                code="plugin_configuration_invalid",
            ) from None
        return value
    if isinstance(value, str):
        try:
            return value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise PluginError(
                "Harness plugin configuration must use UTF-8.",
                code="plugin_configuration_invalid",
            ) from None
    raise PluginError(
        "Harness plugin configuration source must be text or bytes.",
        code="plugin_configuration_invalid",
    )


def _reject_duplicate_object_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON number")


def _parse_configuration_mapping(configuration: Mapping[str, JsonValue]) -> HarnessPluginConfiguration:
    document = _detach_json_object(configuration, field_name="configuration document")
    if set(document) != {"schema_version", "plugins"}:
        raise PluginError(
            "Harness plugin configuration fields are invalid.",
            code="plugin_configuration_invalid",
        )
    schema_version = document["schema_version"]
    if schema_version != "1":
        raise PluginError(
            "Harness plugin configuration schema version is unsupported.",
            code="plugin_configuration_version_unsupported",
        )
    raw_plugins = document["plugins"]
    if not isinstance(raw_plugins, list):
        raise PluginError(
            "Harness plugin configuration plugins must be an array.",
            code="plugin_configuration_invalid",
        )
    if len(raw_plugins) > _MAX_HARNESS_PLUGIN_COUNT:
        raise PluginError(
            "Harness plugin configuration contains too many entries.",
            code="plugin_configuration_too_large",
        )
    entries = tuple(_parse_configuration_entry(value) for value in raw_plugins)
    return HarnessPluginConfiguration(schema_version="1", plugins=entries)


def _parse_configuration_entry(value: JsonValue) -> HarnessPluginConfigurationEntry:
    if not isinstance(value, dict) or set(value) != {"plugin_id", "plugin_key", "enabled", "configuration"}:
        raise PluginError(
            "Harness plugin configuration entry fields are invalid.",
            code="plugin_configuration_invalid",
        )
    plugin_id = value["plugin_id"]
    plugin_key = value["plugin_key"]
    enabled = value["enabled"]
    configuration = value["configuration"]
    if not isinstance(enabled, bool):
        raise PluginError(
            "Harness plugin enabled values must be booleans.",
            code="plugin_configuration_invalid",
        )
    if not isinstance(configuration, dict):
        raise PluginError(
            "Harness plugin factory configuration must be a JSON object.",
            code="plugin_configuration_invalid",
        )
    return HarnessPluginConfigurationEntry(
        plugin_id=_validate_identifier(plugin_id, field_name="plugin_id"),
        plugin_key=_validate_identifier(plugin_key, field_name="plugin_key"),
        enabled=enabled,
        configuration=configuration,
    )


def _validate_identifier(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_HARNESS_PLUGIN_IDENTIFIER_LENGTH
    ):
        raise PluginError(
            f"Harness plugin {field_name} must be a bounded non-blank string without surrounding whitespace.",
            code="plugin_configuration_invalid",
        )
    return value


def _detach_extension_object(value: object) -> Mapping[str, JsonValue]:
    detached = _detach_json_object(value, field_name="extensions")
    for namespace in detached:
        _validate_identifier(namespace, field_name="extension namespace")
    return detached


def _detach_json_object(value: object, *, field_name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise PluginError(
            f"Harness plugin {field_name} must be a JSON object.",
            code="plugin_configuration_invalid",
        )
    try:
        detached = _clone_json(value, depth=0, active=set())
        if not isinstance(detached, dict):
            raise TypeError("root is not an object")
        _require_bounded_json(detached)
    except PluginError:
        raise
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
        raise PluginError(
            f"Harness plugin {field_name} must be bounded finite JSON.",
            code="plugin_configuration_invalid",
        ) from None
    return MappingProxyType(detached)


def _clone_json(value: object, *, depth: int, active: set[int]) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON nesting is too deep")
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    value_id = id(value)
    if value_id in active:
        raise ValueError("JSON values cannot contain cycles")
    active.add(value_id)
    try:
        if isinstance(value, list):
            return [_clone_json(item, depth=depth + 1, active=active) for item in value]
        if isinstance(value, Mapping):
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                result[key] = _clone_json(item, depth=depth + 1, active=active)
            return result
        raise TypeError("value is not JSON")
    finally:
        active.remove(value_id)


def _require_bounded_json(value: object) -> None:
    try:
        encoded = dump_json_bytes(value)
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError):
        raise PluginError(
            "Harness plugin configuration values must be finite JSON.",
            code="plugin_configuration_invalid",
        ) from None
    if len(encoded) > _MAX_HARNESS_PLUGIN_CONFIGURATION_BYTES:
        raise PluginError(
            "Harness plugin configuration value is too large.",
            code="plugin_configuration_too_large",
        )


def _configuration_json(
    schema_version: str,
    plugins: tuple[HarnessPluginConfigurationEntry, ...],
) -> dict[str, JsonValue]:
    return {
        "schema_version": schema_version,
        "plugins": [
            {
                "plugin_id": entry.plugin_id,
                "plugin_key": entry.plugin_key,
                "enabled": entry.enabled,
                "configuration": dict(entry.configuration),
            }
            for entry in plugins
        ],
    }
