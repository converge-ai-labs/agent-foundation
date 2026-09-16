"""Durable selection of explicitly composed memory behavior."""

from collections.abc import Callable, Mapping
from typing import Protocol

from a13n_harness.errors import RunError
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.database.metadata import Base
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.errors import RunAcceptanceError
from a13n_service.interactions.ports.memory import PreparedMemory
from a13n_service.storage import short_session


class RunMemorySelectionRecord(Base):
    __tablename__ = "run_memory_selections"
    __table_args__ = (CheckConstraint("binding_schema_version >= 1", name="schema_version_positive"),)
    run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    behavior_key: Mapped[str] = mapped_column(String(72))
    binding_schema_version: Mapped[int] = mapped_column(Integer)


class MemoryBehavior(Protocol):
    key: str
    schema_version: int

    async def validate(self, session: AsyncSession, run_id: str) -> None: ...
    async def inherit(self, session: AsyncSession, source_run_id: str, run_id: str) -> None: ...
    async def prepare(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> PreparedMemory: ...


class MemoryBehaviors:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        default: MemoryBehavior,
        behaviors: tuple[MemoryBehavior, ...] = (),
    ) -> None:
        self.sessions = sessions
        self.default = default
        self.behaviors: Mapping[str, MemoryBehavior] = {b.key: b for b in (default, *behaviors)}
        if len(self.behaviors) != len(behaviors) + 1:
            raise ValueError("Duplicate memory behavior key")

    async def _selected(self, session: AsyncSession, run_id: str) -> MemoryBehavior:
        selection = await session.get(RunMemorySelectionRecord, run_id)
        if selection is None:
            raise RunAcceptanceError("memory_binding_missing", "Accepted memory selection is missing")
        behavior = self.behaviors.get(selection.behavior_key)
        if behavior is None or behavior.schema_version != selection.binding_schema_version:
            raise RunAcceptanceError("memory_binding_unavailable", "Accepted memory behavior is unavailable")
        await behavior.validate(session, run_id)
        return behavior

    async def validate(self, session: AsyncSession, run_id: str) -> None:
        await self._selected(session, run_id)

    async def finalize(self, session: AsyncSession, run: Run, *, source_run_id: str | None) -> None:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        if source_run_id is not None:
            if selection is not None:
                raise RunAcceptanceError("memory_binding_conflict", "Inherited memory selection cannot be replaced")
            behavior = await self._selected(session, source_run_id)
            await behavior.inherit(session, source_run_id, run.id)
            session.add(
                RunMemorySelectionRecord(
                    run_id=run.id, behavior_key=behavior.key, binding_schema_version=behavior.schema_version
                )
            )
        elif selection is None:
            session.add(
                RunMemorySelectionRecord(
                    run_id=run.id, behavior_key=self.default.key, binding_schema_version=self.default.schema_version
                )
            )
        await session.flush()
        await self.validate(session, run.id)

    async def prepare(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> PreparedMemory:
        try:
            async with short_session(self.sessions) as session:
                behavior = await self._selected(session, run.id)
            return await behavior.prepare(
                run=run, workspace_id=workspace_id, config=config, current_context=current_context
            )
        except RunAcceptanceError as error:
            raise RunError(str(error), code=error.code) from error
