"""Installed typed Environment definitions use actual persisted management and lifecycle."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.environments.configuration import load_configuration
from a13n_service.environments.domain import (
    CreateManagedEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
    CreateTemplateRevisionRequest,
)
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.service import EnvironmentService
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage import short_session, transaction
from anyio import run_process

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def test_installed_environment_management_native_operations_and_frozen_reuse(
    environment_sessions, protector, tmp_path, monkeypatch
):
    installed = tmp_path / "installed"
    await run_process(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(Path(__file__).resolve().parents[4] / "examples/provider-plugin"),
        ]
    )
    monkeypatch.syspath_prepend(str(installed))
    catalog = load_provider_catalogs(("acme",)).environment
    service = EnvironmentService(environment_sessions, catalog, protector)
    backend = await service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest.model_validate(
            {"type": "acme_workspace", "name": "Projects", "configuration": {"root": str(tmp_path / "workspaces")}}
        ),
    )
    template = await service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="template",
        request=CreateTemplateRequest.model_validate(
            {
                "name": "Notes",
                "provider_id": backend.id,
                "configuration": {"directory": "notes"},
                "retention": {"idle": {"stop_after": None, "delete_after": None}},
            }
        ),
    )
    instance = await service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    lifecycle = EnvironmentLifecycle(environment_sessions, catalog, protector)

    async def prepare():
        async with transaction(environment_sessions) as session:
            row = await session.get(EnvironmentRecord, instance.id)
            provider = await session.get(EnvironmentProviderRecord, backend.id)
            config = await load_configuration(session, row)
            operation = lifecycle._claim(row, provider, config, "prepare", datetime.now(UTC))
        return await lifecycle.execute(operation)

    first = await prepare()
    assert first.environment.provider_key == "acme_workspace"
    await first.environment.operations.files.write_text("/proof.txt", "preserved across uses", mode="create")
    await first.environment.close()
    await service.create_revision(
        actor=actor(),
        template_id=template.id,
        request=CreateTemplateRevisionRequest.model_validate(
            {
                "expected_version": template.version,
                "provider_id": backend.id,
                "configuration": {"directory": "new-projects"},
                "retention": {"idle": {"stop_after": None, "delete_after": None}},
            }
        ),
    )
    second = await prepare()
    try:
        assert second.generation == first.generation
        assert (await second.environment.operations.files.read_text("/proof.txt")).text == "preserved across uses"
        assert (tmp_path / "workspaces" / "notes" / instance.id / "proof.txt").exists()
        assert not (tmp_path / "workspaces" / "new-projects").exists()
    finally:
        await second.environment.close()
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, instance.id)
        assert row.status == "running"
        assert (await load_configuration(session, row)).configuration == {"directory": "notes"}
