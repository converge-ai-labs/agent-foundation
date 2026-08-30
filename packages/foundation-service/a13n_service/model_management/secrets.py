"""Authorized, fresh managed Secret resolution for model execution."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.iam.models import RoleBindingRecord, ServiceAccountRecord, UserRecord, WorkspaceRecord
from a13n_service.secret_management.crypto import SecretProtectionError, SecretProtector
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import short_session

from .domain import (
    InvokingUserSecretCredential,
    ModelCredential,
    NoCredential,
    WorkspaceSecretCredential,
)


class SecretResolutionError(ValueError):
    """A model credential cannot be resolved without revealing protected metadata."""


@dataclass(frozen=True, slots=True)
class _EncryptedValue:
    secret_id: str
    organization_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    key: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str


class DatabaseSecretValueResolver:
    """Resolve and decrypt an eligible Secret after closing the database session."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], protector: SecretProtector) -> None:
        self._sessions = sessions
        self._protector = protector

    async def resolve(
        self,
        *,
        principal: PrincipalRef,
        organization_id: str,
        workspace_id: str,
        credential: ModelCredential,
    ) -> str | None:
        if isinstance(credential, NoCredential):
            return None
        async with short_session(self._sessions) as session:
            await _require_current_principal_access(
                session,
                principal=principal,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            query = select(ManagedSecretRecord).where(
                ManagedSecretRecord.organization_id == organization_id,
                ManagedSecretRecord.workspace_id == workspace_id,
                ManagedSecretRecord.deleted_at.is_(None),
            )
            if isinstance(credential, WorkspaceSecretCredential):
                query = query.where(
                    ManagedSecretRecord.id == credential.secret_id,
                    ManagedSecretRecord.owner_type == "workspace",
                    ManagedSecretRecord.owner_id == workspace_id,
                )
            elif isinstance(credential, InvokingUserSecretCredential):
                if principal.principal_type is not PrincipalType.user:
                    raise SecretResolutionError("the selected model credential is unavailable")
                query = query.where(
                    ManagedSecretRecord.owner_type == "user",
                    ManagedSecretRecord.owner_id == principal.principal_id,
                    ManagedSecretRecord.key == credential.secret_key,
                )
            record = await session.scalar(query)
            if record is None or record.ciphertext is None or record.nonce is None or record.encryption_key_id is None:
                raise SecretResolutionError("the selected model credential is unavailable")
            encrypted = _EncryptedValue(
                secret_id=record.id,
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
        try:
            return self._protector.decrypt(
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                encryption_key_id=encrypted.encryption_key_id,
                secret_id=encrypted.secret_id,
                organization_id=encrypted.organization_id,
                workspace_id=encrypted.workspace_id,
                owner_type=encrypted.owner_type,
                owner_id=encrypted.owner_id,
                key=encrypted.key,
                version=encrypted.version,
            )
        except SecretProtectionError as error:
            raise SecretResolutionError("the selected model credential is unavailable") from error


async def _require_current_principal_access(
    session: AsyncSession,
    *,
    principal: PrincipalRef,
    organization_id: str,
    workspace_id: str,
) -> None:
    workspace_exists = await session.scalar(
        select(WorkspaceRecord.id).where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.organization_id == organization_id,
            WorkspaceRecord.deleted_at.is_(None),
        )
    )
    if workspace_exists is None:
        raise SecretResolutionError("the selected model credential is unavailable")
    if principal.principal_type is PrincipalType.service_account:
        active = await session.scalar(
            select(ServiceAccountRecord.id).where(
                ServiceAccountRecord.id == principal.principal_id,
                ServiceAccountRecord.organization_id == organization_id,
                ServiceAccountRecord.workspace_id == workspace_id,
                ServiceAccountRecord.status == "active",
                ServiceAccountRecord.deleted_at.is_(None),
            )
        )
        if active is None:
            raise SecretResolutionError("the selected model credential is unavailable")
        return
    active_user = await session.scalar(
        select(UserRecord.id).where(UserRecord.id == principal.principal_id, UserRecord.status == "active")
    )
    membership = await session.scalar(
        select(RoleBindingRecord.id).where(
            RoleBindingRecord.organization_id == organization_id,
            RoleBindingRecord.principal_type == "user",
            RoleBindingRecord.principal_id == principal.principal_id,
            or_(
                and_(
                    RoleBindingRecord.resource_type == "organization",
                    RoleBindingRecord.resource_id == organization_id,
                ),
                and_(
                    RoleBindingRecord.resource_type == "workspace",
                    RoleBindingRecord.resource_id == workspace_id,
                    RoleBindingRecord.workspace_id == workspace_id,
                ),
            ),
        )
    )
    if active_user is None or membership is None:
        raise SecretResolutionError("the selected model credential is unavailable")
