"""Workspace Run authority admits shared clients without granting registration management."""

from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.claim import claim
from a13n_service.runs.environments.clients.authority import resolve
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.service import authenticate, validate_attachment
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.tenancy.tables import WorkspaceRow

from .conftest import fresh_key, message
from .test_client_environments import register
from .test_platform_tools import set_role
from .test_tenancy import join, new_client

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["user", "service_account"])
async def test_other_runner_can_select_and_execute_client_but_cannot_manage_it(service, runs_kit, scripted_model, kind):
    await runs_kit.pause_sweeps(service)
    created = (await register(service)).json()
    identity = created["client"]["environment"]["id"]
    attachment = await authenticate(service.runtime, identity, created["token"])
    agent = await runs_kit.create_agent(service, scripted_model)
    async with AsyncExitStack() as stack:
        if kind == "user":
            client = await join(service, stack, "runner@example.com", "runner")
        else:
            account = await service.client.post(f"{service.workspace}/service-accounts", json={"name": "runner"})
            assert account.status_code == 201, account.text
            key = await service.client.post(
                f"{service.workspace}/service-accounts/{account.json()['id']}/keys", json={"name": "run"}
            )
            assert key.status_code == 201, key.text
            client = await stack.enter_async_context(
                new_client(service, authorization="Bearer " + key.json()["secret"])
            )
        client.headers["x-workspace-id"] = service.tenant.workspace_id
        principal = (await client.get("/api/v1/users/me")).json()
        assert principal["kind"] == kind and principal["id"] != service.tenant.principal_id
        assert (await client.get(service.workspace)).json()["permissions"] == ["read", "run"]
        payload = message(agent, "Use shared computer", environments=[{"name": "pc", "environment_id": identity}])
        started = await client.post(f"{service.api}/threads", json=payload, headers=fresh_key())
        assert started.status_code == 201, started.text
        assert started.json()["run"]["environment_mounts"][0]["environment_id"] == identity
        [lease] = await claim(service.runtime, worker_id="shared-client-test", worker_build="test", limit=1)
        request = Request(
            environment_id=identity,
            mount_name="pc",
            epoch="a" * 32,
            scope_id=uuid4().hex,
            thread_id=lease.thread_id,
            caller=Caller(run_id=lease.run_id, attempt_id=lease.attempt_id, worker_id=lease.worker_id),
            method="file.read",
            params={},
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        actions = frozenset({"environment.file.read_text"})
        mount = await resolve(service.runtime, request, attachment, actions)
        assert mount.environment_id == identity
        # The registration ceiling still refuses desktop access.
        with pytest.raises(EnvironmentError, match="no longer authorized"):
            await resolve(service.runtime, request, attachment, frozenset({"environment.computer.observe"}))
        # Run permission does not permit changing or issuing device credentials.
        for operation in ("rotate-credential", "revoke-credential"):
            denied = await client.post(
                f"{service.api}/client-environments/{identity}/{operation}",
                headers={"if-match": f'"{identity}:1"'},
            )
            assert denied.status_code == 403, denied.text
        denied = await client.post(
            f"{service.api}/client-environments",
            json={"device_id": "other", "name": "Other", "default_working_directory": "/work"},
        )
        assert denied.status_code == 403, denied.text
        outside = await service.client.post(f"{service.organization}/workspaces", json={"name": "Outside"})
        assert outside.status_code == 201, outside.text
        foreign = await service.client.post(
            "/api/v1/client-environments",
            headers={"x-workspace-id": outside.json()["id"]},
            json={"device_id": "foreign", "name": "Foreign", "default_working_directory": "/work"},
        )
        assert foreign.status_code == 201, foreign.text
        foreign_id = foreign.json()["client"]["environment"]["id"]
        refused = await client.post(
            f"{service.api}/threads",
            json=message(agent, "Cross workspace", environments=[{"name": "pc", "environment_id": foreign_id}]),
            headers=fresh_key(),
        )
        assert refused.status_code == 404, refused.text
        assert refused.json()["error"]["code"] == "not_found"
        # Removing Run authority rejects both future acceptance and an already admitted dispatch.
        grants = (await service.client.get(f"{service.workspace}/grants")).json()["items"]
        [grant] = [item for item in grants if item["principal"]["id"] == principal["id"]]
        changed = await service.client.patch(f"{service.workspace}/grants/{grant['id']}", json={"role": "viewer"})
        assert changed.status_code == 200, changed.text
        with pytest.raises(EnvironmentError, match="no longer authorized"):
            await resolve(service.runtime, request, attachment, actions)
        denied = await client.post(f"{service.api}/threads", json=payload, headers=fresh_key())
        assert denied.status_code == 403, denied.text
        print(f"Shared client: {kind} with Run can execute; management, cross-workspace and revoked Run denied")


async def test_device_connection_survives_registrant_role_change_but_not_workspace_archive(service):
    created = (await register(service)).json()
    identity = created["client"]["environment"]["id"]
    attachment = await authenticate(service.runtime, identity, created["token"])
    async with transaction(service.runtime.storage) as session:
        await set_role(session, service, "viewer")
    assert await authenticate(service.runtime, identity, created["token"]) == attachment
    await validate_attachment(service.runtime, attachment)
    async with transaction(service.runtime.storage) as session:
        workspace = await session.get_one(WorkspaceRow, service.tenant.workspace_id)
        workspace.archived_at = datetime.now(UTC)
    for operation in (
        authenticate(service.runtime, identity, created["token"]),
        validate_attachment(service.runtime, attachment),
    ):
        with pytest.raises(ServiceError) as error:
            await operation
        assert error.value.code == "disabled"
