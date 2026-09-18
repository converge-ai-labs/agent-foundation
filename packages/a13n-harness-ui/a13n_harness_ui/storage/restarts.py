"""One locally coordinated planned restart, with a single atomic startup claim."""

from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.restart_models import RestartBatch

from .database import DatabaseSessions, short_session, transaction
from .models import PlannedRestartRecord


class RestartRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self) -> RestartBatch | None:
        async with short_session(self._sessions) as session:
            record = await session.get(PlannedRestartRecord, 1)
            return None if record is None else RestartBatch.model_validate_json(record.payload)

    async def publish(self, batch: RestartBatch) -> None:
        async with transaction(self._sessions) as session:
            previous = await session.get(PlannedRestartRecord, 1)
            if previous is not None:
                previous.payload = batch.model_dump_json()
            else:
                session.add(PlannedRestartRecord(singleton_id=1, payload=batch.model_dump_json()))

    async def replace(self, expected: RestartBatch, replacement: RestartBatch) -> None:
        async with transaction(self._sessions) as session:
            record = await session.get(PlannedRestartRecord, 1)
            if record is None or RestartBatch.model_validate_json(record.payload) != expected:
                raise RunCoordinationError("The update handoff changed.", code="restart_conflict")
            record.payload = replacement.model_dump_json()

    async def claim(self) -> RestartBatch | None:
        async with transaction(self._sessions) as session:
            record = await session.get(PlannedRestartRecord, 1)
            if record is None:
                return None
            batch = RestartBatch.model_validate_json(record.payload)
            if batch.state != "ready":
                return None
            claimed = batch.model_copy(update={"state": "consumed", "error": "Startup recovery did not finish."})
            record.payload = claimed.model_dump_json()
            return claimed
