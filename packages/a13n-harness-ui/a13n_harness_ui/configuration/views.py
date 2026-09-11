"""Detached views of accepted configuration sources, never raw credential stores."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import ConfigurationError

from .models import LoadedHarnessUiConfiguration
from .mutation import ConfigurationMutationResult, _select_target


class ConfigurationSourceInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    relative_path: str
    source_digest: str
    resource_kind: str
    resource_ids: tuple[str, ...]
    writable: bool
    content_available: bool


class ConfigurationSourceCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation_digest: str | None
    sources: tuple[ConfigurationSourceInfo, ...]


class ConfigurationSourceView(ConfigurationSourceInfo):
    generation_digest: str
    content: str | None


class ConfigurationValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_digest: str


class ConfigurationPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    action: Literal["created", "updated", "deleted", "unchanged"]
    relative_path: str
    source_digest: str | None
    generation_digest: str

    @classmethod
    def from_result(cls, result: ConfigurationMutationResult) -> "ConfigurationPublication":
        return cls(
            action=result.action,
            relative_path=result.relative_path,
            source_digest=result.source_digest,
            generation_digest=result.configuration.source_digest,
        )


def source_catalog(source: LoadedHarnessUiConfiguration | None, configuration_path: Path) -> ConfigurationSourceCatalog:
    if source is None:
        return ConfigurationSourceCatalog(generation_digest=None, sources=())
    entries: list[ConfigurationSourceInfo] = []
    for item in source.sources:
        try:
            _select_target(configuration_path, item.relative_path, False)
            writable = True
        except ConfigurationError:
            writable = False
        entries.append(
            ConfigurationSourceInfo(
                relative_path=item.relative_path,
                source_digest=item.source_digest,
                resource_kind=item.resource_kind,
                resource_ids=item.indexed_resource_ids,
                writable=writable,
                content_available=item.resource_kind != "mcp_server",
            )
        )
    return ConfigurationSourceCatalog(generation_digest=source.source_digest, sources=tuple(entries))


def source_view(
    source: LoadedHarnessUiConfiguration | None, configuration_path: Path, relative_path: str
) -> ConfigurationSourceView:
    catalog = source_catalog(source, configuration_path)
    for info in catalog.sources:
        if info.relative_path == relative_path:
            assert source is not None
            return ConfigurationSourceView(
                **info.model_dump(),
                generation_digest=source.source_digest,
                content=source.source(relative_path).content if info.content_available else None,
            )
    raise ConfigurationError("The accepted configuration source does not exist.", code="configuration_source_not_found")
