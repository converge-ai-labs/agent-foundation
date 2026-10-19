from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.native.configuration import CommandConfiguration, TargetState
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime, TargetConfiguration
from a13n_harness.providers.environment.native.errors import failure
from pydantic import BaseModel

from ._backend import ManagementBackend
from ._backend_factory import ManagementBackendFactory
from .definition import EnvironmentManagementDefinition, management_definition

_DELETE_POLL_SECONDS = 0.5


class NativeManagement[C: TargetConfiguration, S: TargetState](NativeReference[C, S], ManagementBackend):
    def observe(self, state: S) -> None:
        self.target = state
        self._cache_state(
            EnvironmentState(provider_key=self.provider_key, state_version="1", state=state.model_dump(mode="json"))
        )

    def validate_management(self) -> None:
        if self.target is not None and self.target.environment_id != self.environment_id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)

    async def confirm_deleted(self, inspect: Callable[[], Awaitable[object | None]]) -> None:
        """A delete acknowledgement alone does not establish terminal target absence."""
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                while await inspect() is not None:
                    await asyncio.sleep(_DELETE_POLL_SECONDS)
        except (TimeoutError, EnvironmentProviderError) as error:
            raise failure(
                self.provider_key,
                "provider_delete_unconfirmed",
                Category.UNKNOWN_OUTCOME,
                certainty=Certainty.UNKNOWN,
            ) from error

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        return await self.status()

    async def close(self) -> None:
        await self.close_transport()


def native_management_definition[C: BaseModel, K: BaseModel, E: CommandConfiguration](
    execution: EnvironmentProviderDefinition[C, K, E, NativeRuntime],
    *,
    management_type: Callable[[E, str, EnvironmentState | None, NativeRuntime, str], ManagementBackend],
    supports_stop: bool,
    requires_keepalive: bool,
) -> EnvironmentManagementDefinition[C, K, E, NativeRuntime]:
    async def runtime(*, configuration: C, credential: K | None) -> NativeRuntime:
        if credential is None:
            raise ValueError(f"the {execution.type!r} Environment Provider requires a credential")
        return NativeRuntime(configuration, credential)

    def management(
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ) -> ManagementBackend:
        if runtime is None:
            raise TypeError("Native management requires a runtime")
        return management_type(configuration, environment_id, state, runtime, operation_id)

    factory = ManagementBackendFactory(
        key=execution.type,
        environment_model=execution.environment_model,
        management=management,
        runtime_factory=runtime,
    )
    return management_definition(
        execution,
        environment_model=execution.environment_model,
        provider_factory=factory.provider,
        supports_stop=supports_stop,
        supports_destroy=True,
        requires_keepalive=requires_keepalive,
    )
