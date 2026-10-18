"""Daytona execution implementation."""

import math
import shlex
from dataclasses import replace

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentOperationFamily, EnvironmentState
from a13n_harness.providers.environment.daytona.computer import DaytonaComputer, with_computer
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaEnvironmentConfiguration,
    DaytonaReference,
    Execution,
)
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeExecution, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response


class DaytonaExecution(DaytonaReference, NativeExecution[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: DaytonaEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__(config, environment_id, state, runtime)

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        value = super().descriptor
        return with_computer(value) if self.config.desktop else value

    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.state != "started":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        # Organization policy may impose a hard TTL even when creation requests none.
        if target.autoDestroyAt is not None:
            raise failure(self.provider_key, "provider_expiry_unsupported", Category.UNSUPPORTED)
        await self.open_toolbox(target)
        await self.open_operations(self.execute, target.id, execution_id)
        if self.config.desktop:
            assert self.commands is not None and self.toolbox is not None
            computer = DaytonaComputer(self.commands, self.toolbox, target.id)
            await computer.describe()
            self._operations = replace(self._operations, computer=computer)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target and self.toolbox
        result = decode_response(
            Execution,
            await self.toolbox.request(
                "POST",
                f"{self.target.target_id}/process/execute",
                body={"command": shlex.join(argv), "timeout": math.ceil(timeout)},
            ),
            self.provider_key,
            mutation=True,
        )
        if result.exitCode != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return result.result

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        await super().check_ready(operations)
        if "computer" in operations and self._operations.computer is not None:
            await self._operations.computer.describe()

    async def close(self) -> None:
        computer = self._operations.computer
        if isinstance(computer, DaytonaComputer):
            await computer.close()
        await super().close()
