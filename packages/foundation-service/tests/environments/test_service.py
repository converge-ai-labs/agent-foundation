from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalProviderRuntime,
    Environment,
    build_environment_provider_catalog,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.environments.catalog import (
    FoundationEnvironmentProviderCatalog,
    FoundationEnvironmentProviderRegistration,
)
from a13n_service.environments.domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    EnvironmentProviderLock,
    PutEnvironmentProviderSelectionRequest,
    UpdateEnvironmentRequest,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import (
    EnvironmentRecord,
    EnvironmentRevisionRecord,
    EnvironmentTargetRecord,
)
from a13n_service.environments.providers import FoundationBuiltinEnvironmentProviderAdapter
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.environments.testing import NativeEnvironmentAttachmentTester
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.models import RoleBindingRecord, WorkspaceRecord
from a13n_service.secrets.domain import SecretCredentialSource
from a13n_service.storage import transaction
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, SECRET_ID, WORKSPACE_ID, actor

PROVIDER_KEY = "a13n.direct-local"


class _SecretResolver:
    async def resolve(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        credential: SecretCredentialSource,
    ) -> str:
        del actor, organization_id, workspace_id, credential
        return "fresh-secret-value"


async def _runtime_builder(*, provider_key: str, credentials: Mapping[str, str]) -> object:
    assert provider_key == PROVIDER_KEY
    assert credentials == {"token": "fresh-secret-value"}
    return DirectLocalProviderRuntime()


class _ThirdPartyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str


class _ThirdPartyProvider:
    @property
    def provider_key(self) -> str:
        return "acme.remote-workspace"

    @property
    def connection_versions(self) -> frozenset[str]:
        return frozenset({"2026-09"})

    @property
    def identity_schema_version(self) -> str:
        return "1"

    @property
    def retention_behavior(self) -> str:
        return "none"

    def validate_connection(self, *, schema_version: str, parameters: JsonValue) -> BaseModel:
        if schema_version != "2026-09":
            raise ValueError("unsupported test schema")
        return _ThirdPartyConfig.model_validate(parameters)

    def target_identity(self, *, connection: BaseModel) -> object:
        return {
            "namespace": {},
            "target_key": _ThirdPartyConfig.model_validate(connection).workspace,
        }

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment:
        del connection, runtime
        raise AssertionError("management validation must not construct a runtime adapter")


class _BrokenRetainingProvider(_ThirdPartyProvider):
    @property
    def retention_behavior(self) -> str:
        return "while_execution_active"


def test_foundation_owns_builtin_target_identity_and_retention_metadata(tmp_path: Path) -> None:
    shared_catalog = build_environment_provider_catalog(builtin_keys=(PROVIDER_KEY,))
    shared_provider = shared_catalog[PROVIDER_KEY]
    connection = shared_provider.validate_connection(
        schema_version="1",
        parameters=candidate(tmp_path).connection.parameters,
    )

    assert hasattr(shared_provider, "target_key")
    assert not hasattr(shared_provider, "target_identity")
    assert not hasattr(shared_provider, "retention_behavior")

    foundation_catalog = FoundationEnvironmentProviderCatalog.from_environment_provider_catalog(shared_catalog)
    adapter = foundation_catalog.attachment(PROVIDER_KEY)

    assert isinstance(adapter, FoundationBuiltinEnvironmentProviderAdapter)
    assert adapter.identity_schema_version == "1"
    assert adapter.retention_behavior == "none"
    assert adapter.target_identity(connection=connection) == {
        "namespace": {},
        "target_key": str(tmp_path.resolve()),
    }


def candidate(root: Path, *, environment_id: str = "workspace-local") -> CreateEnvironmentRequest:
    return CreateEnvironmentRequest.model_validate(
        {
            "name": "Local Workspace",
            "connection": {
                "provider_key": PROVIDER_KEY,
                "schema_version": "1",
                "parameters": {
                    "environment_id": environment_id,
                    "root": {"path": str(root), "read_only": False},
                },
            },
            "credential_bindings": [
                {
                    "requirement_key": "token",
                    "credential": {"source": "workspace_secret", "secret_id": SECRET_ID},
                }
            ],
            "access": "full",
        }
    )


@pytest.mark.anyio
async def test_provider_selection_environment_and_revision_lifecycle(
    environment_service: EnvironmentManagementService,
    environment_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    catalog = await environment_service.list_provider_catalog(actor=actor())
    assert [item.provider_key for item in catalog.items] == [PROVIDER_KEY]
    assert catalog.items[0].connection_versions == ("1",)
    assert catalog.items[0].identity_schema_version == "1"
    assert catalog.items[0].retention_behavior == "none"
    assert catalog.items[0].provider_lock.distribution_name == "a13n-environment-provider"

    selected = await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    assert selected.enabled

    created = await environment_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-local",
        request=candidate(tmp_path),
    )
    replay = await environment_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-local",
        request=candidate(tmp_path),
    )
    assert replay == created
    assert created.version == 1

    revisions = await environment_service.list_revisions(
        actor=actor(), environment_id=created.id, limit=10, cursor=None
    )
    assert len(revisions.items) == 1
    first = revisions.items[0]
    assert first.id == created.current_revision_id
    assert first.provider_key == PROVIDER_KEY
    assert "connection" not in first.model_dump(mode="json")
    detail = await environment_service.get_revision(actor=actor(), revision_id=first.id)
    assert detail.connection.parameters["root"]["path"] == str(tmp_path)
    assert detail.target_key == str(tmp_path.resolve())
    assert detail.environment_target_id.startswith("envt_")

    unchanged = await environment_service.create_revision(
        actor=actor(),
        environment_id=created.id,
        idempotency_key="revision-noop",
        request=CreateEnvironmentRevisionRequest(
            expected_version=1,
            connection=candidate(tmp_path).connection,
            credential_bindings=candidate(tmp_path).credential_bindings,
            access="full",
        ),
    )
    assert not unchanged.created
    assert unchanged.revision.id == first.id

    second_result = await environment_service.create_revision(
        actor=actor(),
        environment_id=created.id,
        idempotency_key="revision-two",
        request=CreateEnvironmentRevisionRequest(
            expected_version=1,
            connection=candidate(tmp_path, environment_id="workspace-local-v2").connection,
            credential_bindings=candidate(tmp_path).credential_bindings,
            access="read_write",
        ),
    )
    assert second_result.created
    assert second_result.revision.version == 2
    assert second_result.revision.environment_target_id == detail.environment_target_id
    updated = await environment_service.get(actor=actor(), environment_id=created.id)
    assert updated.current_revision_id == second_result.revision.id
    assert updated.version == 2
    async with transaction(environment_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentTargetRecord)) == 1

    archived = await environment_service.patch(
        actor=actor(),
        environment_id=created.id,
        if_match=resource_etag(updated.id, updated.updated_at),
        request=UpdateEnvironmentRequest(archived=True),
    )
    assert archived.archived_at is not None
    assert (
        await environment_service.list(
            actor=actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None, include_archived=False
        )
    ).items == ()


@pytest.mark.anyio
async def test_revision_attachment_test_uses_fresh_attach_only_adapter(
    environment_sessions: async_sessionmaker[AsyncSession],
    provider_catalog: FoundationEnvironmentProviderCatalog,
    tmp_path: Path,
) -> None:
    tester = NativeEnvironmentAttachmentTester(
        provider_catalog,
        secret_resolver=_SecretResolver(),
        runtime_builder=_runtime_builder,
    )
    service = EnvironmentManagementService(
        environment_sessions,
        provider_catalog,
        clock=lambda: NOW,
        attachment_tester=tester,
    )
    await service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    created = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="attachment-test",
        request=candidate(tmp_path),
    )

    result = await service.test_revision(actor=actor(), revision_id=created.current_revision_id)

    assert result.code == "attachment_ready"


@pytest.mark.anyio
async def test_disabled_provider_and_invalid_configuration_fail_atomically(
    environment_service: EnvironmentManagementService,
    environment_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=False),
    )
    with pytest.raises(EnvironmentManagementError) as disabled:
        await environment_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="disabled",
            request=candidate(tmp_path),
        )
    assert disabled.value.code == "environment_provider_disabled"

    with pytest.raises(EnvironmentManagementError) as invalid:
        await environment_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="invalid",
            request=CreateEnvironmentRequest.model_validate(
                {
                    "name": "Invalid",
                    "connection": {
                        "provider_key": PROVIDER_KEY,
                        "schema_version": "1",
                        "parameters": {"environment_id": "bad", "root": {"path": "relative"}},
                    },
                }
            ),
        )
    assert invalid.value.code == "provider_connection_invalid"
    async with transaction(environment_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(EnvironmentRevisionRecord)) == 0


@pytest.mark.anyio
async def test_provider_selection_requires_compare_and_swap(
    environment_service: EnvironmentManagementService,
) -> None:
    await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    with pytest.raises(EnvironmentManagementError) as stale:
        await environment_service.put_provider_selection(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_key=PROVIDER_KEY,
            if_match='"stale"',
            request=PutEnvironmentProviderSelectionRequest(enabled=False),
        )
    assert stale.value.code == "precondition_failed"


@pytest.mark.anyio
async def test_environment_create_rejects_idempotency_key_reuse_with_different_request(
    environment_service: EnvironmentManagementService,
    tmp_path: Path,
) -> None:
    await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    await environment_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="reused-key",
        request=candidate(tmp_path),
    )

    with pytest.raises(EnvironmentManagementError) as conflict:
        await environment_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="reused-key",
            request=candidate(tmp_path, environment_id="different"),
        )
    assert conflict.value.code == "idempotency_conflict"


@pytest.mark.anyio
async def test_expired_environment_evidence_allows_reusing_the_key(
    environment_service: EnvironmentManagementService,
    environment_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    first = await environment_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-environment-key",
        request=candidate(tmp_path),
    )
    async with transaction(environment_sessions) as session:
        evidence = await session.scalar(
            select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "environment.create")
        )
        assert evidence is not None
        evidence.created_at = NOW - timedelta(hours=24)
        evidence.expires_at = NOW

    request = candidate(tmp_path, environment_id="second").model_copy(update={"name": "Second Workspace"})
    second = await environment_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-environment-key",
        request=request,
    )

    assert second.id != first.id


@pytest.mark.anyio
async def test_third_party_provider_attachment_capability_is_registered(
    environment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    catalog = FoundationEnvironmentProviderCatalog(
        (
            FoundationEnvironmentProviderRegistration(
                provider=_ThirdPartyProvider(),
                provider_lock=EnvironmentProviderLock(
                    provider_key="acme.remote-workspace",
                    distribution_name="acme-environment-provider",
                    distribution_version="1.0.0",
                    builtin=False,
                    registration_digest_sha256="f" * 64,
                ),
            ),
        )
    )
    service = EnvironmentManagementService(environment_sessions, catalog)
    selected = await service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key="acme.remote-workspace",
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    created = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="third-party-provider",
        request=CreateEnvironmentRequest.model_validate(
            {
                "name": "Remote Workspace",
                "connection": {
                    "provider_key": "acme.remote-workspace",
                    "schema_version": "2026-09",
                    "parameters": {"workspace": "tenant-one"},
                },
            }
        ),
    )
    revision = await service.get_revision(actor=actor(), revision_id=created.current_revision_id)

    assert not selected.provider_lock.builtin
    assert selected.provider_lock.distribution_name == "acme-environment-provider"
    assert revision.connection.parameters == {"workspace": "tenant-one"}
    assert revision.target_key == "tenant-one"
    assert revision.provider_lock == selected.provider_lock


def test_retaining_provider_requires_the_bounded_retention_capability() -> None:
    with pytest.raises(ValueError, match="ensure_retained_until"):
        FoundationEnvironmentProviderCatalog(
            (
                FoundationEnvironmentProviderRegistration(
                    provider=_BrokenRetainingProvider(),
                    provider_lock=EnvironmentProviderLock(
                        provider_key="acme.remote-workspace",
                        distribution_name="acme-environment-provider",
                        distribution_version="1.0.0",
                        builtin=False,
                        registration_digest_sha256="f" * 64,
                    ),
                ),
            )
        )


@pytest.mark.anyio
async def test_target_identity_is_deduplicated_across_workspaces_without_tenant_fields(
    environment_service: EnvironmentManagementService,
    environment_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    second_workspace_id = "ws_env22222222222222"
    second_actor = replace(actor(), boundary_workspace_id=second_workspace_id)
    async with transaction(environment_sessions) as session:
        session.add(
            WorkspaceRecord(
                id=second_workspace_id,
                organization_id=ORG_ID,
                name="Second",
                normalized_name="second",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            RoleBindingRecord(
                id="rb_envws22222222222",
                organization_id=ORG_ID,
                workspace_id=second_workspace_id,
                principal_type="user",
                principal_id=actor().principal.principal_id,
                resource_type="workspace",
                resource_id=second_workspace_id,
                role_key="builder",
                created_by_user_id=actor().principal.principal_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    first_target_id: str | None = None
    for current_actor, workspace_id, key in (
        (actor(), WORKSPACE_ID, "first-workspace"),
        (second_actor, second_workspace_id, "second-workspace"),
    ):
        await environment_service.put_provider_selection(
            actor=current_actor,
            workspace_id=workspace_id,
            provider_key=PROVIDER_KEY,
            request=PutEnvironmentProviderSelectionRequest(enabled=True),
        )
        created = await environment_service.create(
            actor=current_actor,
            workspace_id=workspace_id,
            idempotency_key=key,
            request=candidate(tmp_path).model_copy(update={"credential_bindings": ()}),
        )
        revision = await environment_service.get_revision(
            actor=current_actor,
            revision_id=created.current_revision_id,
        )
        if workspace_id == WORKSPACE_ID:
            first_target_id = revision.environment_target_id
        else:
            assert first_target_id is not None and revision.environment_target_id == first_target_id
    async with transaction(environment_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentTargetRecord)) == 1
