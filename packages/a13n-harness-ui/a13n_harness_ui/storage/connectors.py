"""Shared Connector configuration and selected-input inbox over the Host database.

Concrete Connectors own fixed domain tables. They can call enqueue_inputs within the same
short transaction that saves their own cursor/state; neither operation performs external I/O.
"""

import json

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorInput
from a13n_harness_ui.errors import HarnessUiError

from .database import DatabaseSessions, short_session, transaction
from .models import ConnectorConfigurationRecord, ConnectorInputRecord


async def enqueue_inputs(session: AsyncSession, connector_id: str, inputs: tuple[ConnectorInput, ...]) -> None:
    """Stage selected events; the caller owns the source-state transaction and commit."""
    row = await session.get(ConnectorConfigurationRecord, connector_id)
    if row is None:
        raise HarnessUiError("Connector does not exist.", code="connector_missing")
    selected = set(json.loads(row.selected_events)) if row.enabled else set()
    for item in inputs:
        if item.connector_id != connector_id:
            raise HarnessUiError("Connector input belongs to another instance.", code="connector_input_invalid")
        if item.event_type not in selected:
            continue
        await session.execute(
            insert(ConnectorInputRecord)
            .values(
                input_id=item.input_id,
                connector_id=connector_id,
                event_type=item.event_type,
                source_event_id=item.source_event_id,
                text=item.text,
                created_at=item.created_at,
            )
            .on_conflict_do_nothing(index_elements=["connector_id", "source_event_id"])
        )


class ConnectorRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def configure(self, configuration: ConnectorConfiguration) -> None:
        """Store plaintext credentials privately, outside captured configuration generations."""
        async with transaction(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, configuration.connector_id)
            if row is not None and row.kind != configuration.kind:
                raise HarnessUiError("Connector kind cannot change.", code="connector_kind_conflict")
            await session.execute(
                insert(ConnectorConfigurationRecord)
                .values(
                    connector_id=configuration.connector_id,
                    kind=configuration.kind,
                    enabled=configuration.enabled,
                    selected_events=json.dumps(configuration.selected_events),
                    settings=json.dumps(configuration.settings, allow_nan=False),
                    credentials=json.dumps(configuration.credentials, allow_nan=False),
                )
                .on_conflict_do_update(
                    index_elements=["connector_id"],
                    set_={
                        "enabled": configuration.enabled,
                        "selected_events": json.dumps(configuration.selected_events),
                        "settings": json.dumps(configuration.settings, allow_nan=False),
                        "credentials": json.dumps(configuration.credentials, allow_nan=False),
                    },
                )
            )
            # Explicitly deselected types cannot linger ahead of selected input in a batch.
            await session.execute(
                delete(ConnectorInputRecord).where(
                    ConnectorInputRecord.connector_id == configuration.connector_id,
                    ConnectorInputRecord.event_type.not_in(configuration.selected_events),
                )
            )

    async def configuration(self, connector_id: str) -> ConnectorConfiguration:
        """Private runtime read. Do not return this object's fields as tool or API settings."""
        async with short_session(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, connector_id)
            if row is None:
                raise HarnessUiError("Connector does not exist.", code="connector_missing")
            return ConnectorConfiguration(
                connector_id=row.connector_id,
                kind=row.kind,
                enabled=row.enabled,
                selected_events=tuple(json.loads(row.selected_events)),
                settings=json.loads(row.settings),
                credentials=json.loads(row.credentials),
            )

    async def retain(self, connector_id: str, inputs: tuple[ConnectorInput, ...]) -> None:
        """Ingress without a cursor update (for example an already authenticated webhook)."""
        async with transaction(self._sessions) as session:
            await enqueue_inputs(session, connector_id, inputs)

    async def pending(self) -> tuple[ConnectorInput, ...]:
        """One cross-Connector batch, at most 32 events and 60 KiB of serialized envelopes."""
        async with short_session(self._sessions) as session:
            rows = (
                await session.scalars(
                    select(ConnectorInputRecord)
                    .join(ConnectorConfigurationRecord)
                    .where(ConnectorConfigurationRecord.enabled.is_(True))
                    .order_by(ConnectorInputRecord.created_at, ConnectorInputRecord.input_id)
                    .limit(32)
                )
            ).all()
            result: list[ConnectorInput] = []
            size = 0
            for row in rows:
                item = ConnectorInput(
                    input_id=row.input_id,
                    connector_id=row.connector_id,
                    event_type=row.event_type,
                    source_event_id=row.source_event_id,
                    text=row.text,
                    created_at=row.created_at,
                )
                size += len(item.model_dump_json().encode("utf-8"))
                if result and size > 60 * 1024:
                    break
                result.append(item)
            return tuple(result)

    async def delivered(self, inputs: tuple[ConnectorInput, ...]) -> None:
        """Forget exactly the accepted batch, preserving arrivals during delivery."""
        if not inputs:
            return
        async with transaction(self._sessions) as session:
            await session.execute(
                delete(ConnectorInputRecord).where(ConnectorInputRecord.input_id.in_(item.input_id for item in inputs))
            )
