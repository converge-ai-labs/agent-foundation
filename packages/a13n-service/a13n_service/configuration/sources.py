"""Resolve one explicit TOML file and environment overrides into typed settings."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .sections import Section

if TYPE_CHECKING:
    from a13n_service.settings import Settings

# Retain the established deployment variable names while grouping authored config.
_ENV_PREFIXES = {
    "service": "",
    "plugins": "PLUGIN_",
    "provider_plugins": "PROVIDER_PLUGIN_",
    "subagents": "SUBAGENT_",
    "environments": "ENVIRONMENT_",
    "models": "MODEL_",
    "webhooks": "WEBHOOK_",
    "assets": "ASSET_",
    "hooks": "HOOK_",
    "objects": "OBJECT_",
    "runs": "RUN_",
    "secrets": "SECRET_",
    "logging": "LOG_",
}
_ENV_NAMES = {
    "service.name": "SERVICE_NAME",
    "service.instance_id": "SERVICE_INSTANCE_ID",
    "migration.auto_migrate": "AUTO_MIGRATE",
}


class ConfigurationError(ValueError):
    """A safe configuration diagnostic that never includes supplied values."""


def configuration_fields(
    model: type[Section], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[str, tuple[str, ...], Any]]:
    """Enumerate the schema once for environment parsing and configuration docs."""
    for name, field in model.model_fields.items():
        path = (*prefix, name)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, Section):
            yield from configuration_fields(annotation, path)
            continue
        section, *parts = path
        suffix = "_".join(parts).upper()
        env_name = _ENV_NAMES.get(".".join(path))
        if env_name is None:
            env_name = (
                suffix
                if section == "gateway" and suffix.startswith("A2A_")
                else (_ENV_PREFIXES.get(section, section.upper() + "_") + suffix)
            )
        yield "A13N_SERVICE_" + env_name, path, field


def _merge(target: dict, source: Mapping) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def load_settings(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping | None = None,
) -> Settings:
    """Defaults < explicit TOML < environment < explicit executable overrides.

    Relative storage paths (including defaults and overrides) use the selected
    file's directory, or the invocation directory when no file is selected.
    """
    from a13n_service.settings import Settings

    values: dict = {}
    base = Path.cwd()
    if path is not None:
        path = path.resolve()
        base = path.parent
        try:
            with path.open("rb") as source:
                values = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            raise ConfigurationError("Cannot read configuration: expected a readable, valid TOML file") from None
        # Check authored keys even if a higher-priority source would replace them.
        _check_keys(Settings, values)
    env = {key.upper(): value for key, value in (os.environ if environ is None else environ).items()}
    for name, field_path, field in configuration_fields(Settings):
        if name not in env:
            continue
        value: Any = env[name]
        if field.annotation is not None and getattr(field.annotation, "__origin__", None) is tuple:
            try:
                value = json.loads(value)
            except ValueError:
                raise ConfigurationError(f"Invalid JSON array in {name}") from None
        current = values
        for part in field_path[:-1]:
            if not isinstance(current.get(part), dict):
                current[part] = {}
            current = current[part]
        current[field_path[-1]] = value
    if overrides:
        _merge(values, overrides)
    try:
        settings = Settings.model_validate(values)
        paths = {"objects": "local_root", "filesystem": "root"}
        for section, field_name in paths.items():
            group = getattr(settings, section)
            value = getattr(group, field_name)
            if not value.is_absolute():
                values.setdefault(section, {})[field_name] = (base / value).resolve()
        settings = Settings.model_validate(values)
        settings.storage_settings()
        settings.identity_configuration()
        return settings
    except ValidationError as error:
        locations = ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors(include_input=False))
        raise ConfigurationError(f"Invalid configuration fields: {locations}") from None
    except ValueError:
        raise ConfigurationError("Invalid configuration: check storage locations and identity settings") from None


def _check_keys(model: type[Section], values: Mapping, prefix: str = "") -> None:
    for name, value in values.items():
        location = prefix + name
        field = model.model_fields.get(name)
        if field is None:
            raise ConfigurationError(f"Unknown configuration field: {location}")
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, Section):
            if not isinstance(value, dict):
                raise ConfigurationError(f"Expected configuration section: {location}")
            _check_keys(annotation, value, location + ".")
