"""Instance-private configuration, credential CAS and atomic state/input retention."""

import json

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.connector_models import (
    ConnectorConfiguration,
    ConnectorGroupName,
    ConnectorInput,
    ConnectorRule,
    ConnectorState,
    StateChange,
)
from a13n_harness_ui.connectors.models import SetupOffer
from a13n_harness_ui.errors import HarnessUiError

from .database import DatabaseSessions, short_session, transaction
from .models import BotInboxRecord, ConnectorConfigurationRecord, ConnectorSetupOfferRecord, ConnectorStateRecord

_RULES = TypeAdapter(tuple[ConnectorRule, ...])
_GROUP_NAME = TypeAdapter(ConnectorGroupName)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _configuration(row: ConnectorConfigurationRecord) -> ConnectorConfiguration:
    return ConnectorConfiguration(
        connector_id=row.connector_id,
        kind=row.kind,
        name=row.name,
        group_name=row.group_name,
        version=row.version,
        credential_version=row.credential_version,
        enabled=row.enabled,
        deliver_to_bot=row.deliver_to_bot,
        selected_events=tuple(json.loads(row.selected_events)),
        rules=_RULES.validate_json(row.rules),
        settings=json.loads(row.settings),
        credentials=json.loads(row.credentials),
    )


def _conflict() -> HarnessUiError:
    return HarnessUiError("Connector changed; reload before saving or submitting input.", code="connector_conflict")


async def enqueue_inputs(
    session: AsyncSession, connector_id: str, inputs: tuple[ConnectorInput, ...]
) -> tuple[str, ...]:
    """Retain plugin-decided inputs. Any rejection aborts the caller's transaction."""
    row = await session.get(ConnectorConfigurationRecord, connector_id)
    if row is None or row.deleted:
        raise HarnessUiError("Connector does not exist.", code="connector_missing")
    if not inputs:
        return ()
    if not row.enabled or not row.deliver_to_bot:
        raise HarnessUiError("Connector delivery is disabled.", code="connector_delivery_disabled")
    if len(inputs) > 32:
        raise HarnessUiError("At most 32 inputs can be retained per commit.", code="connector_input_too_large")
    retained: list[str] = []
    pending = await session.scalar(
        select(func.count()).select_from(BotInboxRecord).where(BotInboxRecord.connector_id == connector_id)
    )
    for item in inputs:
        if item.connector_id != connector_id:
            raise HarnessUiError("Connector input belongs to another instance.", code="connector_input_invalid")
        envelope = {**item.model_dump(mode="json"), "source": "connector"}
        if len(_json([envelope]).encode("utf-8")) > 60 * 1024:
            raise HarnessUiError(
                "Connector envelope exceeds the automatic batch limit.", code="connector_input_too_large"
            )
        previous = await session.scalar(
            select(BotInboxRecord).where(
                BotInboxRecord.connector_id == connector_id,
                BotInboxRecord.source_event_id == item.source_event_id,
            )
        )
        if previous is not None:
            retained.append(previous.input_id)
            continue
        if (pending or 0) >= 1024:
            raise HarnessUiError(
                "Connector inbox is full; retain the source cursor and retry later.", code="connector_inbox_full"
            )
        session.add(
            BotInboxRecord(
                input_id=item.input_id,
                source="connector",
                connector_id=connector_id,
                event_type=item.event_type,
                source_event_id=item.source_event_id,
                text=item.text,
                created_at=item.created_at,
            )
        )
        await session.flush()
        retained.append(item.input_id)
        pending = (pending or 0) + 1
    return tuple(retained)


class ConnectorRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def configure(
        self,
        configuration: ConnectorConfiguration,
        *,
        expected_version: int | None = None,
        credentials: dict[str, str] | None = None,
    ) -> ConnectorConfiguration:
        """Create or conditionally replace human configuration; credentials have their own CAS."""
        try:
            group_name = _GROUP_NAME.validate_python(configuration.group_name)
        except ValidationError as exc:
            raise HarnessUiError(
                "Group name must use lowercase letters, digits and single hyphens, start with a letter, "
                "and contain at most 32 characters.",
                code="connector_group_invalid",
            ) from exc
        async with transaction(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, configuration.connector_id)
            if row is not None and row.deleted:
                raise HarnessUiError(
                    "Deleted Connector IDs cannot be reused; create a new connection.",
                    code="connector_identity_retired",
                )
            if row is not None and row.kind != configuration.kind:
                raise HarnessUiError("Connector kind cannot change.", code="connector_kind_conflict")
            if (row.version if row else None) != expected_version:
                raise _conflict()
            if (
                row is not None
                and credentials is not None
                and row.credential_version != configuration.credential_version
            ):
                raise _conflict()
            duplicate = await session.scalar(
                select(ConnectorConfigurationRecord.connector_id).where(
                    ConnectorConfigurationRecord.group_name == group_name,
                    ConnectorConfigurationRecord.connector_id != configuration.connector_id,
                    ConnectorConfigurationRecord.deleted.is_(False),
                )
            )
            if duplicate is not None:
                raise HarnessUiError(
                    "Group name is already used by another connection.", code="connector_group_conflict"
                )
            values = dict(
                name=configuration.name,
                group_name=group_name,
                enabled=configuration.enabled,
                deliver_to_bot=configuration.deliver_to_bot,
                selected_events=_json(configuration.selected_events),
                settings=_json(configuration.settings),
                rules=_RULES.dump_json(configuration.rules).decode(),
                version=(row.version + 1 if row else 1),
            )
            if row is None:
                row = ConnectorConfigurationRecord(
                    connector_id=configuration.connector_id,
                    kind=configuration.kind,
                    credentials=_json(configuration.credentials if credentials is None else credentials),
                    credential_version=1,
                    deleted=False,
                    **values,
                )
                session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                if credentials is not None:
                    row.credentials = _json(credentials)
                    row.credential_version += 1
            await session.flush()
            return _configuration(row)

    async def configuration(self, connector_id: str) -> ConnectorConfiguration:
        """Private runtime read. Never serialize settings/credentials into a public projection."""
        async with short_session(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, connector_id)
            if row is None or row.deleted:
                raise HarnessUiError("Connector does not exist.", code="connector_missing")
            return _configuration(row)

    async def configurations(self) -> tuple[ConnectorConfiguration, ...]:
        async with short_session(self._sessions) as session:
            return tuple(
                _configuration(row)
                for row in await session.scalars(
                    select(ConnectorConfigurationRecord)
                    .where(ConnectorConfigurationRecord.deleted.is_(False))
                    .order_by(ConnectorConfigurationRecord.connector_id)
                )
            )

    async def save_credentials(self, connector_id: str, *, expected_version: int, credentials: dict[str, str]) -> int:
        async with transaction(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, connector_id)
            if row is None or row.deleted or row.credential_version != expected_version:
                raise _conflict()
            row.credentials = _json(credentials)
            row.credential_version += 1
            return row.credential_version

    async def state(self, connector_id: str, key: str) -> ConnectorState | None:
        async with short_session(self._sessions) as session:
            row = await session.get(ConnectorStateRecord, (connector_id, key))
            return (
                None
                if row is None or row.deleted
                else ConnectorState(key=key, version=row.version, value=json.loads(row.value))
            )

    async def commit(
        self,
        connector_id: str,
        *,
        configuration_version: int,
        state_changes: tuple[StateChange, ...] = (),
        bot_inputs: tuple[ConnectorInput, ...] = (),
    ) -> tuple[str, ...]:
        if len(state_changes) > 32 or len({item.key for item in state_changes}) != len(state_changes):
            raise HarnessUiError("Provide at most 32 distinct state changes.", code="connector_state_invalid")
        values = [_json(item.value) for item in state_changes]
        sizes = [len(value.encode("utf-8")) for value in values]
        if any(size > 64 * 1024 for size in sizes) or sum(sizes) > 256 * 1024:
            raise HarnessUiError(
                "Connector state exceeds the document or commit limit.", code="connector_state_too_large"
            )
        async with transaction(self._sessions) as session:
            configuration = await session.get(ConnectorConfigurationRecord, connector_id)
            if configuration is None or configuration.version != configuration_version or not configuration.enabled:
                raise _conflict()
            for change, value in zip(state_changes, values, strict=True):
                row = await session.get(ConnectorStateRecord, (connector_id, change.key))
                if (row.version if row is not None and not row.deleted else None) != change.expected_version:
                    raise _conflict()
                if row is None:
                    if not change.delete:
                        session.add(
                            ConnectorStateRecord(
                                connector_id=connector_id, key=change.key, version=1, value=value, deleted=False
                            )
                        )
                else:
                    # Keep a tombstone so delete/recreate cannot reuse an old CAS version.
                    row.value, row.version, row.deleted = (
                        ("null" if change.delete else value),
                        row.version + 1,
                        change.delete,
                    )
            return await enqueue_inputs(session, connector_id, bot_inputs)

    async def retain(self, connector_id: str, inputs: tuple[ConnectorInput, ...]) -> tuple[str, ...]:
        async with transaction(self._sessions) as session:
            return await enqueue_inputs(session, connector_id, inputs)

    async def offer(self, offer: SetupOffer) -> SetupOffer:
        async with transaction(self._sessions) as session:
            await session.execute(
                insert(ConnectorSetupOfferRecord)
                .values(
                    offer_id=offer.offer_id,
                    thread_id=offer.thread_id,
                    payload=offer.model_dump_json(),
                    created_at=offer.created_at,
                )
                .on_conflict_do_nothing()
            )
            row = await session.get(ConnectorSetupOfferRecord, offer.offer_id)
            assert row is not None
            return SetupOffer.model_validate_json(row.payload)

    async def offers(self, thread_id: str) -> tuple[SetupOffer, ...]:
        async with short_session(self._sessions) as session:
            return tuple(
                SetupOffer.model_validate_json(row.payload)
                for row in await session.scalars(
                    select(ConnectorSetupOfferRecord)
                    .where(ConnectorSetupOfferRecord.thread_id == thread_id)
                    .order_by(ConnectorSetupOfferRecord.created_at)
                )
            )

    async def delete(self, connector_id: str, *, expected_version: int) -> None:
        async with transaction(self._sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, connector_id)
            if row is None or row.deleted or row.version != expected_version:
                raise _conflict()
            # Retire identity permanently: old captures must never resolve a new
            # account's credentials at an old endpoint after delete/recreate.
            row.deleted, row.enabled, row.deliver_to_bot = True, False, False
            row.name, row.settings, row.credentials, row.rules, row.selected_events = "", "{}", "{}", "[]", "[]"
            row.version += 1
            row.credential_version += 1
            await session.execute(delete(ConnectorStateRecord).where(ConnectorStateRecord.connector_id == connector_id))
            await session.execute(delete(BotInboxRecord).where(BotInboxRecord.connector_id == connector_id))
