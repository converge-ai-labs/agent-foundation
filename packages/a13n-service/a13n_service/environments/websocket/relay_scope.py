"""Worker lease and response authority for one Attempt/binding use."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from .authority import DispatchAuthority, DispatchDenied, UseIdentity
from .coordination import ConfirmedObservation
from .relay_storage import ConnectionRelayStore
from .relay_waiters import RelayResponseDispatcher


class RelayUseScope:
    def __init__(
        self,
        identity: UseIdentity,
        observation: ConfirmedObservation,
        store: ConnectionRelayStore,
        responses: RelayResponseDispatcher,
        *,
        check_authority: Callable[[], None],
    ) -> None:
        if (
            observation.value.use_grant(identity) is None
            or observation.value.status != "online"
            or observation.value.connection != identity.connection
            or store.connection != identity.connection
        ):
            raise ValueError("Relay scope requires the exact confirmed use and connection")
        self.identity = identity
        self.authority = DispatchAuthority(identity, observation.deadline(use=self.identity))
        self.store = store
        self.responses = responses
        self._check_authority = check_authority
        self.server_ms = observation.value.now_ms
        self.received_at = monotonic()
        self.require_current()

    @property
    def available(self) -> bool:
        try:
            self.require_current()
            return True
        except DispatchDenied:
            return False

    def require_current(self) -> None:
        self._check_authority()
        self.authority.check(self.identity)

    async def renew(self, observation: ConfirmedObservation) -> None:
        if (
            observation.value.use_grant(self.identity) is None
            or observation.value.status != "online"
            or observation.value.connection != self.identity.connection
        ):
            await self.invalidate()
            raise DispatchDenied("Relay use was revoked")
        self.authority.renew(self.identity, observation.deadline(use=self.identity))
        # Receipt time maps to an earlier server time, conservatively shortening
        # request deadlines. Delayed responses never extend dispatch authority.
        self.server_ms = observation.value.now_ms
        self.received_at = monotonic()

    def fence(self) -> None:
        self.responses.fence_use(self.identity)
        self.authority.invalidate()

    async def invalidate(self) -> None:
        self.fence()
        await self.authority.fence()
