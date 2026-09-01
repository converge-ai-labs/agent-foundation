"""Managed Secret adapter for Connection- and Trigger-owned credentials."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session

from .errors import ConnectorError
from .provider import ConnectorProviderSecret


@dataclass(frozen=True, slots=True)
class _EncryptedSecretSnapshot:
    id: str
    organization_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    key: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str


class DatabaseConnectorSecretStore:
    """Persist Connector credentials through the canonical managed Secret table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], protector: SecretProtector) -> None:
        self._sessions = sessions
        self._protector = protector

    async def replace_connection_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        secrets: Sequence[ConnectorProviderSecret],
        actor: PrincipalRef,
    ) -> None:
        del actor
        await self._replace(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="connection",
            owner_id=connection_id,
            secrets=secrets,
        )

    async def read_connection_secrets(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
    ) -> tuple[ConnectorProviderSecret, ...]:
        return await self._read(
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="connection",
            owner_id=connection_id,
        )

    async def delete_connection_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        actor: PrincipalRef,
    ) -> None:
        del actor
        await self._delete(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="connection",
            owner_id=connection_id,
        )

    async def replace_trigger_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        secrets: Sequence[ConnectorProviderSecret],
        actor: PrincipalRef,
    ) -> None:
        del actor
        await self._replace(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="trigger",
            owner_id=trigger_id,
            secrets=secrets,
        )

    async def read_trigger_secrets(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
    ) -> tuple[ConnectorProviderSecret, ...]:
        return await self._read(
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="trigger",
            owner_id=trigger_id,
        )

    async def delete_trigger_secrets(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        trigger_id: str,
        actor: PrincipalRef,
    ) -> None:
        del actor
        await self._delete(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            owner_type="trigger",
            owner_id=trigger_id,
        )

    async def _replace(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        owner_type: str,
        owner_id: str,
        secrets: Sequence[ConnectorProviderSecret],
    ) -> None:
        supplied = tuple(secrets)
        if len({item.key for item in supplied}) != len(supplied):
            raise ConnectorError("Connector Provider returned duplicate Secret keys.", code="connection_incompatible")
        records = tuple(
            (
                await session.scalars(
                    select(SecretRecord)
                    .where(
                        SecretRecord.organization_id == organization_id,
                        SecretRecord.workspace_id == workspace_id,
                        SecretRecord.owner_type == owner_type,
                        SecretRecord.owner_id == owner_id,
                        SecretRecord.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        existing = {record.key: record for record in records}
        now = datetime.now(UTC)
        for item in supplied:
            record = existing.pop(item.key, None)
            if record is None:
                record = SecretRecord(
                    id=new_object_id("sec"),
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    key=item.key,
                    version=1,
                    ciphertext=None,
                    nonce=None,
                    encryption_key_id=None,
                    created_at=now,
                    value_updated_at=now,
                    deleted_at=None,
                )
                session.add(record)
            else:
                record.version += 1
                record.value_updated_at = now
            protected = self._protector.encrypt(
                item.value.get_secret_value(),
                secret_id=record.id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                owner_type=owner_type,
                owner_id=owner_id,
                key=item.key,
                version=record.version,
            )
            record.ciphertext = protected.ciphertext
            record.nonce = protected.nonce
            record.encryption_key_id = protected.encryption_key_id
        for record in existing.values():
            record.version += 1
            record.ciphertext = None
            record.nonce = None
            record.encryption_key_id = None
            record.value_updated_at = now
            record.deleted_at = now

    async def _read(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        owner_type: str,
        owner_id: str,
    ) -> tuple[ConnectorProviderSecret, ...]:
        async with short_session(self._sessions) as session:
            records = tuple(
                (
                    await session.scalars(
                        select(SecretRecord)
                        .where(
                            SecretRecord.organization_id == organization_id,
                            SecretRecord.workspace_id == workspace_id,
                            SecretRecord.owner_type == owner_type,
                            SecretRecord.owner_id == owner_id,
                            SecretRecord.deleted_at.is_(None),
                        )
                        .order_by(SecretRecord.key)
                    )
                ).all()
            )
            snapshots = tuple(_snapshot(record) for record in records)
        try:
            return tuple(
                ConnectorProviderSecret(
                    key=item.key,
                    value=SecretStr(
                        self._protector.decrypt(
                            ciphertext=item.ciphertext,
                            nonce=item.nonce,
                            encryption_key_id=item.encryption_key_id,
                            secret_id=item.id,
                            organization_id=item.organization_id,
                            workspace_id=item.workspace_id,
                            owner_type=item.owner_type,
                            owner_id=item.owner_id,
                            key=item.key,
                            version=item.version,
                        )
                    ),
                )
                for item in snapshots
            )
        except SecretProtectionError as error:
            raise ConnectorError(
                "Connector credential material is unavailable.", code="connection_incompatible"
            ) from error

    async def _delete(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        owner_type: str,
        owner_id: str,
    ) -> None:
        records = tuple(
            (
                await session.scalars(
                    select(SecretRecord)
                    .where(
                        SecretRecord.organization_id == organization_id,
                        SecretRecord.workspace_id == workspace_id,
                        SecretRecord.owner_type == owner_type,
                        SecretRecord.owner_id == owner_id,
                        SecretRecord.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(UTC)
        for record in records:
            record.version += 1
            record.ciphertext = None
            record.nonce = None
            record.encryption_key_id = None
            record.value_updated_at = now
            record.deleted_at = now


def _snapshot(record: SecretRecord) -> _EncryptedSecretSnapshot:
    if record.ciphertext is None or record.nonce is None or record.encryption_key_id is None:
        raise ConnectorError("Connector credential material is unavailable.", code="connection_incompatible")
    return _EncryptedSecretSnapshot(
        id=record.id,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        owner_type=record.owner_type,
        owner_id=record.owner_id,
        key=record.key,
        version=record.version,
        ciphertext=bytes(record.ciphertext),
        nonce=bytes(record.nonce),
        encryption_key_id=record.encryption_key_id,
    )
