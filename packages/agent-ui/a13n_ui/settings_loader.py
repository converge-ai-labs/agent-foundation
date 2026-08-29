"""Strict YAML bootstrap for one Agent UI Host lifetime."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import yaml
from anyio import to_thread
from pydantic import ValidationError

from a13n_ui.configuration import ConfigurationSettings, DefinitionRootSettings
from a13n_ui.errors import ConfigurationError
from a13n_ui.settings import AgentUiSettings, StorageSettings

_DEFAULT_DIRECTORY = ".a13n-ui"
_DEFAULT_SETTINGS_NAME = "settings.yaml"
_MAX_SETTINGS_BYTES = 1024 * 1024
_MAX_SETTINGS_NODES = 100_000
_MAX_SETTINGS_DEPTH = 64
_DEFINITION_NAMESPACES = (
    "models",
    "prompts",
    "plugins",
    "skill-sources",
    "skills",
    "agents",
    "environments",
)


@dataclass(frozen=True, slots=True)
class AgentUiSettingsSource:
    """One resolved bootstrap source and its validated settings."""

    settings: AgentUiSettings
    path: Path
    explicit: bool
    exists: bool


def default_agent_ui_root() -> Path:
    return (Path.home() / _DEFAULT_DIRECTORY).resolve(strict=False)


def default_agent_ui_settings_path() -> Path:
    return default_agent_ui_root() / _DEFAULT_SETTINGS_NAME


def default_agent_ui_settings() -> AgentUiSettings:
    root = default_agent_ui_root()
    return AgentUiSettings(
        storage=StorageSettings(data_root=root / "data"),
        configuration=ConfigurationSettings(
            definition_roots=(
                DefinitionRootSettings(
                    root_id="root-user",
                    path=root / "definitions",
                    writable=True,
                ),
            )
        ),
    )


async def load_agent_ui_settings(path: Path | None = None) -> AgentUiSettingsSource:
    """Select one YAML settings source without profiles, merging, or cwd discovery."""

    explicit = path is not None
    selected = (path or default_agent_ui_settings_path()).expanduser().resolve(strict=False)
    if selected.suffix.lower() not in {".yaml", ".yml"}:
        raise _error("settings_path_invalid", "Agent UI settings must use YAML.")
    if not selected.exists():
        if explicit:
            raise _error("settings_unavailable", "The selected Agent UI settings file does not exist.")
        return AgentUiSettingsSource(
            settings=default_agent_ui_settings(),
            path=selected,
            explicit=False,
            exists=False,
        )
    content = await _stable_read(selected)
    raw = _parse_yaml(content)
    try:
        serialized = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        settings = AgentUiSettings.model_validate_json(serialized, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        count = exc.error_count() if isinstance(exc, ValidationError) else 1
        raise _error(
            "settings_invalid",
            "The Agent UI settings document is invalid.",
            details={"validation_error_count": count},
        ) from exc
    return AgentUiSettingsSource(settings=settings, path=selected, explicit=explicit, exists=True)


async def _stable_read(path: Path) -> bytes:
    for _attempt in range(3):
        try:
            before = await to_thread.run_sync(partial(path.stat, follow_symlinks=False))
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_SETTINGS_BYTES:
                raise _error("settings_source_limit", "The Agent UI settings file exceeds its size limit.")
            content = await to_thread.run_sync(path.read_bytes)
            after = await to_thread.run_sync(partial(path.stat, follow_symlinks=False))
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("settings_unavailable", "The Agent UI settings file cannot be read.") from exc
        fingerprint_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        fingerprint_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if fingerprint_before == fingerprint_after and len(content) == before.st_size:
            return content
    raise _error("settings_source_unstable", "The Agent UI settings file changed during bounded reads.")


def ensure_default_directories(source: AgentUiSettingsSource) -> None:
    """Create only the selected local data and writable definition roots."""

    source.settings.storage.data_root.mkdir(parents=True, exist_ok=True)
    for root in source.settings.configuration.ordered_roots:
        if root.writable:
            root.path.mkdir(parents=True, exist_ok=True)
            for namespace in _DEFINITION_NAMESPACES:
                (root.path / namespace).mkdir(exist_ok=True)


def _parse_yaml(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("settings_invalid", "The Agent UI settings file is not valid UTF-8.") from exc
    if "\x00" in text:
        raise _error("settings_invalid", "The Agent UI settings file contains NUL.")
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise _error("settings_invalid", "YAML anchors and aliases are forbidden.")
            tag = getattr(event, "tag", None)
            if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
                raise _error("settings_invalid", "Custom YAML tags are forbidden.")
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                nodes += 1
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.events.ScalarEvent):
                nodes += 1
            if nodes > _MAX_SETTINGS_NODES or depth > _MAX_SETTINGS_DEPTH:
                raise _error("settings_source_limit", "The Agent UI settings file exceeds structural limits.")
        value = yaml.load(text, Loader=_UniqueSafeLoader)
    except ConfigurationError:
        raise
    except RecursionError as exc:
        raise _error("settings_source_limit", "The Agent UI settings file exceeds structural limits.") from exc
    except (yaml.YAMLError, UnicodeError, ValueError) as exc:
        raise _error("settings_invalid", "The Agent UI settings file has invalid syntax.") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _error("settings_invalid", "Agent UI settings must be a string-keyed mapping.")
    nodes, depth = _measure_tree(value)
    if nodes > _MAX_SETTINGS_NODES or depth > _MAX_SETTINGS_DEPTH:
        raise _error("settings_source_limit", "The Agent UI settings file exceeds structural limits.")
    return value


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    pairs = loader.construct_pairs(node, deep=deep)
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(None, None, "mapping keys must be strings", node.start_mark)
        if key in result:
            raise yaml.constructor.ConstructorError(None, None, "duplicate mapping key", node.start_mark)
        result[key] = value
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _measure_tree(value: Any, depth: int = 1) -> tuple[int, int]:
    if isinstance(value, dict):
        children = [_measure_tree(item, depth + 1) for item in value.values()]
    elif isinstance(value, list):
        children = [_measure_tree(item, depth + 1) for item in value]
    else:
        return 1, depth
    return 1 + sum(item[0] for item in children), max((item[1] for item in children), default=depth)


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> ConfigurationError:
    return ConfigurationError(message, code=code, details=details)


__all__ = [
    "AgentUiSettingsSource",
    "default_agent_ui_root",
    "default_agent_ui_settings",
    "default_agent_ui_settings_path",
    "ensure_default_directories",
    "load_agent_ui_settings",
]
