"""Vercel execution implementation."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.configuration import NamedTargetState
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.shared import (
    SandboxResponse,
    VercelEnvironmentConfiguration,
    VercelReference,
)


class VercelExecution(VercelReference, NativeExecution[VercelEnvironmentConfiguration, NamedTargetState]):
    def accept(self, response: SandboxResponse) -> None:
        if self.commands is not None and self.session is not None and self.session.id != response.session.id:
            raise failure(self.provider_key, "provider_execution_replaced", Category.CONFLICT)
        super().accept(response)

    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        response = await self.lookup()
        if response is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if response.session.status != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        identity = f"{response.sandbox.name}:{response.sandbox.createdAt}"
        await self.open_operations(self.execute, identity, execution_id, incarnation=response.session.id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.session
        return await command(self, self.session.id, argv, timeout=timeout)
