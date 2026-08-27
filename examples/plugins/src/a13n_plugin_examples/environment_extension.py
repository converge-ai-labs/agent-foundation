"""An aggregate-wide Environment run extension and its safe package factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from a13n_harness import (
    EnvironmentRunExtension,
    EnvironmentRunExtensionContext,
    EnvironmentRunExtensionFactory,
    EnvironmentRunExtensionFactoryContext,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

EXTENSION_KEY = "example.workspace-marker"


class WorkspaceMarkerConfiguration(BaseModel):
    """Package-owned JSON configuration for the workspace marker extension."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    marker_path: str = Field(default="/workspace/.a13n-run", min_length=1, max_length=256)
    label: str = Field(default="example", min_length=1, max_length=64)

    @field_validator("marker_path")
    @classmethod
    def _workspace_path(cls, value: str) -> str:
        if not value.startswith("/workspace/") or "\x00" in value:
            raise ValueError("marker_path must be beneath /workspace")
        return value


class WorkspaceMarkerExtension(EnvironmentRunExtension):
    """Create a run marker after restore and remove it before provider teardown."""

    def __init__(self, *, extension_id: str, configuration: WorkspaceMarkerConfiguration) -> None:
        self._extension_id = extension_id
        self.configuration = configuration
        self.entered = False
        self.exited = False

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context: EnvironmentRunExtensionContext):
        content = f"{self.configuration.label}:{context.run_id}\n"
        await context.environment.files.write_text(
            self.configuration.marker_path,
            content,
            mode="create",
        )
        self.entered = True
        try:
            yield
        finally:
            await context.environment.files.remove(self.configuration.marker_path)
            self.exited = True


class WorkspaceMarkerExtensionFactory(EnvironmentRunExtensionFactory):
    """Create fresh inert marker extensions from detached JSON configuration."""

    @classmethod
    def extension_key(cls) -> str:
        return EXTENSION_KEY

    def create_extension(
        self,
        context: EnvironmentRunExtensionFactoryContext,
    ) -> EnvironmentRunExtension:
        try:
            parsed = WorkspaceMarkerConfiguration.model_validate(
                dict(context.configuration),
                strict=True,
            )
        except ValidationError as exc:
            raise ValueError("Invalid example.workspace-marker configuration.") from exc
        return WorkspaceMarkerExtension(
            extension_id=context.extension_id,
            configuration=parsed,
        )
