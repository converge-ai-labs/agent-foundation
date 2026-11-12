"""Connector configuration and transaction-scoped automatic inbox ingestion.

Concrete Connectors own fixed domain tables. They can call enqueue_inputs within the same
short transaction that saves their own cursor/state; neither operation performs external I/O.
"""

import json

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorInput
from a13n_harness_ui.errors import HarnessUiError

from .database import DatabaseSessions, short_session, transaction
from .models import BotInboxRecord, ConnectorConfigurationRecord


async def enqueue_inputs(session: AsyncSession, connector_id: str, inputs: tuple[ConnectorInput, ...]) -> None:
    """Stage selected events; the caller owns the source-state transaction and commit."""
    row = await session.get(ConnectorConfigurationRecord, connector_id)
    if row is None:
        raise HarnessUiError("Connector does not exist.", code="connector_missing")
    selected = set(json.loads(row.selected_events)) if row.enabled and row.deliver_to_bot else set()
    for item in inputs:
        if item.connector_id != connector_id:
            raise HarnessUiError("Connector input belongs to another instance.", code="connector_input_invalid")
        if item.event_type not in selected:
            continue
        envelope = {**item.model_dump(mode="json"), "source": "connector"}
        if len(json.dumps([envelope], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 60 * 1024:
            raise HarnessUiError(
                "Connector envelope exceeds the automatic batch limit.", code="connector_input_too_large"
            )
        await session.execute(
            insert(BotInboxRecord)
            .values(
                input_id=item.input_id,
                source="connector",
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
                    deliver_to_bot=configuration.deliver_to_bot,
                    selected_events=json.dumps(configuration.selected_events),
                    settings=json.dumps(configuration.settings, allow_nan=False),
                    credentials=json.dumps(configuration.credentials, allow_nan=False),
                )
                .on_conflict_do_update(
                    index_elements=["connector_id"],
                    set_={
                        "enabled": configuration.enabled,
                        "deliver_to_bot": configuration.deliver_to_bot,
                        "selected_events": json.dumps(configuration.selected_events),
                        "settings": json.dumps(configuration.settings, allow_nan=False),
                        "credentials": json.dumps(configuration.credentials, allow_nan=False),
                    },
                )
            )
            # Explicitly deselected types cannot linger ahead of selected input in a batch.
            await session.execute(
                delete(BotInboxRecord).where(
                    BotInboxRecord.connector_id == configuration.connector_id,
                    BotInboxRecord.event_type.not_in(configuration.selected_events),
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
                deliver_to_bot=row.deliver_to_bot,
                selected_events=tuple(json.loads(row.selected_events)),
                settings=json.loads(row.settings),
                credentials=json.loads(row.credentials),
            )

    async def retain(self, connector_id: str, inputs: tuple[ConnectorInput, ...]) -> None:
        """Ingress without a cursor update (for example an already authenticated webhook)."""
        async with transaction(self._sessions) as session:
            await enqueue_inputs(session, connector_id, inputs)
