"""TCP readiness observed from inside the Guest sandbox."""

import asyncio
import math
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from .commands import PortObservation, PortTarget
from .models import EnvironmentError


class PortCommands(Protocol):
    async def port(self, port: int) -> dict[str, JsonValue]: ...


class GuestPorts:
    def __init__(self, commands: PortCommands) -> None:
        self.commands = commands

    async def inspect(self, target: PortTarget) -> PortObservation:
        if target.address != "loopback":
            raise EnvironmentError("Guest port inspection requires a loopback port.", code="environment_unsupported")
        result = await self.commands.port(target.port)
        return PortObservation(
            target=target, observed_at=datetime.now(UTC), status="listening" if result["listening"] else "not_listening"
        )

    async def wait(
        self, target: PortTarget, *, desired: Literal["listening", "not_listening"], timeout_seconds: float
    ) -> PortObservation:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise EnvironmentError("Invalid Guest port wait.", code="environment_request_invalid")
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    result = await self.inspect(target)
                    if result.status == desired:
                        return result
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise EnvironmentError("Guest port wait timed out.", code="environment_timeout") from None
