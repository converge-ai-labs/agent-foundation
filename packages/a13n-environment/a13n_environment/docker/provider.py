"""One durable native Docker container per logical Environment."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from typing import Literal

from anyio import move_on_after
from pydantic import BaseModel, Field

from .._backend import BackendTarget
from .._backend_factory import BackendFactory
from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from .._local_retention import LocalRetentionStore, create_retention_root
from ..definition import EnvironmentProviderDefinition
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    provider_error,
)
from ..management import EnvironmentProviderConfiguration
from ..models import (
    FILE_EXECUTION_ACTIONS,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
    decode_target_state,
)
from ..operations import EnvironmentOperations
from .commands import DockerCommands
from .configuration import DockerEnvironmentConfiguration, DockerProviderStateData
from .errors import engine_errors
from .processes import DockerProcesses
from .runtime import DockerProviderRuntime, DockerSDKEngine

_KEY = "docker"
_ENGINE_TEARDOWN_SECONDS = 10.0


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


def descriptor(generation: str, config: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    return EnvironmentDescriptor(
        generation=generation,
        working_directory="/workspace",
        backing_identity=None if generation == "unprepared" else generation,
        operation_families=frozenset({"files", "shell", "processes", "outputs", "ports"}),
        permissions=EnvironmentPermissionSet(
            operations=frozenset(
                action for action in FILE_EXECUTION_ACTIONS if not action.value.startswith("environment.state.")
            )
        ),
        mounts=(
            EnvironmentMountDescriptor(name="workspace", path="/workspace"),
            *(
                EnvironmentMountDescriptor(name=f"external-{i}", path=str(m.target))
                for i, m in enumerate(config.mounts)
            ),
        ),
    )


class DockerTarget(BackendTarget):
    def __init__(
        self,
        configuration: DockerEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: DockerProviderRuntime,
    ) -> None:
        super().__init__(state)
        self.config = configuration
        self._environment_id = environment_id
        self.runtime = runtime
        self.fingerprint = _fingerprint(configuration)
        self.target = decode_target_state(_KEY, state, DockerProviderStateData, fingerprint=self.fingerprint)
        self._operations = EnvironmentOperations()
        self.commands: DockerCommands | None = None
        self.processes: DockerProcesses | None = None
        self.retention: LocalRetentionStore | None = None
        self._availability = EnvironmentAvailability(status="preparing")

    @property
    def provider_key(self) -> str:
        return _KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return descriptor(self.target.container_id if self.target else "unprepared", self.config)

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    @property
    def allocation_id(self) -> str:
        return self.target.environment_id if self.target else self.environment_id

    @property
    def labels(self) -> dict[str, str]:
        return {"a13n.environment": self.allocation_id, "a13n.configuration": self.fingerprint}

    def _lookup(self):
        from docker.errors import NotFound

        try:
            container = self.runtime.engine.client.containers.get(
                self.target.container_id if self.target else "a13n-" + self.allocation_id
            )
        except NotFound:
            return None
        if any(container.labels.get(key) != value for key, value in self.labels.items()):
            raise EnvironmentError("Docker target belongs to another configuration", code="environment_target_conflict")
        return container

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

    async def _check_initialized(self, commands: DockerCommands) -> None:
        await commands.execute(
            [
                self.config.python,
                "-I",
                "-c",
                "import os; assert os.path.isfile('/tmp/a13n/initialized'), 'Initialization incomplete'",
            ]
        )

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

    async def open(self, *, execution_id: str) -> None:
        if self.target is None:
            raise provider_error(_KEY, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID)
        container_id = self.target.container_id
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise _missing()
            if container.status != "running":
                raise EnvironmentError("Docker container is stopped", code="environment_unavailable")
            self.commands = DockerCommands(self.runtime.engine, container_id, self.config)
            self.commands.execution_id = execution_id
            await self._check_initialized(self.commands)
            self.retention = LocalRetentionStore(
                root=await create_retention_root(prefix="a13n-docker-output-"),
                execution_id=execution_id,
                generation=container_id,
                max_spool_bytes=self.config.max_spool_bytes,
            )
            self.processes = DockerProcesses(self.commands, self.environment_id, self.retention)
            self._operations = EnvironmentOperations(
                files=GuestFiles(self.commands),
                shell=self.processes,
                processes=self.processes,
                outputs=self.retention,
                ports=GuestPorts(self.commands),
            )
            self._availability = EnvironmentAvailability(
                status="available", ready_families=self.descriptor.operation_families
            )

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

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise EnvironmentError("Docker container is absent", code="environment_unavailable")
            if container.status != "running":
                raise EnvironmentError("Docker container is not running", code="environment_unavailable")

    async def close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        try:
            if self.processes is not None:
                await self.processes.close()
        finally:
            if self.commands is not None:
                self.commands.closed = True
            if self.retention is not None:
                await self.retention.close()

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


def _missing() -> EnvironmentProviderError:
    return provider_error(
        _KEY,
        "environment_not_found",
        EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
    )


class DockerConnectionConfiguration(EnvironmentProviderConfiguration):
    docker_host: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"), min_length=1
    )


async def _release_engine(acquisition: asyncio.Task[DockerSDKEngine]) -> None:
    """Close an engine that finishes acquiring after its caller was already cancelled."""
    with move_on_after(_ENGINE_TEARDOWN_SECONDS, shield=True), contextlib.suppress(Exception):
        engine = await acquisition
        await engine.close()


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> DockerProviderRuntime:
    """Acquire the engine; a cancelled caller never leaks a live Docker client."""
    del credential
    if not isinstance(configuration, DockerConnectionConfiguration):
        raise TypeError("Docker requires DockerConnectionConfiguration")
    acquisition = asyncio.create_task(asyncio.to_thread(DockerSDKEngine.connect, configuration.docker_host))
    try:
        engine = await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        await _release_engine(acquisition)
        raise
    return DockerProviderRuntime(engine)


def _describe(configuration: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DockerEnvironmentConfiguration):
        raise TypeError("Docker requires DockerEnvironmentConfiguration")
    return descriptor("unprepared", configuration)


def _fingerprint(configuration: DockerEnvironmentConfiguration) -> str:
    return hashlib.sha256(
        json.dumps(configuration.model_dump(mode="json", exclude={"pull_policy"}), sort_keys=True).encode()
    ).hexdigest()


def _identity(*, configuration: DockerEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    data = decode_target_state(_KEY, state, DockerProviderStateData, fingerprint=_fingerprint(configuration))
    if data is None:
        raise provider_error(_KEY, "provider_state_required", EnvironmentProviderErrorCategory.INVALID)
    return data.container_id


def _construct(
    *,
    configuration: DockerEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
    operation_id: str,
) -> BackendTarget:
    del operation_id
    if not isinstance(configuration, DockerEnvironmentConfiguration) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    return DockerTarget(configuration, environment_id, state, runtime)


_factory = BackendFactory(
    key=_KEY,
    environment_model=DockerEnvironmentConfiguration,
    target=_construct,
    describe=_describe,
    runtime_factory=_runtime,
    target_identity=_identity,
)


DOCKER = EnvironmentProviderDefinition(
    type="docker",
    display_name="Docker",
    configuration_model=DockerConnectionConfiguration,
    environment_model=DockerEnvironmentConfiguration,
    connector_factory=_factory.connector,
    provider_factory=_factory.provider,
    describe_environment=_describe,
    target_identity=_identity,
    supports_stop=True,
    supports_destroy=True,
)
