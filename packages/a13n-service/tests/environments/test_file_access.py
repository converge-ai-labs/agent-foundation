"""Retained file access reconnects stateless targets without provisioning storage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_service.environments.configuration import load_configuration
from a13n_service.environments.domain import (
    CreateManagedEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
)
from a13n_service.environments.file_access import ExistingEnvironmentFiles
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentFileUseRecord, EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now
from sqlalchemy import select

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
async def retained_files(environment_service, environment_sessions, provider_catalog, protector, tmp_path):
    provider = await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="direct_local", name="Local")
    )
    template = await environment_service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="file-access-template",
        request=CreateTemplateRequest(
            name="Files",
            provider_id=provider.id,
            configuration={"root": {"path": str(tmp_path)}},
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    environment = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="file-access-environment",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        backend = await session.get(EnvironmentProviderRecord, provider.id)
        configuration = await load_configuration(session, row)
        operation = lifecycle._claim(row, backend, configuration, "prepare", utc_now())
    result = await lifecycle.execute(operation)
    await result.environment.close()
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "running" and row.state is None
        backing = f"{row.id}:{row.generation}"
    return SimpleNamespace(
        access=ExistingEnvironmentFiles(lifecycle),
        environment_id=environment.id,
        provider_id=provider.id,
        backing=backing,
        root=tmp_path / "environments" / environment.id,
        sessions=environment_sessions,
    )


async def test_stateless_environment_can_reopen_retained_files(retained_files):
    retained = retained_files
    authorize = AsyncMock()
    for write in (True, False):
        async with retained.access.open(
            actor=actor(),
            environment_id=retained.environment_id,
            backing_identity=retained.backing,
            authorize=authorize,
        ) as files:
            if write:
                await files.write_text("/memory-proof", "retained decision", mode="create")
            assert await files.read_bytes("/memory-proof") == b"retained decision"
        async with short_session(retained.sessions) as session:
            assert list(await session.scalars(select(EnvironmentFileUseRecord))) == []
    assert authorize.await_count == 6


@pytest.mark.parametrize("change", ["generation", "stopped", "provider_disabled", "operation"])
async def test_retained_file_access_rejects_changed_authority(retained_files, change):
    retained = retained_files
    async with transaction(retained.sessions) as session:
        row = await session.get(EnvironmentRecord, retained.environment_id)
        if change == "generation":
            row.generation += 1
        elif change == "stopped":
            row.status = "stopped"
        elif change == "provider_disabled":
            provider = await session.get(EnvironmentProviderRecord, retained.provider_id)
            provider.enabled = False
        else:
            row.operation_id = "envop_other"
    with pytest.raises(ValueError, match="Memory Environment is unavailable"):
        async with retained.access.open(
            actor=actor(),
            environment_id=retained.environment_id,
            backing_identity=retained.backing,
            authorize=AsyncMock(),
        ):
            pytest.fail("Changed Environment authority must not grant file access")


async def test_missing_local_root_is_not_recreated_and_lease_is_released(retained_files):
    retained = retained_files
    retained.root.rmdir()
    with pytest.raises(EnvironmentError) as failure:
        async with retained.access.open(
            actor=actor(),
            environment_id=retained.environment_id,
            backing_identity=retained.backing,
            authorize=AsyncMock(),
        ):
            pytest.fail("Missing storage must not be recreated")
    assert failure.value.code == "environment_not_found"
    assert not retained.root.exists()
    async with short_session(retained.sessions) as session:
        assert list(await session.scalars(select(EnvironmentFileUseRecord))) == []
