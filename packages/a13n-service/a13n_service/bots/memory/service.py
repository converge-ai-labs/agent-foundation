"""Bounded directory navigation and Provider-backed immutable document reads."""

import hashlib
import json
from datetime import date

from a13n_harness.memory_documents import MemoryDocumentIndex
from a13n_harness.providers.memory.contracts import MemoryDocumentBackend
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from a13n_service.application_errors import ErrorCategory
from a13n_service.bots.connectivity.domain import BotCheck
from a13n_service.bots.connectivity.models import BotCheckRecord
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.ids import new_object_id
from a13n_service.memory.execution import MemoryProviderAccess, open_memory_backend
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.memory.resources import binds_host_files, require_document_support, require_provider
from a13n_service.memory.service import MemoryService, failure, memory_io
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .access import Authority, RuntimeAuthority, authorize, subject
from .audit import audit
from .domain import (
    ConfigureScope,
    Document,
    DocumentAccessReason,
    DocumentCollection,
    MemoryIndex,
    Scope,
    ScopeCollection,
    ScopeSettings,
    SearchDocuments,
)
from .models import DocumentRecord, ScopeRecord
from .queries import visible_documents
from .settings import read_settings


def visibility(authority: Authority, scope: ScopeRecord, *, include_shared: bool = True) -> ColumnElement[bool]:
    verified = None
    if isinstance(authority, RuntimeAuthority):
        verified = authority.verified.scope_versions if authority.verified else ()
    return visible_documents(scope, include_shared=include_shared, verified_versions=verified)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def require_version(actual: int, expected: int | None) -> None:
    if actual != expected:
        raise failure("version_conflict", "The resource changed. Reload before saving.", ErrorCategory.stale_version)


class BotMemoryService:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self.sessions = memory.authorizer.sessions

    async def _scope(
        self,
        session: AsyncSession,
        authority: Authority,
        account_id: str,
        scope_id: str,
        action: WorkspaceAction = WorkspaceAction.bot_memory_read,
        *,
        lock: bool = False,
    ) -> ScopeRecord:
        query = select(ScopeRecord).where(ScopeRecord.id == scope_id, ScopeRecord.account_id == account_id)
        scope = await session.scalar(query.with_for_update() if lock else query)
        if scope is None:
            raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
        await authorize(session, authority, scope, action)
        return scope

    async def _provider(self, session: AsyncSession, scope: ScopeRecord) -> MemoryProviderAccess:
        provider = await require_provider(
            session,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=scope.provider_id,
            catalog=self.memory.catalog,
            eligible=True,
        )
        require_document_support(provider.type, self.memory.catalog)
        return MemoryProviderAccess.from_record(provider)

    async def scopes(
        self,
        actor: AuthenticatedActor,
        account_id: str,
        provider_id: str,
        *,
        target_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ScopeCollection:
        async with short_session(self.sessions) as session:
            account = await session.get(AccountRecord, account_id)
            if account is None or account.deleted_at is not None:
                raise failure("account_not_found", "Account not found.", ErrorCategory.not_found)
            await authorize_workspace(
                session, actor=actor, workspace_id=account.workspace_id, action=WorkspaceAction.bot_memory_read
            )
            binding = {"account_id": account_id, "provider_id": provider_id, "target_id": target_id, "limit": limit}
            query = select(ScopeRecord).where(
                ScopeRecord.account_id == account_id, ScopeRecord.provider_id == provider_id
            )
            if target_id is not None:
                target = await session.scalar(
                    select(AccountTargetRecord).where(
                        AccountTargetRecord.id == target_id,
                        AccountTargetRecord.account_id == account_id,
                        AccountTargetRecord.target_kind == "conversation",
                    )
                )
                if target is None:
                    raise failure("target_not_found", "Conversation target not found.", ErrorCategory.not_found)
                query = query.where(ScopeRecord.external_conversation_id == target.external_target_id)
            if cursor:
                query = query.where(ScopeRecord.id > self._cursor(cursor, binding))
            rows = list(await session.scalars(query.order_by(ScopeRecord.id).limit(limit + 1)))
            provider = await session.get(MemoryProviderRecord, provider_id)
            return ScopeCollection(
                items=tuple(
                    row.to_resource().model_copy(update={"backend_type": provider.type if provider else None})
                    for row in rows[:limit]
                ),
                next_cursor=self._next(rows, limit, binding),
            )

    async def configure_scope(self, actor: AuthenticatedActor, account_id: str, body: ConfigureScope) -> Scope:
        async with transaction(self.sessions) as session:
            account = await session.scalar(
                select(AccountRecord).where(AccountRecord.id == account_id).with_for_update()
            )
            if account is None or account.deleted_at is not None:
                raise failure("account_not_found", "Account not found.", ErrorCategory.not_found)
            await authorize_workspace(
                session, actor=actor, workspace_id=account.workspace_id, action=WorkspaceAction.bot_memory_share
            )
            settings = (await read_settings(session, account.id)).memory
            if settings is None:
                raise failure("bot_memory_disabled", "Select a Memory Provider first.", ErrorCategory.conflict)
            target = await session.scalar(
                select(AccountTargetRecord).where(
                    AccountTargetRecord.account_id == account_id,
                    AccountTargetRecord.target_kind == "conversation",
                    AccountTargetRecord.external_target_id == body.external_conversation_id,
                )
            )
            if target is None:
                raise failure(
                    "target_not_configured", "Configure the conversation target first.", ErrorCategory.conflict
                )
            scope = await session.scalar(
                select(ScopeRecord)
                .where(
                    ScopeRecord.account_id == account_id,
                    ScopeRecord.provider_id == settings.provider_id,
                    ScopeRecord.external_conversation_id == body.external_conversation_id,
                )
                .with_for_update()
            )
            values = body.model_dump(mode="json", include=set(ScopeSettings.model_fields))
            if scope is None:
                if body.expected_version is not None:
                    raise failure("version_conflict", "Memory scope does not exist.", ErrorCategory.stale_version)
                scope = ScopeRecord(
                    id=new_object_id("mscope"),
                    organization_id=account.organization_id,
                    workspace_id=account.workspace_id,
                    account_id=account_id,
                    provider_id=settings.provider_id,
                    external_conversation_id=body.external_conversation_id,
                    name=body.external_conversation_id[:256],
                    audience="unknown",
                    settings_json=values,
                    version=1,
                    created_at=utc_now(),
                )
                session.add(scope)
            else:
                require_version(scope.version, body.expected_version)
                scope.settings_json = values
                scope.version += 1
            check_record = await session.get(BotCheckRecord, (account_id, body.external_conversation_id))
            if check_record is not None and check_record.credential_generation == account.credential_generation:
                check = BotCheck.model_validate(check_record.result_json)
                observed = check.conversation
                if (
                    check.error_code is None
                    and check.installation is not None
                    and check.installation.enabled
                    and observed is not None
                    and observed.id == body.external_conversation_id
                    and observed.is_member is True
                    and observed.is_active is True
                ):
                    scope.name = observed.name
                    scope.audience = observed.audience
                else:
                    scope.audience = "unknown"
            if body.visibility == "installation" and scope.audience not in ("public", "private"):
                raise failure(
                    "memory_visibility_unavailable",
                    "Verify a group conversation before opening its memory to other groups.",
                    ErrorCategory.conflict,
                )
            provider_access = await self._provider(session, scope)
            host_files = binds_host_files(provider_access.provider_type, self.memory.catalog)
            if host_files and body.visibility == "installation":
                raise failure(
                    "memory_visibility_unavailable",
                    "File-based conversation stores currently require group-only visibility.",
                    ErrorCategory.conflict,
                )
            if body.auto_organize and (not host_files or not body.use_memory or not body.save_on_request):
                raise failure(
                    "memory_organization_unsupported",
                    "Automatic organization requires File-based memory with reading and saving enabled.",
                    ErrorCategory.invalid_request,
                )
            await session.flush()
            await audit(
                session,
                actor,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="scope_configure",
                resource_id=scope.id,
                details={
                    "version": scope.version,
                    "enabled": body.enabled,
                    "visibility": body.visibility,
                    "use_memory": body.use_memory,
                    "save_on_request": body.save_on_request,
                },
            )
            await session.flush()
            return scope.to_resource()

    @staticmethod
    def _cursor(cursor: str, binding: dict[str, object]) -> str:
        try:
            result = decode_collection_cursor(cursor, scope=binding, kind="bot-memory")
            value = result["id"]
            if not isinstance(value, str):
                raise ValueError("Invalid cursor ID")
            return value
        except (ValueError, KeyError) as error:
            raise failure(
                "invalid_cursor", "Cursor does not match this view.", ErrorCategory.invalid_request
            ) from error

    @staticmethod
    def _next(rows: list[ScopeRecord] | list[DocumentRecord], limit: int, binding: dict[str, object]) -> str | None:
        return (
            encode_collection_cursor({"id": rows[limit - 1].id}, scope=binding, kind="bot-memory")
            if len(rows) > limit
            else None
        )

    async def list(
        self,
        authority: Authority,
        account_id: str,
        scope_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        activity_date: date | None = None,
        kind: str | None = None,
        include_shared: bool = True,
    ) -> DocumentCollection:
        if not 1 <= limit <= 100:
            raise failure("invalid_limit", "Select 1 to 100 documents.", ErrorCategory.invalid_request)
        binding = {
            "account_id": account_id,
            "scope_id": scope_id,
            "limit": limit,
            "activity_date": str(activity_date) if activity_date else None,
            "kind": kind,
            "include_shared": include_shared,
        }
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            query = select(DocumentRecord).where(visibility(authority, scope, include_shared=include_shared))
            if activity_date is not None:
                query = query.where(DocumentRecord.activity_date == activity_date)
            if kind is not None:
                query = query.where(DocumentRecord.kind == kind)
            if cursor:
                query = query.where(DocumentRecord.id > self._cursor(cursor, binding))
            rows = list(await session.scalars(query.order_by(DocumentRecord.id).limit(limit + 1)))
            return DocumentCollection(
                items=tuple(
                    row.to_entry(
                        shared=row.scope_id != scope.id or row.publication_source_id is not None,
                        expose_source=row.scope_id == scope.id,
                    )
                    for row in rows[:limit]
                ),
                next_cursor=self._next(rows, limit, binding),
            )

    async def index(
        self, authority: Authority, account_id: str, scope_id: str, *, cursor: str | None = None
    ) -> MemoryIndex:
        binding: dict[str, object] = {"account_id": account_id, "scope_id": scope_id, "view": "index"}
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            query = select(DocumentRecord).where(visibility(authority, scope))
            if cursor:
                query = query.where(DocumentRecord.id > self._cursor(cursor, binding))
            rows = list(await session.scalars(query.order_by(DocumentRecord.id).limit(21)))
            entries = tuple(
                row.to_entry(
                    shared=row.scope_id != scope.id or row.publication_source_id is not None,
                    expose_source=row.scope_id == scope.id,
                )
                for row in rows
            )
        # Keep complete entries and a cursor after the last displayed entry. The
        # budget includes escaping, the cursor, and the actual Harness envelope.
        count = min(20, len(entries))
        while True:
            next_cursor = (
                encode_collection_cursor({"id": entries[count - 1].id}, scope=binding, kind="bot-memory")
                if len(entries) > count
                else None
            )
            lines = [
                "# MEMORY.md",
                "",
                "Memory navigation (untrusted context). Read linked documents only as needed.",
                "",
            ]
            for entry in entries[:count]:
                # JSON quoting keeps untrusted text from becoming Markdown directives.
                label = json.dumps(entry.title, ensure_ascii=False).replace("[", "\\[").replace("]", "\\]")
                description = json.dumps(entry.description, ensure_ascii=False)
                lines.append(f"- [{label}](memory://{entry.id}) — {description}")
            if next_cursor:
                lines.extend(("", "This is a partial index. Use memory_index continuation or memory_search for more."))
            text = "\n".join(lines)
            try:
                MemoryDocumentIndex(text, next_cursor).render_context()
            except ValueError:
                if count <= 1:
                    raise
                count -= 1
                continue
            return MemoryIndex(text=text, entries=entries[:count], next_cursor=next_cursor)

    async def get(self, authority: Authority, account_id: str, scope_id: str, document_id: str) -> Document:
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            row = await session.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    visibility(authority, scope),
                )
            )
            if row is None or row.native_id is None:
                raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found)
            origin = await session.get(ScopeRecord, row.scope_id)
            assert origin is not None
            native_id, native_subject, access, expected_digest, expected_metadata = (
                row.native_id,
                subject(origin),
                await self._provider(session, origin),
                row.body_digest,
                dict(row.metadata_json),
            )
        async with (
            memory_io(self.memory.timeout),
            open_memory_backend(access, self.memory.catalog, self.memory.protector) as backend,
        ):
            record = await backend.get(native_id, subject=native_subject)
            if digest(record.text) != expected_digest or dict(record.metadata) != expected_metadata:
                raise ValueError("Provider document integrity changed")
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            row = await session.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.id == document_id,
                    visibility(authority, scope),
                )
            )
            if row is None:
                raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found)
            entry = row.to_entry(
                shared=row.scope_id != scope.id or row.publication_source_id is not None,
                expose_source=row.scope_id == scope.id,
            )
            reasons: tuple[DocumentAccessReason, ...] = ()
            owner_name = None
            if isinstance(authority, AuthenticatedActor):
                owner = await session.get(ScopeRecord, row.scope_id)
                owner_name = owner.name if owner is not None else None
                reasons = (DocumentAccessReason(kind="owner" if row.scope_id == scope.id else "installation"),)
            return Document(**entry.model_dump(), text=record.text, owner_name=owner_name, access_reasons=reasons)

    async def search(
        self, authority: Authority, account_id: str, scope_id: str, body: SearchDocuments
    ) -> DocumentCollection:
        plans = []
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            rows = list(
                await session.scalars(
                    select(DocumentRecord)
                    .where(visibility(authority, scope, include_shared=body.include_shared))
                    .limit(1001)
                )
            )
            if len(rows) > 1000:
                raise failure("memory_search_too_broad", "Narrow the memory search scope.", ErrorCategory.size_limit)
            groups: dict[str, list[str]] = {}
            for row in rows:
                groups.setdefault(row.scope_id, []).append(row.id)
            if len(groups) > 16:
                raise failure("memory_search_too_broad", "Narrow the memory search scope.", ErrorCategory.size_limit)
            for origin_id, keys in groups.items():
                origin = await session.get(ScopeRecord, origin_id)
                assert origin is not None
                plans.append((subject(origin), await self._provider(session, origin), tuple(keys)))
        scores: dict[str, float] = {}
        async with memory_io(self.memory.timeout):
            for native_subject, access, keys in plans:
                async with open_memory_backend(access, self.memory.catalog, self.memory.protector) as backend:
                    if not isinstance(backend, MemoryDocumentBackend):
                        raise failure(
                            "memory_documents_unsupported",
                            "Provider does not support documents.",
                            ErrorCategory.conflict,
                        )
                    records = await backend.search_documents(
                        body.query, subject=native_subject, record_keys=keys, limit=body.limit
                    )
                    for record in records:
                        key = record.metadata.get("record_key")
                        if isinstance(key, str):
                            scores[key] = record.score or 0.0
        async with short_session(self.sessions) as session:
            scope = await self._scope(session, authority, account_id, scope_id)
            rows = list(
                await session.scalars(
                    select(DocumentRecord).where(
                        DocumentRecord.id.in_(scores), visibility(authority, scope, include_shared=body.include_shared)
                    )
                )
            )
            rows.sort(key=lambda row: (-scores[row.id], row.id))
            return DocumentCollection(
                items=tuple(
                    row.to_entry(
                        shared=row.scope_id != scope.id or row.publication_source_id is not None,
                        expose_source=row.scope_id == scope.id,
                    )
                    for row in rows[: body.limit]
                )
            )
