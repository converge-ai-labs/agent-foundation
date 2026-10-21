"""Docker management implementation."""

from __future__ import annotations

import asyncio
import json
from pathlib import PurePosixPath
from typing import Literal

from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory, provider_error
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.configuration import DockerProviderStateData
from a13n_harness.providers.environment.docker.errors import engine_errors
from a13n_harness.providers.environment.docker.http import http_execution, http_runtime
from a13n_harness.providers.environment.docker.shared import _KEY, DockerReference, _missing
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache

from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environment.desktop_access import Description, Reply, Request, command, decode
from a13n_service.providers.environment.metrics import (
    DeviceObservation,
    MetricsLaunch,
    ScrapeTarget,
    observe,
    scrape_target,
)

from .configuration import DockerRecipe


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
    config: DockerRecipe
    _metrics: MetricsLaunch | None = None

    def configure_metrics(self, metrics: MetricsLaunch) -> None:
        if self.config.execution != "envd_http":
            raise self._unsupported()
        self._metrics = metrics

    async def scrape_target(self) -> ScrapeTarget:
        if self.config.execution != "envd_http":
            raise self._unsupported()
        async with http_runtime(self) as runtime:
            return scrape_target(runtime)

    async def observe_device(self, *, credential_cache: ManagedCredentialCache | None = None) -> DeviceObservation:
        if self.config.execution != "envd_http":
            raise self._unsupported()
        async with http_runtime(self, credential_cache=credential_cache) as runtime:
            return await observe(runtime, self.allocation_id)

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
            if self.config.execution == "envd_http":
                await self._wait_envd()

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
            if self.config.execution == "envd_http":
                await self._wait_envd()

    async def _wait_envd(self) -> None:
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                while True:
                    execution = http_execution(self)
                    try:
                        await execution.open(execution_id="desktop-readiness")
                        if not self.config.desktop:
                            return
                        await execution.check_ready(frozenset({"computer"}))
                        computer = execution.operations.computer
                        if computer is not None:
                            description = await computer.describe()
                            if description.observe_ready and description.input_ready and description.targets:
                                return
                    except EnvironmentProviderError as error:
                        if (
                            error.category
                            not in {
                                EnvironmentProviderErrorCategory.UNAVAILABLE,
                                EnvironmentProviderErrorCategory.TIMEOUT,
                            }
                            or error.code == "environment_stopped"
                        ):
                            raise
                    except EnvironmentError as error:
                        if error.code not in {"environment_provider_failure", "environment_unavailable"}:
                            raise
                    finally:
                        await execution.close()
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise provider_error(
                _KEY,
                "envd_not_ready",
                EnvironmentProviderErrorCategory.UNAVAILABLE,
                description="Envd or its required desktop did not become ready; the allocated container is retained for inspection",
            ) from None

    def _create(self):
        from docker.types import Mount

        connection = self.runtime.envd_http
        if self.config.execution == "envd_http":
            if connection is None:
                raise provider_error(
                    _KEY,
                    "envd_http_configuration_required",
                    EnvironmentProviderErrorCategory.INVALID,
                    description="Configure the Docker connection's separate envd HTTP route before using EIP execution",
                )
            if any(
                PurePosixPath(connection.credential_file).is_relative_to(mount.target) for mount in self.config.mounts
            ):
                raise provider_error(
                    _KEY,
                    "envd_credential_not_private",
                    EnvironmentProviderErrorCategory.INVALID,
                    description="The per-instance envd credential must not reside in an external host mount",
                )
        client = self.runtime.engine.client
        image, _ = resolve_image(client, self.config.image, pull_policy=self.config.pull_policy)
        image_id = image.id
        if not isinstance(image_id, str) or not image_id:
            raise EnvironmentError("Docker did not return an image identity", code="environment_provider_failure")
        entrypoint = None if self.config.execution == "envd_http" else [self.config.python, "-I", "-c"]
        command = None if self.config.execution == "envd_http" else ["import signal; signal.pause()"]
        user = (
            "root"
            if self.config.execution == "envd_http"
            else self.config.user or image.labels.get("ai.a13n.environment.user")
        )
        environment = dict(self.config.environment)
        if self._metrics is not None:
            environment.update(self._metrics.environment)
        if self.config.desktop_assistance:
            environment["A13N_DESKTOP_ACCESS"] = "true"
        ports = None
        if self.config.execution == "envd_http":
            assert connection is not None
            ports = {"8888/tcp": (connection.listen_address, 0)}
            environment.update(
                {
                    "A13N_ENVD_DEVICE_ID": self.allocation_id,
                    "A13N_ENVD_TRANSPORT": "http",
                    "A13N_ENVD_HTTP_BIND": "0.0.0.0:8888",
                    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                    "A13N_ENVD_HTTP_CREDENTIAL_FILE": connection.credential_file,
                    "A13N_ENVD_CONFIG_JSON": json.dumps(
                        {
                            **(
                                {"monitoring": {"sample_interval_ms": self._metrics.sample_interval_ms}}
                                if self._metrics is not None
                                else {}
                            ),
                            "limits": {
                                "max_file_bytes": self.config.max_file_bytes,
                                "max_output_preview_bytes": self.config.max_output_preview_bytes,
                                "max_output_bytes_per_stream": self.config.max_output_bytes_per_stream,
                                "max_spool_bytes": self.config.max_spool_bytes,
                                "max_processes": self.config.max_concurrent_processes,
                            },
                        }
                    ),
                }
            )
        return client.containers.create(
            image_id,
            name="a13n-" + self.allocation_id,
            init=self.config.execution == "native",
            detach=True,
            working_dir="/workspace",
            environment=environment,
            ports=ports,
            network_mode="none" if self.config.disable_network else "bridge",
            mounts=[Mount(str(m.target), m.source, type="bind", read_only=m.read_only) for m in self.config.mounts],
            labels=self.labels,
            nano_cpus=int(self.config.cpus * 1_000_000_000) if self.config.cpus is not None else None,
            mem_limit=int(self.config.memory_gb * 1_000_000_000) if self.config.memory_gb is not None else None,
            pids_limit=self.config.pids_limit,
            entrypoint=entrypoint,
            command=command,
            user=user,
        )

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                return "absent"
            self._remember(container)
            return "running" if container.status == "running" else "stopped"

    async def desktop_access(self, request: Request) -> Reply:
        if not self.config.desktop or self.target is None:
            raise self._unsupported()
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            if container is None or container.status != "running":
                raise provider_error(_KEY, "desktop_target_unavailable", EnvironmentProviderErrorCategory.CONFLICT)
            # Management runs as root independently of the Agent's configured execution user.
            commands = DockerCommands(
                self.runtime.engine, self.target.container_id, self.config.model_copy(update={"user": "root"})
            )
            try:
                reply = decode(_KEY, request, await commands.execute(command(request), maximum=16384))
                if isinstance(reply, Description) and self.config.desktop_assistance and self.config.desktop_viewer_url:
                    return reply.model_copy(
                        update={
                            "websocket_url": self.config.desktop_viewer_url.replace(
                                "{container_id}", self.target.container_id
                            )
                        }
                    )
                return reply
            finally:
                commands.closed = True

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
