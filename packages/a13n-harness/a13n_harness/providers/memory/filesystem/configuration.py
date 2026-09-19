"""Pure typed file-backed Memory storage inputs; no credential grants file access."""

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilesystemMemoryStorage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str | None = Field(default=None, min_length=1, max_length=128)
    root: str = Field(default="/memory", min_length=1, max_length=2048)

    @field_validator("root")
    @classmethod
    def normalized_root(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or str(path) != value or ".." in path.parts or "\\" in value:
            raise ValueError("Memory root must be a normalized absolute Environment path")
        return value


class FilesystemMemoryConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    storage: FilesystemMemoryStorage = Field(default_factory=FilesystemMemoryStorage)


class FilesystemMemoryCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
