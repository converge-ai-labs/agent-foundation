"""Control-owned client ingress and observation maintenance."""

from dataclasses import dataclass

from .connection_host import ClientConnectionHost
from .reconciliation import ClientConnectionReconciler
from .service import ClientConnectionService


@dataclass(frozen=True, slots=True)
class ClientConnectionRuntime:
    service: ClientConnectionService
    host: ClientConnectionHost
    reconciler: ClientConnectionReconciler

    def begin_drain(self) -> None:
        self.host.begin_drain()
        self.reconciler.drain()

    async def close(self) -> None:
        self.begin_drain()
        await self.host.close()
