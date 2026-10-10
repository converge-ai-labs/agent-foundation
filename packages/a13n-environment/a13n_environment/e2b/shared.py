"""E2B shared implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import SecretStr

from .._backend import BackendReference
from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import (
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_EXECUTION_ACTIONS,
    EnvironmentAction,
    EnvironmentDescriptor,
    EnvironmentMountDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
    decode_target_state,
)
from .configuration import (
    PROVIDER_KEY,
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
    E2BProviderStateData,
)
from .errors import provider_error

if TYPE_CHECKING:
    from e2b.sandbox.sandbox_api import SandboxInfo


class E2BReference(BackendReference):
    def __init__(
        self,
        configuration: E2BEnvironmentConfiguration,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: E2BProviderRuntime,
    ) -> None:
        super().__init__(state)
        self._configuration = configuration
        self._environment_id = environment_id
        self._runtime = runtime
        self._state = decode_target_state(
            PROVIDER_KEY, state, E2BProviderStateData, fingerprint=configuration.fingerprint
        )
        self._native_id = self._state.environment_id if self._state else environment_id

    @property
    def provider_key(self) -> str:
        return PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

    def _options(self):
        from e2b.connection_config import ApiParams

        return ApiParams(
            api_key=self._runtime.api_key.get_secret_value(),
            domain=self._runtime.domain,
            api_url=self._runtime.api_url or f"https://api.{self._runtime.domain}",
            request_timeout=self._configuration.request_timeout_seconds,
        )

    def _validate_target(self, target: SandboxInfo) -> None:
        if (
            target.metadata.get("a13n_environment") != self._native_id
            or target.metadata.get("a13n_configuration") != self._configuration.fingerprint
        ):
            raise provider_error("provider_target_conflict", Category.CONFLICT)
        if self._state is not None and target.sandbox_id != self._state.sandbox_id:
            raise provider_error("provider_target_conflict", Category.CONFLICT)


def descriptor(
    configuration: E2BEnvironmentConfiguration, generation: str = "unprepared", identity: str | None = None
) -> EnvironmentDescriptor:
    actions = {action for action in FILE_EXECUTION_ACTIONS if not action.value.startswith("environment.state.")}
    actions -= {EnvironmentAction.PROCESS_SIGNAL, EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE}
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=identity,
        operation_families=frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in actions),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        mounts=(EnvironmentMountDescriptor(name="root", path="/"),),
        limits={
            "max_value_bytes": configuration.max_file_bytes,
            "max_observation_bytes": configuration.max_observation_bytes,
            "max_active_observations": configuration.max_active_observations,
            "max_retained_output_bytes": configuration.max_retained_output_bytes,
        },
    )


@dataclass(frozen=True, slots=True)
class E2BProviderRuntime:
    api_key: SecretStr = field(repr=False)
    domain: str = "e2b.dev"
    api_url: str | None = None

    def __post_init__(self) -> None:
        E2BCredential(api_key=self.api_key)
        E2BConnectionConfiguration(domain=self.domain, api_url=self.api_url)
