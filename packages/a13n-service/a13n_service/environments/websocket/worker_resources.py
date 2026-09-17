"""Short persisted use admission, separate from Control connection observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from a13n_environment import EnvironmentAction, EnvironmentState
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_environment.remote_envd.environment import decode_state
from a13n_harness import EnvironmentAccess
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from ..capacity import CapacityLimits
from ..run_use import lock_run_environment_use, mark_run_environment_use

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext


@dataclass(frozen=True, slots=True)
class ClientUseTarget:
    state: EnvironmentState
    generation: int
    access: str
    permissions: frozenset[EnvironmentAction]


class ClientUseResources:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], capacity: CapacityLimits) -> None:
        self._sessions, self._capacity = sessions, capacity

    async def admit(self, attempt: AttemptContext, environment_id: str) -> ClientUseTarget:
        now = utc_now()
        attempt.lease.require_current(now)
        attempt.authorization.require_environment(environment_id)
        async with transaction(self._sessions) as session:
            run, row, provider = await lock_run_environment_use(session, environment_id, attempt, self._capacity, now)
            if (
                provider.type != WEBSOCKET_PROVIDER_KEY
                or row.ownership != "external"
                or row.status == "deleted"
                or row.state is None
                or run.environment_access is None
            ):
                raise ValueError("The Run has no eligible client Environment selection")
            state = EnvironmentState.model_validate(row.state)
            decode_state(WEBSOCKET_PROVIDER_KEY, state)
            await self._capacity.admit(session, row)
            mark_run_environment_use(run, row, now)
            return ClientUseTarget(
                state=state,
                generation=row.generation,
                access=run.environment_access,
                permissions=(
                    EnvironmentAccess(run.environment_access).permission_set().operations
                    & EnvironmentAccess(row.access).permission_set().operations
                ),
            )
