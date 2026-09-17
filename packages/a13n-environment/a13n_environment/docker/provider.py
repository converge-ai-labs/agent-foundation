"""One durable native Docker container per logical Environment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from .._local_retention import LocalRetentionStore
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..management import Environment
from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .commands import DockerCommands
from .configuration import DockerProviderConfiguration, DockerProviderStateData
from .errors import engine_errors
from .processes import DockerProcesses
from .runtime import DockerProviderRuntime

_KEY = "a13n.docker"


def descriptor(generation: str, config: DockerProviderConfiguration) -> EnvironmentDescriptor:
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
        configuration: DockerProviderConfiguration,
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
        self.target = None if state is None else DockerProviderStateData.model_validate(state.state)
        if state is not None and (state.provider_key != _KEY or state.state_version != "1"):
            raise ValueError("Invalid Docker state envelope")
        if self.target is not None and (
            (runtime.managed and self.target.environment_id != environment_id)
            or self.target.configuration_fingerprint != self.fingerprint
        ):
            raise ValueError("Docker state does not match Environment configuration")
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

    async def _prepare(
        self, *, thread_id: str, run_id: str, agent_instance_id: str, mount_id: str, host_refs: Mapping[str, str]
    ) -> None:
        with engine_errors(mutation=True):
            container = await asyncio.to_thread(self._lookup)
            created = container is None
            if created:
                if not self.runtime.managed:
                    raise _missing()
                container = await asyncio.to_thread(self._create)
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
        from docker.errors import ImageNotFound
        from docker.types import Mount

        client = self.runtime.engine.client
        if self.config.pull_policy == "always":
            client.images.pull(self.config.image)
        else:
            try:
                client.images.get(self.config.image)
            except ImageNotFound:
                if self.config.pull_policy == "never":
                    raise
                client.images.pull(self.config.image)
        return client.containers.create(
            self.config.image,
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
            mem_limit=self.config.memory_mib * 1024 * 1024 if self.config.memory_mib is not None else None,
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
    return EnvironmentProviderError(
        "Docker container is absent",
        code="environment_not_found",
        category=EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
        context=EnvironmentProviderErrorContext(provider_key=_KEY),
    )
