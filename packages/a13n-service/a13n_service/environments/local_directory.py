"""Service ownership of Direct Local directories, separate from library adapters."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from anyio import to_thread
from pydantic import JsonValue

from .domain import EnvironmentConfiguration, TemplateConfiguration


@dataclass(frozen=True)
class ManagedLocalDirectory:
    base: Path
    environment_id: str

    @property
    def path(self) -> Path:
        return self.base / "environments" / self.environment_id

    @contextmanager
    def _parent(self, *, create: bool) -> Iterator[int | None]:
        if create:
            self.base.mkdir(parents=True, exist_ok=True)
        try:
            base_fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            yield None
            return
        try:
            if create:
                try:
                    os.mkdir("environments", mode=0o700, dir_fd=base_fd)
                except FileExistsError:
                    pass
            try:
                parent_fd = os.open("environments", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
            except FileNotFoundError:
                yield None
                return
            try:
                yield parent_fd
            finally:
                os.close(parent_fd)
        finally:
            os.close(base_fd)

    def _create(self) -> None:
        with self._parent(create=True) as parent_fd:
            assert parent_fd is not None
            try:
                os.mkdir(self.environment_id, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(self.environment_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(directory_fd)

    def _delete(self) -> None:
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError("Managed local directory deletion requires safe descriptor-relative removal")
        with self._parent(create=False) as parent_fd:
            if parent_fd is None:
                return
            try:
                shutil.rmtree(self.environment_id, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    async def create(self) -> None:
        await to_thread.run_sync(self._create)

    async def delete(self) -> None:
        await to_thread.run_sync(self._delete)


def managed_local_directory(
    provider_type: str, environment_id: str, configuration: EnvironmentConfiguration | TemplateConfiguration
) -> ManagedLocalDirectory | None:
    if provider_type != "a13n.direct-local" or not isinstance(configuration, TemplateConfiguration):
        return None
    if re.fullmatch(r"env_[A-Za-z0-9]+", environment_id) is None:
        raise ValueError("Managed local directory requires a canonical Environment ID")
    root = configuration.configuration.get("root")
    if not isinstance(root, dict) or not isinstance(root.get("path"), str):
        raise ValueError("Direct Local template requires a base root path")
    path = root["path"]
    assert isinstance(path, str)
    base = Path(path).expanduser()
    if not base.is_absolute() or ".." in base.parts:
        raise ValueError("Direct Local base must be an absolute normalized path")
    return ManagedLocalDirectory(base, environment_id)


def instance_configuration(
    provider_type: str, environment_id: str, configuration: EnvironmentConfiguration | TemplateConfiguration
) -> dict[str, JsonValue]:
    directory = managed_local_directory(provider_type, environment_id, configuration)
    if directory is None:
        return configuration.configuration
    root = configuration.configuration["root"]
    assert isinstance(root, dict)
    return {**configuration.configuration, "root": {**root, "path": str(directory.path)}}
