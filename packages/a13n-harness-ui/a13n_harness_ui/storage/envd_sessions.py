"""One current Session binding per Thread and Host-selected Device target."""

from a13n_harness.environment.session_recovery import EnvdSessionState
from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdLaunchConfiguration
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from a13n_harness_ui.configuration.models import DeviceResource
from a13n_harness_ui.errors import StoreConflictError

from .database import DatabaseSessions, short_session, transaction
from .models import EnvdSessionBindingRecord

type SessionTarget = LocalEnvdLaunchConfiguration | DeviceResource


class SessionBindingHead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Configuration selection, independent of attachment owner_epoch.
    version: int = Field(ge=1)
    configuration_digest: str | None
    session: EnvdSessionState | None = None
    session_version: int | None = None
    session_target: SessionTarget | None = None


class EnvdSessionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, thread_id: str, device_key: str) -> SessionBindingHead | None:
        async with short_session(self._sessions) as session:
            row = await session.get(EnvdSessionBindingRecord, (thread_id, device_key))
            return None if row is None else SessionBindingHead.model_validate_json(row.head_json)

    async def list(self, thread_id: str) -> dict[str, SessionBindingHead]:
        async with short_session(self._sessions) as session:
            rows = await session.scalars(
                select(EnvdSessionBindingRecord).where(EnvdSessionBindingRecord.thread_id == thread_id)
            )
            return {row.device_key: SessionBindingHead.model_validate_json(row.head_json) for row in rows}

    async def select(
        self,
        thread_id: str,
        device_key: str,
        *,
        expected: SessionBindingHead | None,
        replacement: SessionBindingHead,
    ) -> None:
        """Compare the complete head; external Session I/O never owns this transaction."""
        await self.select_many(thread_id, {device_key: (expected, replacement)})

    async def select_many(
        self,
        thread_id: str,
        changes: dict[str, tuple[SessionBindingHead | None, SessionBindingHead]],
        *,
        complete: bool = False,
    ) -> None:
        """Compare and publish one selection atomically, including removed Device keys."""
        async with transaction(self._sessions) as session:
            rows = {
                row.device_key: row
                for row in await session.scalars(
                    select(EnvdSessionBindingRecord).where(EnvdSessionBindingRecord.thread_id == thread_id)
                )
            }
            if complete and rows.keys() - changes.keys():
                raise StoreConflictError("Session selection changed", code="envd_session_conflict")
            for device_key, (expected, replacement) in changes.items():
                row = rows.get(device_key)
                actual = None if row is None else SessionBindingHead.model_validate_json(row.head_json)
                if actual != expected:
                    raise StoreConflictError("Session binding changed", code="envd_session_conflict")
                if row is None:
                    session.add(
                        EnvdSessionBindingRecord(
                            thread_id=thread_id, device_key=device_key, head_json=replacement.model_dump_json()
                        )
                    )
                elif replacement != expected:
                    row.head_json = replacement.model_dump_json()
            await session.flush()
