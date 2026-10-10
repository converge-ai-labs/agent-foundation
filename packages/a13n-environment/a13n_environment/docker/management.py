"""Docker management implementation."""

from __future__ import annotations

import asyncio
from typing import Literal

from .._backend import ManagementBackend
from ..errors import (
    EnvironmentProviderErrorCategory,
    provider_error,
)
from ..models import (
    EnvironmentError,
    EnvironmentState,
)
from .commands import DockerCommands
from .configuration import DockerProviderStateData
from .errors import engine_errors
from .shared import _KEY, DockerReference, _missing


def resolve_image(client, image_ref: str, *, pull_policy: Literal["never", "if_missing"] = "if_missing"):
    from docker.errors import ImageNotFound

    try:
        return client.images.get(image_ref), "local"
    except ImageNotFound:
        if pull_policy == "never":
            raise provider_error(
                _KEY,
                "environment_image_missing",
                EnvironmentProviderErrorCategory.INVALID,
                description=(
                    "Image is missing from the configured Docker Engine and pull_policy is never. "
                    "Build or load it first; for the supplied image run make image-sandbox."
                ),
            ) from None
        return client.images.pull(image_ref), "pulled"


class DockerManagement(DockerReference, ManagementBackend):
    def validate_management(self) -> None:
        if self.target is not None and self.target.environment_id != self.environment_id:
            raise provider_error(_KEY, "provider_target_conflict", EnvironmentProviderErrorCategory.CONFLICT)

    def _remember(self, container) -> str:
        if not container.id:
            raise EnvironmentError("Docker did not return a container identity", code="environment_provider_failure")
        self.target = DockerProviderStateData(
            environment_id=self.allocation_id, container_id=container.id, configuration_fingerprint=self.fingerprint
        )
        self._cache_state(
            EnvironmentState(provider_key=_KEY, state_version="1", state=self.target.model_dump(mode="json"))
        )
        return self.target.container_id

    async def create(self) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            created = container is None
            if created:
                if self.target is not None:
                    raise _missing()
                creation = asyncio.create_task(asyncio.to_thread(self._create))
                try:
                    container = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    result = await asyncio.gather(creation, return_exceptions=True)
                    if not isinstance(result[0], BaseException):
                        self._remember(result[0])
                    raise
            container_id = self._remember(container)
            if container.status != "running":
                await asyncio.to_thread(container.start)
            commands = DockerCommands(self.runtime.engine, container_id, self.config)
            try:
                if created:
                    await commands.execute(
                        [
                            self.config.python,
                            "-I",
                            "-c",
                            "import os; os.makedirs('/workspace',exist_ok=True); os.makedirs('/tmp/a13n',exist_ok=True)",
                        ]
                    )
                    if self.config.init_script:
                        await commands.execute(
                            [self.config.shell, "-c", self.config.init_script],
                            maximum=self.config.max_output_preview_bytes,
                        )
                    await commands.execute(
                        [self.config.python, "-I", "-c", "open('/tmp/a13n/initialized','w').close()"]
                    )
                else:
                    await self._check_initialized(commands)
            finally:
                commands.closed = True

    async def start(self) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise _missing()
            container_id = self._remember(container)
            if container.status != "running":
                await asyncio.to_thread(container.start)
            commands = DockerCommands(self.runtime.engine, container_id, self.config)
            try:
                await self._check_initialized(commands)
            finally:
                commands.closed = True

    def _create(self):
        from docker.types import Mount

        client = self.runtime.engine.client
        image, _ = resolve_image(client, self.config.image, pull_policy=self.config.pull_policy)
        image_id = image.id
        if not isinstance(image_id, str) or not image_id:
            raise EnvironmentError("Docker did not return an image identity", code="environment_provider_failure")
        return client.containers.create(
            image_id,
            name="a13n-" + self.allocation_id,
            entrypoint=[self.config.python, "-I", "-c"],
            command=["import signal; signal.pause()"],
            init=True,
            detach=True,
            user=self.config.user or image.labels.get("ai.a13n.environment.user"),
            working_dir="/workspace",
            environment=self.config.environment,
            network_mode="none" if self.config.disable_network else "bridge",
            mounts=[Mount(str(m.target), m.source, type="bind", read_only=m.read_only) for m in self.config.mounts],
            labels=self.labels,
            nano_cpus=int(self.config.cpus * 1_000_000_000) if self.config.cpus is not None else None,
            mem_limit=int(self.config.memory_gb * 1_000_000_000) if self.config.memory_gb is not None else None,
            pids_limit=self.config.pids_limit,
        )

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                return "absent"
            self._remember(container)
            return "running" if container.status == "running" else "stopped"

    async def stop(self) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            if container is not None and container.status == "running":
                await asyncio.to_thread(container.stop, timeout=self.config.stop_grace_seconds)

    async def destroy(self) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            if container is not None:
                await asyncio.to_thread(container.remove, force=True)
