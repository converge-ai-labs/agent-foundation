"""Operation-local, live platform audience verification for Bot memory tools."""

from __future__ import annotations

from dataclasses import dataclass, replace

import anyio
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.bots.connectivity.probe import InstallationProbe
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.iam import WorkspaceAction
from a13n_service.memory.service import failure
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session

from .access import RuntimeAuthority, VerifiedConversations, authorize
from .models import DocumentRecord, ScopeRecord
from .queries import visible_documents


@dataclass(frozen=True, slots=True)
class _Conversation:
    scope_id: str
    version: int
    external_id: str
    audience: str


class BotMemoryVerifier:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        protector: SecretProtector,
        *,
        timeout_seconds: float = 20,
    ) -> None:
        self.sessions = sessions
        self.http_client = http_client
        self.endpoint_validator = endpoint_validator
        self.protector = protector
        self.timeout_seconds = timeout_seconds

    async def verify(
        self,
        authority: RuntimeAuthority,
        *,
        write: bool = False,
        document_id: str | None = None,
    ) -> RuntimeAuthority:
        action = WorkspaceAction.bot_memory_create if write else WorkspaceAction.bot_memory_read
        async with short_session(self.sessions) as session:
            scope = await session.get(ScopeRecord, authority.scope_id)
            if scope is None:
                raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
            await authorize(session, authority, scope, action, before_provider_check=True)
            account = await session.get(AccountRecord, authority.account_id)
            assert account is not None
            credentials = account.credential_snapshot()
            provider_key, config_version, config = (
                account.provider_key,
                account.provider_config_version,
                dict(account.provider_config_json),
            )
            conversations = [_Conversation(scope.id, scope.version, scope.external_conversation_id, scope.audience)]
            if not write:
                origins = select(DocumentRecord.scope_id).where(visible_documents(scope))
                if document_id is not None:
                    origins = origins.where(DocumentRecord.id == document_id)
                source_scopes = list(
                    await session.scalars(
                        select(ScopeRecord)
                        .where(ScopeRecord.id.in_(origins.distinct()), ScopeRecord.id != scope.id)
                        .limit(16)
                    )
                )
                if len(source_scopes) == 16:
                    raise failure(
                        "memory_search_too_broad",
                        "Too many shared groups for one memory operation.",
                        ErrorCategory.size_limit,
                    )
                conversations.extend(
                    _Conversation(item.id, item.version, item.external_conversation_id, item.audience)
                    for item in source_scopes
                )
        try:
            with anyio.fail_after(self.timeout_seconds):
                probe = InstallationProbe(
                    self.http_client,
                    self.endpoint_validator,
                    provider_key=provider_key,
                    config_version=config_version,
                    config=config,
                    credentials=credentials,
                    protector=self.protector,
                )
                identity = await probe.installation()
                if not identity.enabled:
                    raise ConnectivityHttpError("bot_inactive")
                errors: list[ConnectivityHttpError] = []
                semaphore = anyio.Semaphore(4)

                async def check(item: _Conversation) -> None:
                    try:
                        async with semaphore:
                            observed = await probe.conversation(item.external_id)
                        if (
                            observed.is_member is not True
                            or observed.is_active is not True
                            or observed.audience == "unknown"
                            or observed.audience != item.audience
                        ):
                            raise ConnectivityHttpError("conversation_access_unverified")
                    except ConnectivityHttpError as error:
                        errors.append(error)

                async with anyio.create_task_group() as group:
                    for item in conversations:
                        group.start_soon(check, item)
                if errors:
                    raise errors[0]
        except (ConnectivityHttpError, TimeoutError) as error:
            raise failure(
                "memory_scope_unverified",
                "The platform could not confirm current conversation access.",
                ErrorCategory.unavailable,
            ) from error
        verified = replace(
            authority,
            verified=VerifiedConversations(
                credentials.generation, tuple((item.scope_id, item.version) for item in conversations)
            ),
        )
        async with short_session(self.sessions) as session:
            scope = await session.get(ScopeRecord, authority.scope_id)
            if scope is None:
                raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
            await authorize(session, verified, scope, action)
        return verified

    async def verify_organization(self, run_id: str, storage_id: str) -> None:
        from a13n_service.memory.models import MemoryStorageRecord

        from .organization import authorize_organization

        async with short_session(self.sessions) as session:
            storage = await session.get(MemoryStorageRecord, storage_id)
            if storage is None:
                raise failure("memory_scope_unavailable", "Memory storage is unavailable.")
            await authorize_organization(session, run_id, storage, {})
            scope = await session.get(ScopeRecord, storage.subject_id)
            assert scope is not None
            account = await session.get(AccountRecord, scope.account_id)
            assert account is not None
            scope_id, scope_version, account_id = scope.id, scope.version, account.id
            audience, external_id = scope.audience, scope.external_conversation_id
            credentials = account.credential_snapshot()
            provider_key, config_version, config = (
                account.provider_key,
                account.provider_config_version,
                dict(account.provider_config_json),
            )
        with anyio.fail_after(self.timeout_seconds):
            probe = InstallationProbe(
                self.http_client,
                self.endpoint_validator,
                provider_key=provider_key,
                config_version=config_version,
                config=config,
                credentials=credentials,
                protector=self.protector,
            )
            installation = await probe.installation()
            observed = await probe.conversation(external_id)
            if (
                not installation.enabled
                or observed.is_member is not True
                or observed.is_active is not True
                or observed.audience != audience
            ):
                raise failure("memory_scope_unverified", "Conversation access cannot be confirmed.")
        async with short_session(self.sessions) as session:
            storage = await session.get(MemoryStorageRecord, storage_id)
            assert storage is not None
            await authorize_organization(session, run_id, storage, {})
            scope = await session.get(ScopeRecord, scope_id)
            account = await session.get(AccountRecord, account_id)
            if (
                scope is None
                or scope.version != scope_version
                or account is None
                or account.credential_generation != credentials.generation
            ):
                raise failure("memory_scope_unverified", "Conversation access changed.")
