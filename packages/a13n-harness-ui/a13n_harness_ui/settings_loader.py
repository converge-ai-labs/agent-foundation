"""Bootstrap data-root selection and full Harness UI configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from a13n_harness_ui.configuration import (
    LoadedHarnessUiConfiguration,
    empty_harness_ui_configuration,
    load_harness_ui_configuration,
)
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings, default_harness_ui_root

_DEFAULT_SETTINGS_NAME = "a13n-harness-ui.yaml"
_DATA_ROOT_ENV = "A13N_HARNESS_UI_DATA_ROOT"


@dataclass(frozen=True, slots=True)
class HarnessUiSettingsSource:
    """Selected source tree plus bootstrap-resolved process settings."""

    configuration: LoadedHarnessUiConfiguration | None
    settings: HarnessUiSettings
    path: Path
    explicit: bool
    exists: bool
    candidate_error: ConfigurationError | None = None


def default_harness_ui_settings_path() -> Path:
    return default_harness_ui_root() / _DEFAULT_SETTINGS_NAME


def default_harness_ui_settings() -> HarnessUiSettings:
    path = default_harness_ui_settings_path()
    return HarnessUiSettings(storage=StorageSettings(data_root=path.parent / "data"))


def resolve_harness_ui_data_root(path: Path | None = None, *, data_root: Path | None = None) -> Path:
    """Resolve the bootstrap locator without scanning configuration or plugins."""
    selected = (path or default_harness_ui_settings_path()).expanduser().resolve(strict=False)
    configured_data_root = data_root
    if configured_data_root is None:
        environment_value = os.environ.get(_DATA_ROOT_ENV)
        configured_data_root = Path(environment_value) if environment_value else selected.parent / "data"
    resolved_data_root = configured_data_root.expanduser()
    if not resolved_data_root.is_absolute():
        resolved_data_root = Path.cwd() / resolved_data_root
    resolved_data_root = resolved_data_root.resolve(strict=False)

    return resolved_data_root


async def load_harness_ui_settings(
    path: Path | None = None,
    *,
    data_root: Path | None = None,
) -> HarnessUiSettingsSource:
    """Select the config path, bootstrap data root, and load one complete generation."""

    explicit = path is not None
    selected = (path or default_harness_ui_settings_path()).expanduser().resolve(strict=False)
    if selected.suffix != ".yaml":
        raise ConfigurationError(
            "Harness UI configuration must use lower-case .yaml.",
            code="settings_path_invalid",
            details={"path": str(selected)},
        )
    resolved_data_root = resolve_harness_ui_data_root(selected, data_root=data_root)

    candidate_error: ConfigurationError | None = None
    if not selected.exists():
        if explicit:
            candidate_error = ConfigurationError(
                "The selected Harness UI configuration file does not exist.",
                code="settings_unavailable",
                details={"path": str(selected)},
            )
        configuration = None
        exists = False
    else:
        try:
            configuration = await load_harness_ui_configuration(
                selected,
                content_plugin_root=resolved_data_root / "content-plugins",
            )
        except ConfigurationError as exc:
            configuration = None
            candidate_error = exc
        exists = True

    process = (
        empty_harness_ui_configuration().document.process if configuration is None else configuration.document.process
    )
    settings = HarnessUiSettings(
        storage=StorageSettings(data_root=resolved_data_root, max_object_bytes=process.max_object_bytes),
        log_level=process.log_level,
        log_format=process.log_format,
        pricing_auto_update=process.pricing_auto_update,
    )
    return HarnessUiSettingsSource(
        configuration=configuration,
        settings=settings,
        path=selected,
        explicit=explicit,
        exists=exists,
        candidate_error=candidate_error,
    )


def ensure_default_directories(source: HarnessUiSettingsSource) -> None:
    """Create only the bootstrap-selected local data root."""

    source.settings.storage.data_root.mkdir(parents=True, exist_ok=True)


__all__ = [
    "HarnessUiSettingsSource",
    "default_harness_ui_settings",
    "default_harness_ui_settings_path",
    "ensure_default_directories",
    "load_harness_ui_settings",
]
