"""Internal Secret lifecycle with exact owner and generation binding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction

from .crypto import EncryptedSecret, SecretProtectionError, SecretProtector
from .domain import SecretOperation, SecretOwnerType, SecretUseContext
from .models import SecretRecord


class InternalSecretError(ValueError):
    """A protected internal Secret is unavailable or changed."""


@dataclass(frozen=True, slots=True)
class SecretValueRef:
    secret_id: str
    version: int


@dataclass(frozen=True, slots=True)
class _EncryptedValue:
    secret_id: str
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str


class InternalSecretService:
    """Create, replace, resolve, and tombstone non-public owner credentials."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._protector = protector
        self._clock = clock

    async def create(self, context: SecretUseContext, value: str) -> SecretValueRef:
        if context.credential_generation != 1:
            raise InternalSecretError("new Secret generation must be one")
        _validate_owner(context)
        secret_id = new_object_id("sec")
        protected = self._protect(secret_id, context, value)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                if await session.scalar(self._owner_key_query(context).with_only_columns(SecretRecord.id)) is not None:
                    raise InternalSecretError("a Secret already exists for this owner and key")
                session.add(
                    SecretRecord(
                        id=secret_id,
                        organization_id=context.organization_id,
                        workspace_id=context.workspace_id,
                        owner_type=context.owner_type.value,
                        owner_id=context.owner_id,
                        key=context.key,
                        version=1,
                        ciphertext=protected.ciphertext,
                        nonce=protected.nonce,
                        encryption_key_id=protected.encryption_key_id,
                        created_at=now,
                        value_updated_at=now,
                        deleted_at=None,
                    )
                )
        except IntegrityError as error:
            raise InternalSecretError("an active Secret already exists for this owner and key") from error
        return SecretValueRef(secret_id=secret_id, version=1)

    async def replace(self, context: SecretUseContext, value: str) -> SecretValueRef:
        _validate_owner(context)
        async with transaction(self._sessions) as session:
            record = await self._load(session, context, lock=True)
            next_version = record.version + 1
            next_context = context.model_copy(update={"credential_generation": next_version})
            protected = self._protect(record.id, next_context, value)
            record.version = next_version
            record.ciphertext = protected.ciphertext
            record.nonce = protected.nonce
            record.encryption_key_id = protected.encryption_key_id
            record.value_updated_at = self._clock()
        return SecretValueRef(secret_id=record.id, version=next_version)

    async def resolve(self, context: SecretUseContext) -> str:
        _validate_owner(context)
        if context.operation is SecretOperation.management:
            raise InternalSecretError("management operations cannot read Secret values")
        async with short_session(self._sessions) as session:
            record = await self._load(session, context, lock=False)
            if record.ciphertext is None or record.nonce is None or record.encryption_key_id is None:
                raise InternalSecretError("the internal Secret is unavailable")
            encrypted = _EncryptedValue(
                secret_id=record.id,
                ciphertext=bytes(record.ciphertext),
                nonce=bytes(record.nonce),
                encryption_key_id=record.encryption_key_id,
            )
        try:
            return self._protector.decrypt(
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                encryption_key_id=encrypted.encryption_key_id,
                secret_id=encrypted.secret_id,
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
                owner_type=context.owner_type.value,
                owner_id=context.owner_id,
                key=context.key,
                version=context.credential_generation,
            )
        except SecretProtectionError as error:
            raise InternalSecretError("the internal Secret is unavailable") from error

    async def tombstone(self, context: SecretUseContext) -> SecretValueRef:
        _validate_owner(context)
        async with transaction(self._sessions) as session:
            record = await self._load(session, context, lock=True)
            record.ciphertext = None
            record.nonce = None
            record.encryption_key_id = None
            record.deleted_at = self._clock()
        return SecretValueRef(secret_id=record.id, version=record.version)

    async def _load(self, session: AsyncSession, context: SecretUseContext, *, lock: bool) -> SecretRecord:
        query = self._owner_key_query(context).where(
            SecretRecord.version == context.credential_generation,
            SecretRecord.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update()
        record = await session.scalar(query)
        if record is None:
            raise InternalSecretError("the internal Secret is unavailable")
        return record

    @staticmethod
    def _owner_key_query(context: SecretUseContext):
        return select(SecretRecord).where(
            SecretRecord.organization_id == context.organization_id,
            SecretRecord.workspace_id == context.workspace_id,
            SecretRecord.owner_type == context.owner_type.value,
            SecretRecord.owner_id == context.owner_id,
            SecretRecord.key == context.key,
        )

    def _protect(self, secret_id: str, context: SecretUseContext, value: str) -> EncryptedSecret:
        return self._protector.encrypt(
            value,
            secret_id=secret_id,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            owner_type=context.owner_type.value,
            owner_id=context.owner_id,
            key=context.key,
            version=context.credential_generation,
        )


def _validate_owner(context: SecretUseContext) -> None:
    if context.owner_type is SecretOwnerType.workspace and context.owner_id != context.workspace_id:
        raise InternalSecretError("Workspace Secret owner must match its Workspace")
