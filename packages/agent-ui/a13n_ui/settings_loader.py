"""Selection of one full Agent UI configuration source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from a13n_ui.configuration import (
    LoadedAgentUiConfiguration,
    empty_agent_ui_configuration,
    load_agent_ui_configuration,
)
from a13n_ui.errors import ConfigurationError
from a13n_ui.settings import AgentUiSettings, default_agent_ui_root

_DEFAULT_SETTINGS_NAME = "a13n-ui.yaml"


@dataclass(frozen=True, slots=True)
class AgentUiSettingsSource:
    """One selected full configuration and its process settings."""

    configuration: LoadedAgentUiConfiguration
    path: Path
    explicit: bool
    exists: bool

    @property
    def settings(self) -> AgentUiSettings:
        return self.configuration.document.process


def default_agent_ui_settings_path() -> Path:
    return default_agent_ui_root() / _DEFAULT_SETTINGS_NAME


def default_agent_ui_settings() -> AgentUiSettings:
    return empty_agent_ui_configuration().document.process


async def load_agent_ui_settings(path: Path | None = None) -> AgentUiSettingsSource:
    """Select one YAML source without profiles, merging, or cwd discovery."""

    explicit = path is not None
    selected = (path or default_agent_ui_settings_path()).expanduser().resolve(strict=False)
    if selected.suffix.lower() not in {".yaml", ".yml"}:
        raise ConfigurationError(
            "Agent UI configuration must use YAML.",
            code="settings_path_invalid",
            details={"path": str(selected)},
        )
    if not selected.exists():
        if explicit:
            raise ConfigurationError(
                "The selected Agent UI configuration file does not exist.",
                code="settings_unavailable",
                details={"path": str(selected)},
            )
        return AgentUiSettingsSource(
            configuration=empty_agent_ui_configuration(),
            path=selected,
            explicit=False,
            exists=False,
        )
    configuration = await load_agent_ui_configuration(selected)
    return AgentUiSettingsSource(
        configuration=configuration,
        path=selected,
        explicit=explicit,
        exists=True,
    )


def ensure_default_directories(source: AgentUiSettingsSource) -> None:
    """Create only the selected local data root."""

    source.settings.storage.data_root.mkdir(parents=True, exist_ok=True)


__all__ = [
    "AgentUiSettingsSource",
    "default_agent_ui_settings",
    "default_agent_ui_settings_path",
    "ensure_default_directories",
    "load_agent_ui_settings",
]
