"""Docker shared implementation."""

from __future__ import annotations

import hashlib
import json
import os

from pydantic import Field, PrivateAttr

from a13n_harness.environment.errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    provider_error,
)
from a13n_harness.environment.models import (
    FILE_EXECUTION_ACTIONS,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
    decode_target_state,
)
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment._backend import BackendReference
from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerProviderStateData,
)
from a13n_harness.providers.environment.docker.connection import DockerHttpConfiguration
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime

_KEY = "docker"

_ENGINE_TEARDOWN_SECONDS = 10.0


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


class DockerReference(BackendReference):
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

    @property
    def provider_key(self) -> str:
        return _KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

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

    async def _check_initialized(self, commands: DockerCommands) -> None:
        await commands.execute(
            [
                self.config.python,
                "-I",
                "-c",
                "import os; assert os.path.isfile('/tmp/a13n/initialized'), 'Initialization incomplete'",
            ]
        )


def _missing() -> EnvironmentProviderError:
    return provider_error(
        _KEY,
        "environment_not_found",
        EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
    )


class DockerConnectionConfiguration(EnvironmentProviderConfiguration):
    envd_http: DockerHttpConfiguration | None = Field(default=None, title="Envd HTTP connection")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)

    docker_host: str = Field(
        default_factory=lambda: os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"), min_length=1
    )


def _fingerprint(configuration: DockerEnvironmentConfiguration) -> str:
    values = configuration.model_dump(mode="json", include=set(DockerEnvironmentConfiguration.model_fields))
    # Existing native targets must keep the fingerprint computed before desktop mode existed.
    if not configuration.desktop:
        values.pop("desktop")
    if configuration.execution == "native":
        values.pop("execution")
    return hashlib.sha256(
        json.dumps(
            values,
            sort_keys=True,
        ).encode()
    ).hexdigest()
