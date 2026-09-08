"""Bind managed Skill preparation and materialization to the live Attempt lease."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext, read_attempt_lease
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .materialization import SkillMaterializationStale


@dataclass(frozen=True, slots=True)
class CurrentSkillAttempt:
    sessions: async_sessionmaker[AsyncSession]
    current_context: Callable[[], AttemptContext]
    clock: Clock = utc_now

    async def require_current(self) -> None:
        try:
            async with short_session(self.sessions) as session:
                await read_attempt_lease(session, self.current_context(), self.clock())
        except AttemptAuthorityError as error:
            raise SkillMaterializationStale("The Skill's owning Attempt is no longer current.") from error
