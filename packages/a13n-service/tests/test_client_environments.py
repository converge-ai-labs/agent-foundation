"""Offline registration, private device bindings and explicit credential revocation."""

import asyncio

import pytest
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.environments.clients import service as clients
from a13n_service.runs.environments.clients.schemas import Registration, StatusChange
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import func, select

from .environments_support import DEFAULT_SESSION
from .test_metrics_queries import builder

pytestmark = pytest.mark.anyio


async def register(service, device_id="laptop"):
    return await service.client.post(
        f"{service.api}/client-environments",
        json={"device_id": device_id, "name": "Laptop", "default_working_directory": "/work"},
    )


async def test_offline_registration_is_idempotent_and_keeps_secrets_private(service):
    responses = await asyncio.gather(*(register(service) for _ in range(3)))
    assert sorted(response.status_code for response in responses) == [200, 200, 201]
    created = next(response for response in responses if response.status_code == 201)
    body = created.json()
    environment = body["client"]["environment"]
    identity = environment["id"]
    assert environment["kind"] == "external" and environment["status"] == "ready"
    assert environment["endpoint"] is None and environment["device_id"] == "laptop"
    assert environment["owner_principal_id"] == service.tenant.principal_id
    assert body["connection_path"] == f"/api/v1/client-environments/{identity}/connect"
    assert body["token"]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["client"]["environment"]["id"] == identity
        if response.status_code == 200:
            assert response.json()["token"] is None
    read = await service.client.get(f"{service.api}/client-environments/{identity}")
    assert read.status_code == 200 and read.json()["credential_present"]
    assert read.json()["environment"]["client_connection"]["state"] == "offline"
    assert body["token"] not in read.text and "token_hash" not in read.text
    async with short_session(service.runtime.storage) as session:
        config = await session.get_one(ClientSettingsRow, identity)
        assert config.token_hash == secret_hash(body["token"])
        assert config.credential_version == 1
        assert await session.scalar(select(func.count()).select_from(ClientSettingsRow)) == 1
        assert await session.scalar(select(func.count()).select_from(EnvironmentRow)) == 1
    attachment = await clients.authenticate(service.runtime, identity, body["token"])
    assert attachment.device_id == "laptop" and attachment.credential_version == 1
    listed = (await service.client.get(f"{service.api}/environments")).json()["items"]
    assert [item["id"] for item in listed] == [identity]


async def test_status_rotation_and_revocation_fence_existing_connections(service):
    created = await register(service)
    assert created.status_code == 201, created.text
    body = created.json()
    identity = body["client"]["environment"]["id"]
    path = f"{service.api}/client-environments/{identity}"
    first = await clients.authenticate(service.runtime, identity, body["token"])
    disabled = await service.client.patch(
        f"{path}/status", json={"status": "disabled"}, headers={"if-match": created.headers["etag"]}
    )
    assert disabled.status_code == 200, disabled.text
    with pytest.raises(ServiceError):
        await clients.authenticate(service.runtime, identity, body["token"])
    duplicate = await register(service)
    assert duplicate.status_code == 200 and duplicate.json()["token"] is None
    assert duplicate.json()["client"]["environment"]["status"] == "disabled"
    stale = await service.client.patch(
        f"{path}/status", json={"status": "ready"}, headers={"if-match": created.headers["etag"]}
    )
    assert stale.status_code == 412
    enabled = await service.client.patch(
        f"{path}/status", json={"status": "ready"}, headers={"if-match": disabled.headers["etag"]}
    )
    assert enabled.status_code == 200, enabled.text
    current = await clients.authenticate(service.runtime, identity, body["token"])
    assert current.credential_version > first.credential_version
    with pytest.raises(ServiceError):
        await clients.validate_attachment(service.runtime, first)
    await clients.validate_attachment(service.runtime, current)
    rotated = await service.client.post(f"{path}/rotate-credential", headers={"if-match": enabled.headers["etag"]})
    assert rotated.status_code == 200, rotated.text
    with pytest.raises(ServiceError):
        await clients.authenticate(service.runtime, identity, body["token"])
    token = rotated.json()["token"]
    assert (
        await clients.authenticate(service.runtime, identity, token)
    ).credential_version > current.credential_version
    revoked = await service.client.post(f"{path}/revoke-credential", headers={"if-match": rotated.headers["etag"]})
    assert revoked.status_code == 200 and not revoked.json()["client"]["credential_present"]
    with pytest.raises(ServiceError):
        await clients.authenticate(service.runtime, identity, token)
    with pytest.raises(ServiceError):
        await clients.validate_attachment(service.runtime, current)


async def test_acceptance_freezes_offline_directory_and_registration_permission_ceiling(
    service, scripted_model, runs_kit
):
    created = await service.client.post(
        f"{service.api}/client-environments",
        json={
            "device_id": "offline-laptop",
            "name": "Laptop",
            "default_working_directory": "/project",
            "permission_ceiling": {"operations": ["environment.file.list", "environment.file.read_text"]},
            "declared_capabilities": {"operations": ["environment.file.list", "environment.file.write_text"]},
        },
    )
    assert created.status_code == 201, created.text
    identity = created.json()["client"]["environment"]["id"]
    agent = await runs_kit.create_agent(service, scripted_model)
    accepted = await runs_kit.start_thread(
        service,
        agent,
        "Read the client project",
        environments=[
            {
                "name": "workspace",
                "environment_id": identity,
                "permission_ceiling": {
                    "operations": ["environment.file.list", "environment.file.read_text", "environment.file.write_text"]
                },
            }
        ],
    )
    from a13n_service.runs.tables import RunRow

    async with short_session(service.runtime.storage) as session:
        run = await session.get_one(RunRow, accepted["run"]["id"])
        assert run.environment_mounts == [
            {
                **DEFAULT_SESSION,
                "name": "workspace",
                "environment_id": identity,
                "working_directory": "/project",
                "permission_ceiling": {"operations": ["environment.file.list"]},
            }
        ]
    assert not service.runtime.client_connections.entries


@pytest.mark.parametrize("selected", [False, True])
async def test_unused_clients_do_not_affect_a_cloud_run(service, scripted_model, runs_kit, monkeypatch, selected):
    from a13n_service.runs.environments.clients.connections import connection_channel

    environments = []
    for name in ("laptop", "workstation"):
        response = await register(service, name)
        identity = response.json()["client"]["environment"]["id"]
        environments.append({"environment_id": identity, "name": name})

    async def unexpected_presence(*args):
        pytest.fail("Unused client unexpectedly requested connection state")

    monkeypatch.setattr(service.runtime.client_connections, "presence", unexpected_presence)
    agent = await runs_kit.create_agent(service, scripted_model)
    release = asyncio.Event()
    scripted_model.say("Answered without a client.", gate=release)
    accepted = await runs_kit.start_thread(
        service, agent, "Answer without tools", environments=environments if selected else []
    )
    task = await runs_kit.attempt(service)
    try:
        message = await scripted_model.request()
        for mount in environments:
            await service.runtime.redis.publish(connection_channel(mount["environment_id"]), "test-online-event")
        assert "has reconnected" not in str(message["messages"])
    finally:
        release.set()
        await task
    assert (await runs_kit.get_run(service, accepted["run"]["id"]))["status"] == "completed"
    assert not service.runtime.client_sessions.scopes


async def test_delete_keeps_binding_but_erases_credential_and_allows_new_registration(service):
    created = await register(service)
    body = created.json()
    identity = body["client"]["environment"]["id"]
    removed = await service.client.delete(
        f"{service.api}/environments/{identity}", headers={"if-match": created.headers["etag"]}
    )
    assert removed.status_code == 202 and removed.json()["status"] == "deleted", removed.text
    async with short_session(service.runtime.storage) as session:
        assert (await session.get_one(ClientSettingsRow, identity)).token_hash is None
        assert (await session.get_one(EnvironmentRow, identity)).device_id == "laptop"
    with pytest.raises(ServiceError):
        await clients.authenticate(service.runtime, identity, body["token"])
    again = await register(service)
    assert again.status_code == 201 and again.json()["client"]["environment"]["id"] != identity


async def test_same_installation_has_separate_owner_bindings_and_tokens(service):
    created = await register(service)
    own = created.json()
    identity = own["client"]["environment"]["id"]
    actor = await builder(service)
    other = await clients.register(
        service.runtime,
        actor,
        service.tenant.workspace_id,
        Registration(device_id="laptop", name="Other owner", default_working_directory="/other"),
    )
    assert other.client.environment.id != identity
    with pytest.raises(ServiceError) as denied:
        await clients.change_status(
            service.runtime,
            actor,
            service.tenant.workspace_id,
            identity,
            StatusChange(status="disabled"),
            if_match=created.headers["etag"],
        )
    assert denied.value.code == "forbidden"
    assert other.token is not None
    with pytest.raises(ServiceError):
        await clients.authenticate(service.runtime, identity, other.token)


async def connect(service, **overrides):
    return await service.client.post(
        f"{service.api}/client-environments/connect",
        json={"device_id": "laptop", "name": "Laptop", "default_working_directory": "/work", **overrides},
    )


async def test_first_connection_creates_once_and_existing_configuration_is_unchanged(service):
    responses = await asyncio.gather(*(connect(service) for _ in range(3)))
    assert sorted(response.status_code for response in responses) == [200, 200, 201]
    created = next(response for response in responses if response.status_code == 201)
    identity = created.json()["client"]["environment"]["id"]
    assert created.json()["token"]
    for response in responses:
        assert response.json()["client"]["environment"]["id"] == identity
        assert response.headers["cache-control"] == "no-store"
        if response.status_code == 200:
            assert response.json()["token"] is None
    again = await connect(service, environment_id=identity, name="Changed", default_working_directory="/changed")
    assert again.status_code == 200, again.text
    assert again.json()["token"] is None
    assert again.json()["client"]["environment"]["name"] == "Laptop"
    assert again.json()["client"]["configuration"]["default_working_directory"] == "/work"
    assert not service.runtime.client_connections.entries


async def test_explicit_connection_never_creates_or_rebinds(service):
    missing = await connect(service, environment_id="env_missing")
    assert missing.status_code == 404, missing.text
    created = await connect(service)
    identity = created.json()["client"]["environment"]["id"]
    mismatch = await connect(service, environment_id=identity, device_id="other-device")
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["error"]["details"]["reason"] == "device_id_mismatch"
    from a13n_service.runs.environments.clients.schemas import ConnectionRequest

    other = await builder(service)
    with pytest.raises(ServiceError) as denied:
        await clients.connect(
            service.runtime,
            other,
            service.tenant.workspace_id,
            ConnectionRequest(
                environment_id=identity, device_id="laptop", name="Other", default_working_directory="/other"
            ),
        )
    assert denied.value.code == "not_found"
    binding = await clients.connect(
        service.runtime,
        other,
        service.tenant.workspace_id,
        ConnectionRequest(device_id="laptop", name="Other", default_working_directory="/other"),
    )
    assert binding.client.environment.id != identity
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentRow)) == 2


@pytest.mark.parametrize("state", ["disabled", "deleted", "revoked"])
async def test_connection_cannot_restore_disabled_deleted_or_revoked_registration(service, state):
    created = await connect(service)
    identity = created.json()["client"]["environment"]["id"]
    headers = {"if-match": created.headers["etag"]}
    path = f"{service.api}/client-environments/{identity}"
    if state == "disabled":
        changed = await service.client.patch(f"{path}/status", json={"status": "disabled"}, headers=headers)
    elif state == "deleted":
        changed = await service.client.delete(f"{service.api}/environments/{identity}", headers=headers)
    else:
        changed = await service.client.post(f"{path}/revoke-credential", headers=headers)
    assert changed.is_success, changed.text
    for selected in ({}, {"environment_id": identity}):
        rejected = await connect(service, **selected)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["details"]["reason"] == (
            "client_credential_revoked" if state == "revoked" else f"client_environment_{state}"
        )
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentRow)) == 1
    if state == "deleted":
        replacement = await register(service)
        assert replacement.status_code == 201, replacement.text
        resolved = await connect(service)
        assert resolved.status_code == 200
        assert resolved.json()["client"]["environment"]["id"] == replacement.json()["client"]["environment"]["id"]


async def test_device_credential_cannot_enroll_and_enrollment_is_not_an_mcp_tool(service):
    import httpx2

    created = await connect(service)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as client:
        rejected = await client.post(
            f"{service.api}/client-environments/connect",
            params={"workspace_id": service.tenant.workspace_id},
            headers={"authorization": f"Bearer {created.json()['token']}"},
            json={"device_id": "another-device", "name": "Laptop", "default_working_directory": "/work"},
        )
    assert rejected.status_code == 401, rejected.text
    operation = service.app.openapi()["paths"]["/api/v1/client-environments/connect"]["post"]
    assert operation["x-a13n-mcp"] is False


async def test_agent_default_uses_offline_client_and_preserves_explicit_thread_mount(service, scripted_model, runs_kit):
    identity = (await register(service, "agent-default")).json()["client"]["environment"]["id"]
    other = (await register(service, "thread-choice")).json()["client"]["environment"]["id"]
    agent = await runs_kit.create_agent(service, scripted_model, default_environment_id=identity)
    accepted = await runs_kit.start_thread(service, agent, "Use my default computer")
    [mount] = accepted["run"]["environment_mounts"]
    assert mount["name"] == "workspace" and mount["environment_id"] == identity
    assert mount["working_directory"] == "/work"
    explicit = await runs_kit.start_thread(
        service, agent, "Use this computer", environments=[{"name": "workspace", "environment_id": other}]
    )
    assert explicit["run"]["environment_mounts"][0]["environment_id"] == other
    assert not service.runtime.client_connections.entries

    # Revocation after publication is enforced again at acceptance, without blocking a different explicit mount.
    from a13n_service.infra.db import transaction

    async with transaction(service.runtime.storage) as session:
        environment = await session.get_one(EnvironmentRow, identity)
        environment.status = "disabled"
    refused = await runs_kit.start_thread(service, agent, "Default is disabled")
    assert refused["run"] is None
    assert refused["entry"]["failure"]["code"] == "environment_disabled"
    allowed = await runs_kit.start_thread(
        service, agent, "Explicit still usable", environments=[{"name": "workspace", "environment_id": other}]
    )
    assert allowed["run"]["environment_mounts"][0]["environment_id"] == other


async def test_agent_default_can_use_another_principals_client_device(service, scripted_model, runs_kit):
    from a13n_service.resources.agents.schemas import AgentConfigurationChanges
    from a13n_service.resources.agents.service import change_configuration

    from .test_agent_configuration_changes import actor, etag

    other = await builder(service)
    registered = await clients.register(
        service.runtime,
        other,
        service.tenant.workspace_id,
        Registration(device_id="private-default", name="Private", default_working_directory="/work"),
    )
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.pause_sweeps(service)
    await change_configuration(
        service.runtime.storage,
        actor(service),
        service.tenant.workspace_id,
        agent["id"],
        AgentConfigurationChanges(default_environment_id=registered.client.environment.id),
        expected_version=etag(agent),
        registry=service.runtime.registry,
        plugins=service.runtime.plugins,
    )
    started = await runs_kit.start_thread(service, agent, "Use the shared client default")
    assert started["run"]["environment_mounts"][0]["environment_id"] == registered.client.environment.id
