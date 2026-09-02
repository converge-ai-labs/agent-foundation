"""Transactional Model application service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from anyio import fail_after
from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.domain import PrincipalType
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction

from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .domain import (
    CreateModelRequest,
    CreateModelRevisionRequest,
    InvokingUserSecretCredential,
    Model,
    ModelCollection,
    ModelConnectionTestResult,
    ModelCredential,
    ModelRevision,
    ModelRevisionCollection,
    ModelRevisionCreateResult,
    ModelRevisionInput,
    UpdateModelRequest,
    WorkspaceSecretCredential,
    model_revision_digest,
    new_model_id,
    new_model_revision_id,
)
from .endpoint_policy import EndpointPolicy, EndpointPolicyError
from .models import ModelRecord, ModelRevisionRecord
from .providers import ProviderDefinitionCollection, ProviderRegistry, ValidatedProviderSelection

_CREDENTIAL_ADAPTER = TypeAdapter(ModelCredential)


class ModelError(Exception):
    """Safe stable error raised by the Model Management application layer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CandidateConnectionTester(Protocol):
    def __call__(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        provider_type: str,
        model_name: str,
        credential: ModelCredential,
        selection: ValidatedProviderSelection,
    ) -> Awaitable[None]: ...


class ModelService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        resolve_dns_on_save: bool = True,
        connection_tester: CandidateConnectionTester | None = None,
        connection_test_timeout_seconds: float = 15,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resolve_dns_on_save = resolve_dns_on_save
        self._connection_tester = connection_tester
        self._connection_test_timeout_seconds = connection_test_timeout_seconds

    async def provider_definitions(self, *, actor: AuthenticatedActor) -> ProviderDefinitionCollection:
        async with transaction(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.models_read,
            )
        return ProviderDefinitionCollection(items=self._registry.definitions())

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateModelRequest,
    ) -> ModelRevisionCreateResult:
        selection = await self._validate_provider(request.config)
        now = self._clock()
        model_id = new_model_id()
        revision_id = new_model_revision_id()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                await _require_eligible_credential(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    actor=actor,
                    credential=request.config.credential,
                )
                record = ModelRecord(
                    id=model_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    name=request.name,
                    normalized_name=_normalize_name(request.name),
                    description=request.description,
                    version=1,
                    current_revision_id=revision_id,
                    enabled=request.enabled,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                revision = _new_revision(
                    record,
                    revision_id=revision_id,
                    version=1,
                    request=request.config,
                    selection=selection,
                    actor=actor,
                    now=now,
                )
                session.add_all((record, revision))
                session.add(
                    _audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        model_id=model_id,
                        action="model.create",
                        now=now,
                    )
                )
                await session.flush()
                return ModelRevisionCreateResult(model=record.to_resource(), revision=revision.to_resource())
        except IntegrityError as error:
            raise ModelError(
                "model_name_conflict",
                "A Model with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str, model_id: str) -> Model:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            return (
                await _require_model(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                )
            ).to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int = 50,
        cursor: str | None = None,
        name: str | None = None,
        provider_type: str | None = None,
        enabled: bool | None = None,
    ) -> ModelCollection:
        if limit < 1 or limit > 100:
            raise ModelError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        if name is not None and (not name.strip() or len(name) > 128):
            raise ModelError("invalid_request", "name search is invalid.", status_code=400)
        if provider_type is not None:
            try:
                self._registry.definition(provider_type)
            except ValueError as error:
                raise ModelError(
                    "invalid_request", "provider_type is not a trusted provider.", status_code=400
                ) from error
        scope: dict[str, object] = {
            "workspace_id": workspace_id,
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "name": name,
            "provider_type": provider_type,
            "enabled": enabled,
        }
        try:
            position = decode_model_cursor(cursor, scope=scope) if cursor is not None else None
        except CursorError as error:
            raise ModelError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelRecord).where(
                ModelRecord.organization_id == workspace.organization_id,
                ModelRecord.workspace_id == workspace.workspace_id,
            )
            if name is not None:
                escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query = query.where(ModelRecord.name.ilike(f"%{escaped}%", escape="\\"))
            if provider_type is not None:
                query = query.join(
                    ModelRevisionRecord,
                    ModelRevisionRecord.id == ModelRecord.current_revision_id,
                ).where(ModelRevisionRecord.provider_type == provider_type)
            if enabled is not None:
                query = query.where(ModelRecord.enabled == enabled)
            if position is not None:
                updated_at, cursor_model_id = position
                query = query.where(
                    or_(
                        ModelRecord.updated_at < updated_at,
                        and_(ModelRecord.updated_at == updated_at, ModelRecord.id < cursor_model_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ModelRecord.updated_at.desc(), ModelRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_model_cursor(
                    updated_at=page[-1].updated_at,
                    model_id=page[-1].id,
                    scope=scope,
                )
            return ModelCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        if_match: str,
        request: UpdateModelRequest,
    ) -> Model:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                record = await _require_model(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                    lock=True,
                )
                _require_etag(record, if_match)
                if "name" in request.model_fields_set:
                    assert request.name is not None
                    record.name = request.name
                    record.normalized_name = _normalize_name(request.name)
                if "description" in request.model_fields_set:
                    record.description = request.description
                if "enabled" in request.model_fields_set:
                    assert request.enabled is not None
                    record.enabled = request.enabled
                _touch(record, actor=actor, now=now)
                session.add(
                    _audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        model_id=model_id,
                        action="model.update",
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise ModelError(
                "model_name_conflict",
                "A Model with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def create_revision(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        request: CreateModelRevisionRequest,
    ) -> ModelRevisionCreateResult:
        selection = await self._validate_provider(request.config)
        now = self._clock()
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            record = await _require_model(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                model_id=model_id,
                lock=True,
            )
            _require_version(record, request.expected_version)
            await _require_eligible_credential(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                actor=actor,
                credential=request.config.credential,
            )
            current = await _require_revision(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                revision_id=record.current_revision_id,
            )
            candidate = _new_revision(
                record,
                revision_id=new_model_revision_id(),
                version=record.version + 1,
                request=request.config,
                selection=selection,
                actor=actor,
                now=now,
            )
            if candidate.content_digest == current.content_digest:
                return ModelRevisionCreateResult(model=record.to_resource(), revision=current.to_resource())
            session.add(candidate)
            record.version = candidate.version
            record.current_revision_id = candidate.id
            _touch(record, actor=actor, now=now)
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                    action="model.revision.create",
                    now=now,
                )
            )
            await session.flush()
            return ModelRevisionCreateResult(model=record.to_resource(), revision=candidate.to_resource())

    async def list_revisions(
        self, *, actor: AuthenticatedActor, workspace_id: str, model_id: str
    ) -> ModelRevisionCollection:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            await _require_model(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                model_id=model_id,
            )
            revisions = tuple(
                (
                    await session.scalars(
                        select(ModelRevisionRecord)
                        .where(ModelRevisionRecord.model_id == model_id)
                        .order_by(ModelRevisionRecord.version.desc())
                    )
                ).all()
            )
            return ModelRevisionCollection(items=tuple(item.to_resource() for item in revisions))

    async def get_revision(self, *, actor: AuthenticatedActor, workspace_id: str, revision_id: str) -> ModelRevision:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            return (
                await _require_revision(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    revision_id=revision_id,
                )
            ).to_resource()

    async def test_candidate(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: ModelRevisionInput,
    ) -> ModelConnectionTestResult:
        if self._connection_tester is None:
            raise ModelError(
                "model_connection_tester_unavailable", "Model connection testing is unavailable.", status_code=503
            )
        selection = await self._validate_provider(request)
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            await _require_eligible_credential(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                actor=actor,
                credential=request.credential,
            )
            organization_id = workspace.organization_id
        started = monotonic()
        success = True
        code = "connection_succeeded"
        message = "The provider connection succeeded."
        try:
            with fail_after(self._connection_test_timeout_seconds):
                await self._connection_tester(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    provider_type=request.provider_type,
                    model_name=request.model_name,
                    credential=request.credential,
                    selection=selection,
                )
        except TimeoutError:
            success = False
            code = "connection_timeout"
            message = "The provider connection timed out."
        except Exception:
            success = False
            code = "connection_failed"
            message = "The provider connection failed."
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        async with transaction(self._sessions) as session:
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    model_id=None,
                    action="model.test",
                    now=self._clock(),
                    outcome="success" if success else "failure",
                )
            )
        return ModelConnectionTestResult(success=success, elapsed_ms=elapsed_ms, code=code, message=message)

    async def _validate_provider(self, request: ModelRevisionInput) -> ValidatedProviderSelection:
        try:
            selection = self._registry.validate(
                provider_type=request.provider_type,
                model_name=request.model_name,
                provider_config=request.provider_config,
                credential=request.credential,
                capabilities=request.capabilities,
            )
            if selection.base_url is None:
                return selection
            normalized_url = await self._endpoint_policy.validate(
                selection.base_url, resolve_dns=self._resolve_dns_on_save
            )
        except (ValueError, EndpointPolicyError) as error:
            raise ModelError(
                "invalid_model_configuration",
                "The model configuration is invalid.",
                status_code=400,
            ) from error
        provider_config = dict(selection.provider_config)
        if request.provider_type == "openai_compatible":
            provider_config["base_url"] = normalized_url
        elif request.provider_type == "azure_openai":
            provider_config["resource_endpoint"] = normalized_url
        return selection.model_copy(update={"base_url": normalized_url, "provider_config": provider_config})


def _new_revision(
    model: ModelRecord,
    *,
    revision_id: str,
    version: int,
    request: ModelRevisionInput,
    selection: ValidatedProviderSelection,
    actor: AuthenticatedActor,
    now: datetime,
) -> ModelRevisionRecord:
    return ModelRevisionRecord(
        id=revision_id,
        organization_id=model.organization_id,
        workspace_id=model.workspace_id,
        model_id=model.id,
        version=version,
        provider_type=request.provider_type,
        model_name=request.model_name,
        base_url=selection.base_url,
        credential=_CREDENTIAL_ADAPTER.dump_python(request.credential, mode="json"),
        provider_config=selection.provider_config,
        capabilities=selection.capabilities.model_dump(mode="json"),
        capability_source=selection.capability_source.value,
        content_digest=model_revision_digest(
            provider_type=request.provider_type,
            model_name=request.model_name,
            base_url=selection.base_url,
            credential=request.credential,
            provider_config=selection.provider_config,
            capabilities=selection.capabilities,
            capability_source=selection.capability_source,
        ),
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


async def _require_model(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    model_id: str,
    lock: bool = False,
) -> ModelRecord:
    query = select(ModelRecord).where(
        ModelRecord.organization_id == organization_id,
        ModelRecord.workspace_id == workspace_id,
        ModelRecord.id == model_id,
    )
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ModelError("model_not_found", "The Model was not found.", status_code=404)
    return record


async def _require_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
) -> ModelRevisionRecord:
    revision = await session.scalar(
        select(ModelRevisionRecord).where(
            ModelRevisionRecord.organization_id == organization_id,
            ModelRevisionRecord.workspace_id == workspace_id,
            ModelRevisionRecord.id == revision_id,
        )
    )
    if revision is None:
        raise ModelError("model_revision_not_found", "The ModelRevision was not found.", status_code=404)
    return revision


def _require_version(record: ModelRecord, expected: int) -> None:
    if record.version != expected:
        raise ModelError(
            "model_version_conflict",
            "The Model version has changed.",
            status_code=409,
            details={"current_version": record.version},
        )


def _require_etag(record: ModelRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise ModelError(
            "precondition_failed",
            "The Model changed after it was read.",
            status_code=412,
            details={"current_etag": current},
        )


def _touch(record: ModelRecord, *, actor: AuthenticatedActor, now: datetime) -> None:
    record.updated_by_type = actor.principal.principal_type.value
    record.updated_by_id = actor.principal.principal_id
    record.updated_at = now


async def _authorize(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ModelError(
            "resource_not_found" if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            status_code=404 if error.concealed else 403,
        ) from error


async def _require_eligible_credential(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    actor: AuthenticatedActor,
    credential: ModelCredential,
) -> None:
    query = select(SecretRecord.id).where(
        SecretRecord.organization_id == organization_id,
        SecretRecord.workspace_id == workspace_id,
        SecretRecord.deleted_at.is_(None),
    )
    if isinstance(credential, WorkspaceSecretCredential):
        query = query.where(
            SecretRecord.id == credential.secret_id,
            SecretRecord.owner_type == "workspace",
            SecretRecord.owner_id == workspace_id,
        )
    elif isinstance(credential, InvokingUserSecretCredential):
        if actor.principal.principal_type is not PrincipalType.user:
            raise ModelError(
                "credential_not_eligible",
                "The selected credential is not eligible for this operation.",
                status_code=400,
            )
        query = query.where(
            SecretRecord.owner_type == "user",
            SecretRecord.owner_id == actor.principal.principal_id,
            SecretRecord.key == credential.secret_key,
        )
    else:
        return
    if await session.scalar(query) is None:
        raise ModelError(
            "credential_not_eligible",
            "The selected credential is not eligible for this operation.",
            status_code=400,
        )


def _audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str,
    model_id: str | None,
    action: str,
    now: datetime,
    outcome: str = "success",
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="model",
        resource_id=model_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome=outcome,
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def _normalize_name(name: str) -> str:
    return name.casefold()
