"""Shared explicit CodeAct values; snapshots initialize once and never overwrite late writes."""

from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.state import CodeActStateOwner, CodeActStoredValues, StateKey, validate_stored_values
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra.db import Storage, lock, short_session, transaction
from a13n_service.runs.tables import ThreadCodeActRow


class SharedCodeActState(CodeActStateOwner):
    def __init__(self, storage: Storage, thread_id: str) -> None:
        super().__init__()
        self.storage = storage
        self.thread_id = thread_id

    async def initialize(self, thread_id: str, state: CodeActStoredValues) -> None:
        if thread_id != self.thread_id:
            raise ValueError("CodeAct state owner belongs to another Thread")
        async with transaction(self.storage) as session:
            await session.execute(
                insert(ThreadCodeActRow)
                .values(thread_id=thread_id, values=state.values)
                .on_conflict_do_nothing(index_elements=[ThreadCodeActRow.thread_id])
            )

    async def snapshot(self) -> CodeActStoredValues:
        async with short_session(self.storage) as session:
            row = await session.get_one(ThreadCodeActRow, self.thread_id)
            return CodeActStoredValues(values=row.values)

    async def store(self, key: StateKey, value: JsonValue, config: CodeActConfig) -> None:
        async with transaction(self.storage) as session:
            row = await lock(session, ThreadCodeActRow, self.thread_id)
            if row is None:
                raise RuntimeError("CodeAct state has not been initialized")
            candidate = CodeActStoredValues(values={**row.values, key: value})
            validate_stored_values(candidate, config)
            row.values = candidate.values

    async def forget(self, key: StateKey) -> bool:
        async with transaction(self.storage) as session:
            row = await lock(session, ThreadCodeActRow, self.thread_id)
            if row is None or key not in row.values:
                return False
            row.values = {name: value for name, value in row.values.items() if name != key}
            return True
