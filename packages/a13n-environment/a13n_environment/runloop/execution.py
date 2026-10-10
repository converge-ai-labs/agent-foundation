"""Runloop execution implementation."""

import shlex

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..native.configuration import TargetState
from ..native.environment import NativeExecution
from ..native.errors import failure
from ..native.http import decode_response
from .shared import Execution, RunloopEnvironmentConfiguration, RunloopReference


class RunloopExecution(RunloopReference, NativeExecution[RunloopEnvironmentConfiguration, TargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.status != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        await self.open_operations(self.execute, target.id, execution_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target
        result = decode_response(
            Execution,
            await self.transport().request(
                "POST", f"/v1/devboxes/{self.target.target_id}/execute_sync", body={"command": shlex.join(argv)}
            ),
            self.provider_key,
            mutation=True,
        )
        if result.exit_status != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return result.stdout
