"""One durable native Docker container per logical Environment."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from anyio import move_on_after
from pydantic import BaseModel, Field

from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from .._local_retention import LocalRetentionStore
from ..definition import EnvironmentProviderDefinition
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    provider_error,
)
from ..management import Environment, EnvironmentProviderConfiguration
from ..models import (
    EnvironmentAction,
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


def resolve_image(client, image_ref: str):
    from docker.errors import ImageNotFound

    try:
        return client.images.get(image_ref), "local"
    except ImageNotFound:
        return client.images.pull(image_ref), "pulled"


def descriptor(generation: str, config: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    return EnvironmentDescriptor(
        generation=generation,
        working_directory="/workspace",
        backing_identity=None if generation == "unprepared" else generation,
        operation_families=frozenset({"files", "shell", "processes", "outputs", "ports"}),
        permissions=EnvironmentPermissionSet(
            operations=frozenset(
                action for action in EnvironmentAction if not action.value.startswith("environment.state.")
            )
        ),
        mounts=(
            EnvironmentMountDescriptor(name="workspace", path="/workspace", read_only=False),
            *(
                EnvironmentMountDescriptor(name=f"external-{i}", path=str(m.target), read_only=m.read_only)
                for i, m in enumerate(config.mounts)
            ),
        ),
    )


class DockerEnvironment(Environment):
    recover_on_unavailable = True

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
        self.fingerprint = hashlib.sha256(
            json.dumps(configuration.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        self.target = decode_target_state(_KEY, state, DockerProviderStateData, fingerprint=self.fingerprint)
        if self.target is not None and runtime.managed and self.target.environment_id != environment_id:
            raise provider_error(_KEY, "provider_target_conflict", EnvironmentProviderErrorCategory.CONFLICT)
        self._operations = EnvironmentOperations()
        self.commands: DockerCommands | None = None
        self.processes: DockerProcesses | None = None
        self.retention: LocalRetentionStore | None = None
        self._available = False

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
        return EnvironmentAvailability(
            status="available" if self._available else "unavailable",
            ready_families=self.descriptor.operation_families if self._available else frozenset(),
        )

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
            if not self.runtime.managed:
                return None
            try:
                container = self.runtime.engine.client.containers.get("a13n-" + self.allocation_id)
            except NotFound:
                return None
        if any(container.labels.get(key) != value for key, value in self.labels.items()):
            raise EnvironmentError("Docker target belongs to another configuration", code="environment_target_conflict")
        return container

    async def _prepare(self, *, mount_id: str) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            created = container is None
            if created:
                if not self.runtime.managed:
                    raise _missing()
                creation = asyncio.create_task(asyncio.to_thread(self._create))
                try:
                    container = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    await asyncio.gather(creation, return_exceptions=True)
                    raise
            container_id = container.id
            if container_id is None:
                raise EnvironmentError(
                    "Docker did not return a container identity", code="environment_provider_failure"
                )
            self.target = DockerProviderStateData(
                environment_id=self.allocation_id,
                container_id=container_id,
                configuration_fingerprint=self.fingerprint,
            )
            self._known_state = EnvironmentState(
                provider_key=_KEY, state_version="1", state=self.target.model_dump(mode="json")
            )
            if container.status != "running":
                if not self.runtime.managed:
                    raise EnvironmentError("External Docker container is stopped", code="environment_unavailable")
                await asyncio.to_thread(container.start)
            if self.processes is not None:
                await self.processes.close()
            if self.retention is not None:
                await self.retention.close()
            if self.commands is not None:
                self.commands.closed = True
            self.commands = DockerCommands(self.runtime.engine, container_id, self.config)
            self.commands.mount_id = mount_id
            if created:
                await self.commands.execute(
                    [
                        self.config.python,
                        "-I",
                        "-c",
                        "import os; os.makedirs('/workspace',exist_ok=True); os.makedirs('/tmp/a13n',exist_ok=True)",
                    ]
                )
                if self.config.init_script:
                    await self.commands.execute(
                        [self.config.shell, "-c", self.config.init_script], maximum=self.config.max_output_preview_bytes
                    )
                await self.commands.execute(
                    [self.config.python, "-I", "-c", "open('/tmp/a13n/initialized','w').close()"]
                )
            else:
                await self.commands.execute(
                    [
                        self.config.python,
                        "-I",
                        "-c",
                        "import os; assert os.path.isfile('/tmp/a13n/initialized'), 'Initialization incomplete'",
                    ]
                )
            self.retention = LocalRetentionStore(
                root=Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="a13n-docker-output-")),
                mount_id=mount_id,
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
            self._available = True

    def _create(self):
        from docker.types import Mount

        client = self.runtime.engine.client
        image, _ = resolve_image(client, self.config.image)
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
            user=self.config.user,
            working_dir="/workspace",
            environment=self.config.environment,
            network_mode="none" if self.config.disable_network else "bridge",
            mounts=[Mount(str(m.target), m.source, type="bind", read_only=m.read_only) for m in self.config.mounts],
            labels=self.labels,
            nano_cpus=int(self.config.cpus * 1_000_000_000) if self.config.cpus is not None else None,
            mem_limit=int(self.config.memory_gb * 1_000_000_000) if self.config.memory_gb is not None else None,
            pids_limit=self.config.pids_limit,
        )

    def _bind_mount(self, mount_id: str) -> None:
        if self.commands is not None:
            self.commands.mount_id = mount_id
        if self.retention is not None:
            self.retention.bind_mount(mount_id)

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                raise EnvironmentError("Docker container is absent", code="environment_unavailable")
            if container.status != "running":
                raise EnvironmentError("Docker container is not running", code="environment_unavailable")

    async def _close(self) -> None:
        self._available = False
        self._operations = EnvironmentOperations()
        try:
            if self.processes is not None:
                await self.processes.close()
        finally:
            if self.commands is not None:
                self.commands.closed = True
            try:
                if self.retention is not None:
                    await self.retention.close()
            finally:
                if self.runtime.owns_engine:
                    await self.runtime.engine.close()

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        with engine_errors(mutation=False):
            container = await asyncio.to_thread(self._lookup)
            if container is None:
                return "absent"
            self.target = DockerProviderStateData(
                environment_id=self.allocation_id, container_id=container.id, configuration_fingerprint=self.fingerprint
            )
            self._known_state = EnvironmentState(
                provider_key=_KEY, state_version="1", state=self.target.model_dump(mode="json")
            )
            return "running" if container.status == "running" else "stopped"

    async def _stop(self) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            if container is not None and container.status == "running":
                await asyncio.to_thread(container.stop, timeout=self.config.stop_grace_seconds)

    async def _destroy(self) -> None:
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


async def _runtime(
    *, configuration: BaseModel, credential: BaseModel | None, operation_id: str, allow_create: bool
) -> DockerProviderRuntime:
    """Own the acquired engine: a cancelled caller never leaks a live Docker client."""
    del credential, operation_id
    if not isinstance(configuration, DockerConnectionConfiguration):
        raise TypeError("Docker requires DockerConnectionConfiguration")
    acquisition = asyncio.create_task(asyncio.to_thread(DockerSDKEngine.connect, configuration.docker_host))
    try:
        engine = await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        await _release_engine(acquisition)
        raise
    return DockerProviderRuntime(engine, managed=allow_create, owns_engine=True)


def _describe(configuration: DockerEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, DockerEnvironmentConfiguration):
        raise TypeError("Docker requires DockerEnvironmentConfiguration")
    return descriptor("unprepared", configuration)


def _identity(*, configuration: DockerEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    _describe(configuration)
    return None if state is None else DockerProviderStateData.model_validate(state.state).container_id


def _construct(
    *,
    configuration: DockerEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: DockerProviderRuntime | None,
) -> Environment:
    if not isinstance(configuration, DockerEnvironmentConfiguration) or runtime is None:
        raise TypeError("Docker requires typed configuration and runtime")
    return DockerEnvironment(configuration, environment_id, state, runtime)


DOCKER = EnvironmentProviderDefinition(
    type="docker",
    display_name="Docker",
    configuration_model=DockerConnectionConfiguration,
    environment_model=DockerEnvironmentConfiguration,
    construct=_construct,
    describe_environment=_describe,
    target_identity=_identity,
    supports_stop=True,
    supports_destroy=True,
    runtime_factory=_runtime,
)
