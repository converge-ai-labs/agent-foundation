"""Environment authoring and allocation; no target I/O occurs on control paths."""

from __future__ import annotations

import json
from datetime import datetime
from typing import cast

from a13n_envd_client.eip.v1 import DirectoryListResult
from a13n_environment import EnvironmentProviderCatalog, EnvironmentProviderError, EnvironmentState
from a13n_environment.docker.configuration import DockerProviderConfiguration
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import is_evidence_unique_race
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.labels import Labels, LabelsBody, label_predicates, labels_etag
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import next_updated_at, utc_now

from .access import authorize_environment_resource, authorize_environment_workspace, environment_actor_scope
from .configuration import load_configuration
from .cursors import decode_cursor, encode_cursor
from .devices import ENVD_PROVIDER_KEYS, DeviceDiscovery, DeviceInfo, DeviceTarget, capture_device_target
from .domain import (
    Collection,
    CreateEnvironmentRequest,
    CreateManagedEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
    CreateTemplateRevisionRequest,
    Environment,
    EnvironmentCommand,
    EnvironmentCommandRequest,
    EnvironmentDetail,
    EnvironmentProvider,
    EnvironmentProviderDefinition,
    EnvironmentTemplate,
    EnvironmentTemplateRevision,
    JsonObject,
    NewEnvironmentSelection,
    RegisterEnvironmentRequest,
    ReplaceCredentialRequest,
    TemplateConfiguration,
    UpdateEnvironmentRequest,
    UpdateProviderRequest,
    UpdateTemplateRequest,
)
from .errors import EnvironmentManagementError, environment_not_found, invalid_environment, is_target_identity_conflict
from .identity import default_environment_name
from .identity import target_identity as scoped_target_identity
from .image_jobs import (
    ImageTestIdentity,
    ImageTestRequest,
    ImageTestResponse,
    ProviderConnectivity,
    cancel_image_test,
    connectivity_key,
    request_image_test,
)
from .models import (
    EnvironmentCommandRecord,
    EnvironmentProviderRecord,
    EnvironmentRecord,
    EnvironmentTemplateRecord,
    EnvironmentTemplateRevisionRecord,
)
from .selection import allocate_selection, resolve_selection


class EnvironmentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalog: EnvironmentProviderCatalog,
        protector: SecretProtector,
        *,
        deployment_provider_types: frozenset[str] = frozenset(),
        redis: Redis | None = None,
        devices: DeviceDiscovery | None = None,
    ) -> None:
        self.sessions = sessions
        self.catalog = catalog
        self.protector = protector
        self.deployment_provider_types = deployment_provider_types
        self.redis = redis
        self.devices = devices or DeviceDiscovery(protector)

    async def _device_target(self, actor: AuthenticatedActor, environment_id: str) -> DeviceTarget:
        async with short_session(self.sessions) as session:
            row = await session.get(EnvironmentRecord, environment_id)
            if row is None:
                raise environment_not_found()
            await authorize_environment_resource(
                session,
                actor=actor,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                action=WorkspaceAction.environment_use,
            )
            return await capture_device_target(session, row, principal=actor.principal)

    async def device_info(self, *, actor: AuthenticatedActor, environment_id: str) -> DeviceInfo:
        target = await self._device_target(actor, environment_id)
        descriptor = await self.devices.describe(target)
        return DeviceInfo(
            environment_id=environment_id,
            path_style=descriptor.path_style.value,
            default_working_directory=descriptor.default_working_directory,
            directory_discovery=descriptor.directory_discovery,
        )

    async def device_directories(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        path: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> DirectoryListResult:
        target = await self._device_target(actor, environment_id)
        return await self.devices.directories(target, path=path, offset=offset, limit=limit)

    async def provider_types(self, actor: AuthenticatedActor) -> Collection[EnvironmentProviderDefinition]:
        async with short_session(self.sessions) as session:
            await authorize_environment_workspace(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.environment_provider_read,
            )
        return Collection(
            items=tuple(
                EnvironmentProviderDefinition(
                    type=provider.key,
                    display_name=provider.display_name,
                    configuration_versions=tuple(sorted(provider.configuration_versions)),
                    configuration_schema=provider.provider_configuration_model.model_json_schema(),
                    template_configuration_schemas={
                        version: model.model_json_schema() for version, model in provider.configuration_models.items()
                    },
                    deployment_managed=provider.key in self.deployment_provider_types,
                    credential_schema=provider.credential_model.model_json_schema()
                    if provider.credential_model
                    else None,
                    supports_managed=provider.supports_managed,
                    supports_stop=provider.supports_stop,
                    supports_destroy=provider.supports_destroy,
                    requires_keepalive=provider.requires_keepalive,
                )
                for provider in self.catalog.values()
            )
        )

    async def provider_connectivity(self, *, actor: AuthenticatedActor, provider_id: str) -> ProviderConnectivity:
        async with short_session(self.sessions) as session:
            provider = await self._provider(session, actor, provider_id)
            if provider.type != "docker":
                raise invalid_environment("Connectivity is available for Docker Providers")
        if self.redis is None:
            return ProviderConnectivity(status="unknown")
        value = await self.redis.get(connectivity_key(provider_id))
        return ProviderConnectivity.model_validate_json(value) if value else ProviderConnectivity(status="unknown")

    async def test_docker_image(
        self,
        *,
        actor: AuthenticatedActor,
        provider_id: str,
        workspace_id: str | None,
        request_id: str,
        configuration: JsonObject,
    ) -> ImageTestResponse:
        if self.redis is None:
            raise EnvironmentManagementError(
                "environment_unavailable", "Worker image testing is unavailable", category=ErrorCategory.unavailable
            )
        identity = await self._docker_image_identity(
            actor=actor,
            provider_id=provider_id,
            workspace_id=workspace_id,
            request_id=request_id,
            require_enabled=True,
        )
        try:
            checked = self.catalog.require("docker").validate_configuration(schema_version="1", value=configuration)
        except (ValidationError, EnvironmentProviderError) as error:
            raise invalid_environment("Docker image configuration is invalid") from error
        try:
            return await request_image_test(
                self.redis,
                ImageTestRequest(
                    identity=identity,
                    configuration=cast(DockerProviderConfiguration, checked).model_copy(
                        update={"mounts": (), "init_script": None}
                    ),
                ),
            )
        except TimeoutError as error:
            raise EnvironmentManagementError(
                "environment_image_test_timeout", "Worker image test timed out", category=ErrorCategory.timeout
            ) from error
        except ValueError as error:
            raise invalid_environment("Image test request ID is already in use") from error

    async def cancel_docker_image(
        self, *, actor: AuthenticatedActor, provider_id: str, workspace_id: str | None, request_id: str
    ) -> None:
        if self.redis is None:
            raise EnvironmentManagementError(
                "environment_unavailable", "Worker image testing is unavailable", category=ErrorCategory.unavailable
            )
        identity = await self._docker_image_identity(
            actor=actor,
            provider_id=provider_id,
            workspace_id=workspace_id,
            request_id=request_id,
            require_enabled=False,
        )
        await cancel_image_test(self.redis, identity)

    async def _docker_image_identity(
        self,
        *,
        actor: AuthenticatedActor,
        provider_id: str,
        workspace_id: str | None,
        request_id: str,
        require_enabled: bool,
    ) -> ImageTestIdentity:
        async with short_session(self.sessions) as session:
            provider = await self._provider(session, actor, provider_id)
            scope = await authorize_environment_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_template_manage,
            )
            if (
                provider.organization_id != scope.organization_id
                or (provider.workspace_id is not None and provider.workspace_id != workspace_id)
                or provider.type != "docker"
                or (require_enabled and not provider.enabled)
            ):
                raise invalid_environment("Select an enabled Docker Provider in this scope")
        return ImageTestIdentity(
            request_id=request_id,
            provider_id=provider_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            principal_type=actor.principal.principal_type,
            principal_id=actor.principal.principal_id,
        )

    async def create_provider(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, request: CreateProviderRequest
    ) -> EnvironmentProvider:
        if request.type in self.deployment_provider_types:
            raise invalid_environment("Local Providers are configured by the deployment")
        try:
            provider = self.catalog.require(request.type)
            configuration = provider.provider_configuration_model.model_validate(request.configuration)
        except (ValidationError, EnvironmentProviderError) as error:
            raise invalid_environment("Provider type or configuration is invalid") from error
        credential = self._credential(request.type, request.credential)
        now = utc_now()
        async with transaction(self.sessions) as session:
            workspace = await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_provider_manage
            )
            row = EnvironmentProviderRecord(
                id=new_object_id("envp"),
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                type=request.type,
                name=request.name,
                configuration=configuration.model_dump(mode="json", by_alias=True, exclude_none=False),
                enabled=True,
                credential_generation=0,
                created_at=now,
                updated_at=now,
            )
            row.replace_credential(credential, self.protector)
            session.add(row)
            await session.flush()
            return row.to_resource()

    def _credential(self, provider_type: str, value: dict | None) -> str | None:
        model = self.catalog.require(provider_type).credential_model
        if model is None:
            if value is not None:
                raise invalid_environment("this Provider does not accept credentials")
            return None
        try:
            if value is None:
                return None
            model.model_validate(value)
            # Validate with the Provider schema, then encrypt the submitted JSON.
            # Serializing SecretStr fields would irreversibly store their display mask.
            return json.dumps(value)
        except ValidationError as error:
            raise invalid_environment("Provider credential is invalid") from error

    async def update_provider(
        self, *, actor: AuthenticatedActor, provider_id: str, request: UpdateProviderRequest, if_match: str
    ) -> EnvironmentProvider:
        async with transaction(self.sessions) as session:
            row = await self._provider(session, actor, provider_id, manage=True, lock=True)
            self._match(row.id, row.updated_at, if_match)
            if row.configuration_source == "deployment":
                raise invalid_environment("Deployment-owned Providers are read-only")
            if request.name is not None:
                row.name = request.name
            if request.enabled is not None:
                row.enabled = request.enabled
            if "credential" in request.model_fields_set:
                row.replace_credential(self._credential(row.type, request.credential), self.protector)
            row.updated_at = utc_now()
            return row.to_resource()

    async def replace_credential(
        self, *, actor: AuthenticatedActor, provider_id: str, request: ReplaceCredentialRequest, if_match: str
    ) -> EnvironmentProvider:
        return await self.update_provider(
            actor=actor,
            provider_id=provider_id,
            if_match=if_match,
            request=UpdateProviderRequest(credential=request.credential),
        )

    async def create_template(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        request: CreateTemplateRequest,
        idempotency_key: str,
    ) -> EnvironmentTemplate:
        now = utc_now()
        identity = request_identity(idempotency_key, request)
        async with short_session(self.sessions) as session:
            owner = await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_template_manage
            )
        try:
            async with transaction(self.sessions) as session:
                workspace = await authorize_environment_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_template_manage
                )
                replay = await load_replay(
                    session,
                    actor=actor,
                    operation="environment_template.create",
                    scope_id=owner.id,
                    identity=identity,
                    now=now,
                )
                if replay:
                    await self._template(session, actor, replay.result_ref)
                    return replay.restore(EnvironmentTemplate)
                template_config = TemplateConfiguration.model_validate(
                    request.model_dump(exclude={"name", "description", "labels"})
                )
                await self.validate_template_config(
                    session, actor=actor, workspace_id=workspace_id, template_config=template_config
                )
                row = EnvironmentTemplateRecord(
                    id=new_object_id("envtpl"),
                    organization_id=workspace.organization_id,
                    workspace_id=workspace_id,
                    name=request.name,
                    description=request.description,
                    labels=request.labels,
                    version=1,
                    current_revision_id=new_object_id("envrev"),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                await session.flush()
                session.add(self._revision(row, template_config, now))
                session.add(
                    evidence_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        operation="environment_template.create",
                        scope_id=owner.id,
                        identity=identity,
                        result_kind="environment_template",
                        result_ref=row.id,
                        now=now,
                        response=row.to_resource(),
                    )
                )
                return row.to_resource()
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                async with transaction(self.sessions) as session:
                    replay = await load_replay(
                        session,
                        actor=actor,
                        operation="environment_template.create",
                        scope_id=owner.id,
                        identity=identity,
                        now=utc_now(),
                    )
                    if replay is not None:
                        await self._template(session, actor, replay.result_ref)
                        return replay.restore(EnvironmentTemplate)
            raise

    async def validate_template_config(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        template_config: TemplateConfiguration,
    ) -> None:
        row = await self._provider(session, actor, template_config.provider_id)
        if (row.workspace_id is not None and row.workspace_id != workspace_id) or not row.enabled:
            raise environment_not_found()
        provider = self.catalog.require(row.type)
        if not provider.supports_managed:
            raise invalid_environment("the selected Provider supports external registration only")
        try:
            provider.validate_configuration(
                schema_version=template_config.configuration_schema_version, value=template_config.configuration
            )
        except (ValidationError, EnvironmentProviderError) as error:
            raise invalid_environment("Environment template configuration is invalid") from error
        window = template_config.retention.idle
        if window.stop_after is not None and not provider.supports_stop:
            raise invalid_environment("the selected Provider does not support stop")
        if window.delete_after is not None and not provider.supports_destroy:
            raise invalid_environment("the selected Provider does not support delete")

    @staticmethod
    def _revision(
        row: EnvironmentTemplateRecord, template_config: TemplateConfiguration, now: datetime
    ) -> EnvironmentTemplateRevisionRecord:
        return EnvironmentTemplateRevisionRecord(
            id=row.current_revision_id,
            template_id=row.id,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            provider_id=template_config.provider_id,
            version=row.version,
            template_config=template_config.model_dump(mode="json"),
            created_at=now,
        )

    async def create_revision(
        self, *, actor: AuthenticatedActor, template_id: str, request: CreateTemplateRevisionRequest
    ) -> EnvironmentTemplateRevision:
        async with transaction(self.sessions) as session:
            row = await self._template(session, actor, template_id, manage=True, lock=True)
            if row.version != request.expected_version or row.archived_at is not None:
                raise EnvironmentManagementError(
                    "environment_template_conflict",
                    "Template version changed or is archived.",
                    category=ErrorCategory.conflict,
                )
            template_config = TemplateConfiguration.model_validate(request.model_dump(exclude={"expected_version"}))
            await self.validate_template_config(
                session, actor=actor, workspace_id=row.workspace_id, template_config=template_config
            )
            current = await session.get(EnvironmentTemplateRevisionRecord, row.current_revision_id)
            if current is not None and current.template_config == template_config.model_dump(mode="json"):
                return current.to_resource()
            row.version += 1
            row.current_revision_id = new_object_id("envrev")
            row.updated_at = utc_now()
            revision = self._revision(row, template_config, row.updated_at)
            session.add(revision)
            return revision.to_resource()

    async def update_template(
        self, *, actor: AuthenticatedActor, template_id: str, request: UpdateTemplateRequest, if_match: str
    ) -> EnvironmentTemplate:
        async with transaction(self.sessions) as session:
            row = await self._template(session, actor, template_id, manage=True, lock=True)
            self._match(row.id, row.updated_at, if_match)
            if request.name is not None:
                row.name = request.name
            if "description" in request.model_fields_set:
                row.description = request.description
            if request.archived is not None:
                row.archived_at = utc_now() if request.archived else None
            row.updated_at = utc_now()
            return row.to_resource()

    async def allocate(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        selection: NewEnvironmentSelection,
        now: datetime,
        labels: Labels | None = None,
    ) -> EnvironmentRecord:
        await authorize_environment_workspace(
            session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_template_use
        )
        selected = await resolve_selection(session, workspace_id=workspace_id, choice=selection)
        row = await allocate_selection(session, selected, workspace_id=workspace_id, now=now, labels=labels)
        await session.flush()
        return row

    async def create_environment(
        self, *, actor: AuthenticatedActor, workspace_id: str, request: CreateEnvironmentRequest, idempotency_key: str
    ) -> Environment:
        now = utc_now()
        identity = request_identity(idempotency_key, request)
        try:
            async with transaction(self.sessions) as session:
                await authorize_environment_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.environment_template_use
                    if isinstance(request, CreateManagedEnvironmentRequest)
                    else WorkspaceAction.environment_manage,
                )
                replay = await load_replay(
                    session,
                    actor=actor,
                    operation="environment.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=now,
                )
                if replay:
                    await self.require_environment(session, actor, replay.result_ref)
                    return replay.restore(Environment)
                if isinstance(request, CreateManagedEnvironmentRequest):
                    row = await self.allocate(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        selection=request,
                        now=now,
                        labels=request.labels,
                    )
                    if request.name is not None:
                        row.name = request.name
                else:
                    row = await self._register(session, actor, workspace_id, request, now)
                session.add(
                    evidence_record(
                        actor=actor,
                        organization_id=row.organization_id,
                        workspace_id=workspace_id,
                        operation="environment.create",
                        scope_id=workspace_id,
                        identity=identity,
                        result_kind="environment",
                        result_ref=row.id,
                        now=now,
                        response=row.to_resource(),
                    )
                )
                return row.to_resource()
        except IntegrityError as error:
            if is_target_identity_conflict(error):
                raise EnvironmentManagementError(
                    "environment_target_conflict",
                    "This backend target already has an Environment owner.",
                    category=ErrorCategory.conflict,
                ) from error
            if is_evidence_unique_race(error):
                async with transaction(self.sessions) as session:
                    replay = await load_replay(
                        session,
                        actor=actor,
                        operation="environment.create",
                        scope_id=workspace_id,
                        identity=identity,
                        now=utc_now(),
                    )
                    if replay is not None:
                        await self.require_environment(session, actor, replay.result_ref)
                        return replay.restore(Environment)
            raise

    async def _register(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: RegisterEnvironmentRequest,
        now: datetime,
    ) -> EnvironmentRecord:
        provider = await self._provider(session, actor, request.provider_id)
        if (provider.workspace_id is not None and provider.workspace_id != workspace_id) or not provider.enabled:
            raise environment_not_found()
        implementation = self.catalog.require(provider.type)
        try:
            configuration = implementation.validate_configuration(
                schema_version=request.configuration_schema_version, value=request.configuration
            )
        except (ValidationError, EnvironmentProviderError) as error:
            raise invalid_environment("Environment registration configuration is invalid") from error
        state = request.state
        if provider.type in ENVD_PROVIDER_KEYS:
            if request.device_id is None or state is not None:
                raise invalid_environment("Envd registration requires device_id and does not accept Provider state")
            if request.configuration.get("working_directory") is not None:
                raise invalid_environment("Select the Device working directory when accepting an execution binding")
            state = EnvironmentState(
                provider_key=provider.type, state_version="1", state={"device_id": request.device_id}
            )
        elif request.device_id is not None:
            raise invalid_environment("Native Providers do not accept a Device identity")
        if state is not None and state.provider_key != provider.type:
            raise invalid_environment("Target state belongs to another Provider type")
        try:
            target_identity = implementation.target_identity(configuration=configuration, state=state)
        except (ValueError, EnvironmentProviderError) as error:
            raise invalid_environment("Environment registration state is invalid") from error
        target_identity = scoped_target_identity(implementation, provider.configuration, target_identity)
        environment_id = new_object_id("env")
        row = EnvironmentRecord(
            id=environment_id,
            name=request.name or default_environment_name(environment_id),
            labels=request.labels,
            organization_id=provider.organization_id,
            workspace_id=workspace_id,
            provider_id=provider.id,
            ownership="external",
            external_configuration={
                "configuration_schema_version": request.configuration_schema_version,
                "configuration": request.configuration,
            },
            state=state.model_dump(mode="json") if state else None,
            target_identity=target_identity,
            generation=1,
            status="unavailable",
            retention_condition="idle",
            condition_since=now,
            operation_generation=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        return row

    async def _provider(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        resource_id: str,
        *,
        manage: bool = False,
        lock: bool = False,
    ) -> EnvironmentProviderRecord:
        boundary = await environment_actor_scope(session, actor)
        query = select(EnvironmentProviderRecord).where(
            EnvironmentProviderRecord.id == resource_id,
            boundary.accessible(EnvironmentProviderRecord.organization_id, EnvironmentProviderRecord.workspace_id),
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise environment_not_found()
        await authorize_environment_resource(
            session,
            actor=actor,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            manage=manage,
            action=WorkspaceAction.environment_provider_manage if manage else WorkspaceAction.environment_provider_read,
        )
        return row

    async def _template(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        resource_id: str,
        *,
        manage: bool = False,
        lock: bool = False,
    ) -> EnvironmentTemplateRecord:
        boundary = await environment_actor_scope(session, actor)
        query = select(EnvironmentTemplateRecord).where(
            EnvironmentTemplateRecord.id == resource_id,
            boundary.accessible(EnvironmentTemplateRecord.organization_id, EnvironmentTemplateRecord.workspace_id),
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise environment_not_found()
        await authorize_environment_resource(
            session,
            actor=actor,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            manage=manage,
            action=WorkspaceAction.environment_template_manage if manage else WorkspaceAction.environment_template_read,
        )
        return row

    async def update_environment(
        self, *, actor: AuthenticatedActor, environment_id: str, request: UpdateEnvironmentRequest, if_match: str
    ) -> Environment:
        async with transaction(self.sessions) as session:
            row = await self.require_environment(session, actor, environment_id, lock=True)
            await authorize_environment_workspace(
                session, actor=actor, workspace_id=row.workspace_id, action=WorkspaceAction.environment_manage
            )
            self._match(row.id, row.updated_at, if_match)
            row.name = request.name
            row.updated_at = utc_now()
            return row.to_resource()

    async def require_environment(
        self, session: AsyncSession, actor: AuthenticatedActor, resource_id: str, *, lock: bool = False
    ) -> EnvironmentRecord:
        boundary = await environment_actor_scope(session, actor)
        query = select(EnvironmentRecord).where(
            EnvironmentRecord.id == resource_id,
            boundary.accessible(EnvironmentRecord.organization_id, EnvironmentRecord.workspace_id),
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise environment_not_found()
        await authorize_environment_workspace(
            session, actor=actor, workspace_id=row.workspace_id, action=WorkspaceAction.environment_read
        )
        return row

    async def get_template_labels(self, *, actor: AuthenticatedActor, template_id: str) -> tuple[LabelsBody, str]:
        async with short_session(self.sessions) as session:
            row = await self._template(session, actor, template_id)
            return LabelsBody(labels=row.labels), labels_etag(row.id, row.labels)

    async def replace_template_labels(
        self, *, actor: AuthenticatedActor, template_id: str, body: LabelsBody, if_match: str
    ) -> tuple[LabelsBody, str]:
        async with transaction(self.sessions) as session:
            row = await self._template(session, actor, template_id, manage=True, lock=True)
            current = labels_etag(row.id, row.labels)
            if not etag_matches(if_match, current):
                raise EnvironmentManagementError(
                    "labels_etag_mismatch",
                    "Labels changed since they were read.",
                    category=ErrorCategory.stale_version,
                    details={"current_etag": current},
                )
            if row.labels != body.labels:
                row.labels = dict(body.labels)
                row.updated_at = next_updated_at(row.updated_at, utc_now())
                session.add(
                    security_audit_record(
                        audit_id=new_object_id("audit"),
                        actor=actor,
                        organization_id=row.organization_id,
                        workspace_id=row.workspace_id,
                        action="environment_template.labels.update",
                        resource_type="environment_template",
                        resource_id=row.id,
                        outcome="success",
                        occurred_at=row.updated_at,
                        details=None,
                    )
                )
            return LabelsBody(labels=row.labels), labels_etag(row.id, row.labels)

    async def get_environment_labels(self, *, actor: AuthenticatedActor, environment_id: str) -> tuple[LabelsBody, str]:
        async with short_session(self.sessions) as session:
            row = await self.require_environment(session, actor, environment_id)
            return LabelsBody(labels=row.labels), labels_etag(row.id, row.labels)

    async def replace_environment_labels(
        self, *, actor: AuthenticatedActor, environment_id: str, body: LabelsBody, if_match: str
    ) -> tuple[LabelsBody, str]:
        async with transaction(self.sessions) as session:
            row = await self.require_environment(session, actor, environment_id, lock=True)
            await authorize_environment_workspace(
                session,
                actor=actor,
                workspace_id=row.workspace_id,
                action=WorkspaceAction.environment_manage,
            )
            current = labels_etag(row.id, row.labels)
            if not etag_matches(if_match, current):
                raise EnvironmentManagementError(
                    "labels_etag_mismatch",
                    "Labels changed since they were read.",
                    category=ErrorCategory.stale_version,
                    details={"current_etag": current},
                )
            if row.labels != body.labels:
                row.labels = dict(body.labels)
                row.updated_at = next_updated_at(row.updated_at, utc_now())
                session.add(
                    security_audit_record(
                        audit_id=new_object_id("audit"),
                        actor=actor,
                        organization_id=row.organization_id,
                        workspace_id=row.workspace_id,
                        action="environment.labels.update",
                        resource_type="environment",
                        resource_id=row.id,
                        outcome="success",
                        occurred_at=row.updated_at,
                        details=None,
                    )
                )
            return LabelsBody(labels=row.labels), labels_etag(row.id, row.labels)

    @staticmethod
    def _match(resource_id: str, updated_at: datetime, if_match: str) -> None:
        if not etag_matches(if_match, resource_etag(resource_id, updated_at)):
            raise EnvironmentManagementError(
                "precondition_failed", "The resource changed.", category=ErrorCategory.stale_version
            )

    async def get_provider(self, *, actor: AuthenticatedActor, resource_id: str) -> EnvironmentProvider:
        async with short_session(self.sessions) as session:
            return (await self._provider(session, actor, resource_id)).to_resource()

    async def list_providers(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, limit: int = 50, cursor: str | None = None
    ) -> Collection[EnvironmentProvider]:
        scope = {
            "collection": "providers",
            "workspace_id": workspace_id,
            "organization_boundary": actor.boundary_organization_id,
        }
        async with short_session(self.sessions) as session:
            owner = await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_provider_read
            )
            query = select(EnvironmentProviderRecord).where(
                owner.visible(EnvironmentProviderRecord.organization_id, EnvironmentProviderRecord.workspace_id)
            )
            if cursor is not None:
                query = query.where(EnvironmentProviderRecord.id > decode_cursor(cursor, scope=scope))
            rows = tuple(await session.scalars(query.order_by(EnvironmentProviderRecord.id).limit(limit + 1)))
            return Collection(
                items=tuple(row.to_resource() for row in rows[:limit]),
                next_cursor=encode_cursor(rows[limit - 1].id, scope=scope) if len(rows) > limit else None,
            )

    async def get_template(self, *, actor: AuthenticatedActor, resource_id: str) -> EnvironmentTemplate:
        async with short_session(self.sessions) as session:
            return (await self._template(session, actor, resource_id)).to_resource()

    async def list_templates(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> Collection[EnvironmentTemplate]:
        scope = {
            "collection": "templates",
            "workspace_id": workspace_id,
            "organization_boundary": actor.boundary_organization_id,
            "labels": labels or {},
        }
        async with short_session(self.sessions) as session:
            owner = await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_template_read
            )
            query = select(EnvironmentTemplateRecord).where(
                owner.visible(EnvironmentTemplateRecord.organization_id, EnvironmentTemplateRecord.workspace_id)
            )
            query = query.where(*label_predicates(EnvironmentTemplateRecord.labels, labels or {}))
            if cursor is not None:
                query = query.where(EnvironmentTemplateRecord.id > decode_cursor(cursor, scope=scope))
            rows = tuple(await session.scalars(query.order_by(EnvironmentTemplateRecord.id).limit(limit + 1)))
            return Collection(
                items=tuple(row.to_resource() for row in rows[:limit]),
                next_cursor=encode_cursor(rows[limit - 1].id, scope=scope) if len(rows) > limit else None,
            )

    async def get_environment(self, *, actor: AuthenticatedActor, resource_id: str) -> EnvironmentDetail:
        async with short_session(self.sessions) as session:
            row = await self.require_environment(session, actor, resource_id)
            configuration = await load_configuration(session, row)
            provider = await session.get_one(EnvironmentProviderRecord, row.provider_id)
            implementation = self.catalog.get(provider.type) if row.ownership == "managed" else None
            return EnvironmentDetail(
                **row.to_resource().model_dump(),
                retention=configuration.retention if isinstance(configuration, TemplateConfiguration) else None,
                supports_stop=implementation.supports_stop if implementation else False,
                supports_destroy=implementation.supports_destroy if implementation else False,
            )

    async def list_environments(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int = 50,
        cursor: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> Collection[Environment]:
        scope = {"collection": "environments", "workspace_id": workspace_id, "labels": labels or {}}
        async with short_session(self.sessions) as session:
            await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_read
            )
            query = select(EnvironmentRecord).where(EnvironmentRecord.workspace_id == workspace_id)
            query = query.where(*label_predicates(EnvironmentRecord.labels, labels or {}))
            if cursor is not None:
                query = query.where(EnvironmentRecord.id > decode_cursor(cursor, scope=scope))
            rows = tuple(await session.scalars(query.order_by(EnvironmentRecord.id).limit(limit + 1)))
            return Collection(
                items=tuple(row.to_resource() for row in rows[:limit]),
                next_cursor=encode_cursor(rows[limit - 1].id, scope=scope) if len(rows) > limit else None,
            )

    async def get_revision(self, *, actor: AuthenticatedActor, revision_id: str) -> EnvironmentTemplateRevision:
        async with short_session(self.sessions) as session:
            row = await session.get(EnvironmentTemplateRevisionRecord, revision_id)
            if row is None:
                raise environment_not_found()
            await self._template(session, actor, row.template_id)
            return row.to_resource()

    async def list_revisions(
        self, *, actor: AuthenticatedActor, template_id: str, limit: int = 50, cursor: str | None = None
    ) -> Collection[EnvironmentTemplateRevision]:
        scope = {"collection": "revisions", "template_id": template_id, "workspace_id": actor.boundary_workspace_id}
        position = decode_cursor(cursor, scope=scope) if cursor else "0"
        if not position.isdecimal():
            raise invalid_environment("Revision cursor is invalid")
        async with short_session(self.sessions) as session:
            await self._template(session, actor, template_id)
            rows = tuple(
                await session.scalars(
                    select(EnvironmentTemplateRevisionRecord)
                    .where(
                        EnvironmentTemplateRevisionRecord.template_id == template_id,
                        EnvironmentTemplateRevisionRecord.version > int(position),
                    )
                    .order_by(EnvironmentTemplateRevisionRecord.version)
                    .limit(limit + 1)
                )
            )
            return Collection(
                items=tuple(row.to_resource() for row in rows[:limit]),
                next_cursor=encode_cursor(str(rows[limit - 1].version), scope=scope) if len(rows) > limit else None,
            )

    async def request_command(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        request: EnvironmentCommandRequest,
        idempotency_key: str,
    ) -> EnvironmentCommand:
        from .retention import has_active_use

        now = utc_now()
        identity = request_identity(idempotency_key, request)
        async with transaction(self.sessions) as session:
            environment = await self.require_environment(session, actor, environment_id)
            await authorize_environment_workspace(
                session, actor=actor, workspace_id=environment.workspace_id, action=WorkspaceAction.environment_manage
            )
            replay = await load_replay(
                session,
                actor=actor,
                operation="environment.command",
                scope_id=environment.id,
                identity=identity,
                now=now,
            )
            if replay:
                command = await session.get(EnvironmentCommandRecord, replay.result_ref)
                if command is None:
                    raise environment_not_found()
                return replay.restore(EnvironmentCommand)
            environment = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment.id).with_for_update()
            )
            assert environment is not None
            if (
                environment.ownership != "managed"
                or environment.operation_id
                or await has_active_use(session, environment.id)
            ):
                raise EnvironmentManagementError(
                    "environment_busy",
                    "Environment is in use, externally owned, or has pending work.",
                    category=ErrorCategory.conflict,
                )
            provider = await session.get(EnvironmentProviderRecord, environment.provider_id)
            if provider is None:
                raise environment_not_found()
            implementation = self.catalog.require(provider.type)
            supported = implementation.supports_stop if request.action == "stop" else implementation.supports_destroy
            if not supported:
                raise invalid_environment("Provider does not support this lifecycle action")
            command = EnvironmentCommandRecord(
                id=new_object_id("envop"),
                environment_id=environment.id,
                action=request.action,
                principal_type=actor.principal.principal_type.value,
                principal_id=actor.principal.principal_id,
                status="pending",
                created_at=now,
            )
            session.add(command)
            environment.operation_id = command.id
            environment.operation_action = request.action
            environment.next_maintenance_at = now
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=environment.organization_id,
                    workspace_id=environment.workspace_id,
                    operation="environment.command",
                    scope_id=environment.id,
                    identity=identity,
                    result_kind="environment_command",
                    result_ref=command.id,
                    now=now,
                    response=command.to_resource(),
                )
            )
            return command.to_resource()

    async def get_command(self, *, actor: AuthenticatedActor, command_id: str) -> EnvironmentCommand:
        async with short_session(self.sessions) as session:
            command = await session.get(EnvironmentCommandRecord, command_id)
            if command is None:
                raise environment_not_found()
            await self.require_environment(session, actor, command.environment_id)
            return command.to_resource()
