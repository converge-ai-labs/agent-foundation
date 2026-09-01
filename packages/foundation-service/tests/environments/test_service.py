from __future__ import annotations

from pathlib import Path

import pytest
from a13n_environment_provider import (
    Environment,
    EnvironmentProvider,
    EnvironmentState,
    build_environment_provider_catalog,
)
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.domain import (
    CreateEnvironmentRequest,
    CreateEnvironmentRevisionRequest,
    PatchEnvironmentRequest,
    PutEnvironmentProviderSelectionRequest,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentRecord, EnvironmentRevisionRecord
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.storage import transaction
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import SECRET_ID, WORKSPACE_ID, actor

PROVIDER_KEY = "a13n.direct-local"


class _ThirdPartyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str


class _ThirdPartyProvider(EnvironmentProvider):
    @property
    def key(self) -> str:
        return "acme.remote-workspace"

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({"2026-09"})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != "2026-09":
            raise ValueError("unsupported test schema")
        return _ThirdPartyConfig.model_validate(value)

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        del configuration, state, runtime
        raise AssertionError("management validation must not construct a runtime adapter")


def candidate(root: Path, *, environment_id: str = "workspace-local") -> CreateEnvironmentRequest:
    return CreateEnvironmentRequest.model_validate(
        {
            "name": "Local Workspace",
            "provider": {
                "provider_key": PROVIDER_KEY,
                "schema_version": "1",
                "configuration": {
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
    tmp_path: Path,
) -> None:
    catalog = await environment_service.list_provider_catalog(actor=actor())
    assert [item.provider_key for item in catalog.items] == [PROVIDER_KEY]
    assert catalog.items[0].configuration_versions == ("1",)
    assert catalog.items[0].provider_lock.distribution_name == "a13n-environment-provider"

    selected = await environment_service.put_provider_selection(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_key=PROVIDER_KEY,
        request=PutEnvironmentProviderSelectionRequest(enabled=True),
    )
    assert selected.enabled
    assert selected.version == 1

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
    assert first.provider.configuration["root"]["path"] == str(tmp_path)

    unchanged = await environment_service.create_revision(
        actor=actor(),
        environment_id=created.id,
        idempotency_key="revision-noop",
        request=CreateEnvironmentRevisionRequest(
            expected_environment_version=1,
            provider=candidate(tmp_path).provider,
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
            expected_environment_version=1,
            provider=candidate(tmp_path, environment_id="workspace-local-v2").provider,
            credential_bindings=candidate(tmp_path).credential_bindings,
            access="read_write",
        ),
    )
    assert second_result.created
    assert second_result.revision.revision_number == 2
    updated = await environment_service.get(actor=actor(), environment_id=created.id)
    assert updated.current_revision_id == second_result.revision.id
    assert updated.version == 2

    archived = await environment_service.patch(
        actor=actor(),
        environment_id=created.id,
        request=PatchEnvironmentRequest(expected_version=2, archived=True),
    )
    assert archived.archived_at is not None
    assert (
        await environment_service.list(
            actor=actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None, include_archived=False
        )
    ).items == ()


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
                    "provider": {
                        "provider_key": PROVIDER_KEY,
                        "schema_version": "1",
                        "configuration": {"environment_id": "bad", "root": {"path": "relative"}},
                    },
                }
            ),
        )
    assert invalid.value.code == "provider_spec_invalid"
    async with transaction(environment_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(EnvironmentRevisionRecord)) == 0


@pytest.mark.anyio
async def test_provider_selection_requires_compare_and_swap(
    environment_service: EnvironmentManagementService,
) -> None:
    first = await environment_service.put_provider_selection(
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
            request=PutEnvironmentProviderSelectionRequest(enabled=False, expected_version=first.version + 1),
        )
    assert stale.value.code == "environment_provider_version_conflict"


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
async def test_third_party_provider_uses_shared_catalog_without_foundation_specific_code(
    environment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    catalog = FoundationEnvironmentProviderCatalog(
        build_environment_provider_catalog(explicit_providers=(_ThirdPartyProvider(),))
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
                "provider": {
                    "provider_key": "acme.remote-workspace",
                    "schema_version": "2026-09",
                    "configuration": {"workspace": "tenant-one"},
                },
            }
        ),
    )
    revision = await service.get_revision(actor=actor(), revision_id=created.current_revision_id)

    assert not selected.provider_lock.builtin
    assert selected.provider_lock.distribution_name is None
    assert revision.provider.configuration == {"workspace": "tenant-one"}
    assert revision.provider_lock == selected.provider_lock
