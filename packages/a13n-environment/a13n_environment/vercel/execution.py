"""Vercel execution implementation."""

import json
import math

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..native.configuration import NamedTargetState
from ..native.environment import NativeExecution
from ..native.errors import failure
from .shared import SandboxResponse, VercelEnvironmentConfiguration, VercelReference


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
        output: list[str] = []
        size = 0
        async with self.transport().stream(
            "POST",
            f"/v2/sandboxes/sessions/{self.session.id}/cmd",
            body={
                "command": argv[0],
                "args": argv[1:],
                "env": {},
                "sudo": False,
                "wait": True,
                "logs": True,
                "timeout": math.ceil(timeout * 1000),
            },
        ) as response:
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if "command" in event:
                    code = event["command"].get("exitCode")
                    if code is not None:
                        if type(code) is not int:
                            raise ValueError("Invalid native exit code")
                        if code != 0:
                            raise failure(
                                self.provider_key,
                                "provider_command_failed",
                                Category.PROVIDER_FAILURE,
                                certainty=Certainty.KNOWN,
                            )
                        return "".join(output)
                elif event.get("stream") == "stdout":
                    data = event["data"]
                    size += len(data.encode())
                    if size > 24 * 1024 * 1024:
                        raise failure(
                            self.provider_key,
                            "provider_response_invalid",
                            Category.UNKNOWN_OUTCOME,
                            certainty=Certainty.UNKNOWN,
                        )
                    output.append(data)
                elif event.get("stream") == "error":
                    break
        raise failure(
            self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        )
