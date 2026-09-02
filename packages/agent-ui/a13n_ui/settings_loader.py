"""Bootstrap data-root selection and full Agent UI configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from a13n_ui.configuration import (
    LoadedAgentUiConfiguration,
    empty_agent_ui_configuration,
    load_agent_ui_configuration,
)
from a13n_ui.errors import ConfigurationError
from a13n_ui.settings import AgentUiSettings, StorageSettings, default_agent_ui_root

_DEFAULT_SETTINGS_NAME = "a13n-ui.yaml"
_DATA_ROOT_ENV = "A13N_UI_DATA_ROOT"


@dataclass(frozen=True, slots=True)
class AgentUiSettingsSource:
    """Selected source tree plus bootstrap-resolved process settings."""

    configuration: LoadedAgentUiConfiguration | None
    settings: AgentUiSettings
    path: Path
    explicit: bool
    exists: bool
    candidate_error: ConfigurationError | None = None


def default_agent_ui_settings_path() -> Path:
    return default_agent_ui_root() / _DEFAULT_SETTINGS_NAME


def default_agent_ui_settings() -> AgentUiSettings:
    path = default_agent_ui_settings_path()
    return AgentUiSettings(storage=StorageSettings(data_root=path.parent / "data"))


async def load_agent_ui_settings(
    path: Path | None = None,
    *,
    data_root: Path | None = None,
) -> AgentUiSettingsSource:
    """Select the config path, bootstrap data root, and load one complete generation."""

    explicit = path is not None
    selected = (path or default_agent_ui_settings_path()).expanduser().resolve(strict=False)
    if selected.suffix != ".yaml":
        raise ConfigurationError(
            "Agent UI configuration must use lower-case .yaml.",
            code="settings_path_invalid",
            details={"path": str(selected)},
        )
    configured_data_root = data_root
    if configured_data_root is None:
        environment_value = os.environ.get(_DATA_ROOT_ENV)
        configured_data_root = Path(environment_value) if environment_value else selected.parent / "data"
    resolved_data_root = configured_data_root.expanduser()
    if not resolved_data_root.is_absolute():
        resolved_data_root = Path.cwd() / resolved_data_root
    resolved_data_root = resolved_data_root.resolve(strict=False)

    candidate_error: ConfigurationError | None = None
    if not selected.exists():
        if explicit:
            candidate_error = ConfigurationError(
                "The selected Agent UI configuration file does not exist.",
                code="settings_unavailable",
                details={"path": str(selected)},
            )
        configuration = None
        exists = False
    else:
        try:
            configuration = await load_agent_ui_configuration(selected)
        except ConfigurationError as exc:
            configuration = None
            candidate_error = exc
        exists = True

    process = (
        empty_agent_ui_configuration().document.process if configuration is None else configuration.document.process
    )
    settings = AgentUiSettings(
        storage=StorageSettings(data_root=resolved_data_root),
        log_level=process.log_level,
        log_format=process.log_format,
    )
    return AgentUiSettingsSource(
        configuration=configuration,
        settings=settings,
        path=selected,
        explicit=explicit,
        exists=exists,
        candidate_error=candidate_error,
    )


def ensure_default_directories(source: AgentUiSettingsSource) -> None:
    """Create only the bootstrap-selected local data root."""

    source.settings.storage.data_root.mkdir(parents=True, exist_ok=True)


__all__ = [
    "AgentUiSettingsSource",
    "default_agent_ui_settings",
    "default_agent_ui_settings_path",
    "ensure_default_directories",
    "load_agent_ui_settings",
]
