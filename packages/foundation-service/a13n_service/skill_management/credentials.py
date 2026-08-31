"""Fresh Workspace Secret resolution for GitHub Skill acquisition."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.secret_management.crypto import SecretProtectionError, SecretProtector
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import short_session

from .errors import GitHubCredentialError


@dataclass(frozen=True, slots=True)
class _EncryptedSecret:
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


class DatabaseGitHubCredentialResolver:
    """Copy encrypted material under authorization, then decrypt after closing the session."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], protector: SecretProtector) -> None:
        self._sessions = sessions
        self._protector = protector

    async def resolve(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        action: WorkspaceAction,
        secret_id: str,
    ) -> str:
        async with short_session(self._sessions) as session:
            try:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=action,
                )
            except AuthorizationError as error:
                raise GitHubCredentialError("the selected GitHub credential is unavailable") from error
            if workspace.organization_id != organization_id:
                raise GitHubCredentialError("the selected GitHub credential is unavailable")
            record = await session.scalar(
                select(ManagedSecretRecord).where(
                    ManagedSecretRecord.id == secret_id,
                    ManagedSecretRecord.organization_id == organization_id,
                    ManagedSecretRecord.workspace_id == workspace_id,
                    ManagedSecretRecord.owner_type == "workspace",
                    ManagedSecretRecord.owner_id == workspace_id,
                    ManagedSecretRecord.deleted_at.is_(None),
                )
            )
            if record is None or record.ciphertext is None or record.nonce is None or record.encryption_key_id is None:
                raise GitHubCredentialError("the selected GitHub credential is unavailable")
            encrypted = _EncryptedSecret(
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
            raise GitHubCredentialError("the selected GitHub credential is unavailable") from error
