"""Docker image authoring and Worker boundaries through Service HTTP."""

import asyncio
import os
import socket

import httpx2
import pytest
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_service.app import Components, create_app
from a13n_service.environments import image_jobs
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.process.server import ServiceServer
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import update

from .conftest import ORG_ID, USER_ID, WORKSPACE_ID
from .test_router import (
    NOW,
    PROVIDER_KEY,
    authenticate,
    seed_database,
    settings,
)
from .test_router import (
    environment_api_client as environment_api_client,
)


@pytest.mark.anyio
async def test_docker_image_http_reaches_worker_and_real_engine(environment_api_client):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE to a local compatible image for the real Docker boundary")
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={"type": "docker", "name": "Docker"},
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    response = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider_id}/test-image",
        json={
            "request_id": "envtest_" + "a" * 32,
            "workspace_id": WORKSPACE_ID,
            "configuration": {"image": image, "init_script": "exit 99", "mounts": []},
        },
        timeout=140,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["error"] is None, result
    assert result["image_source"] == "local"
    assert result["image_id"].startswith("sha256:")
    assert {"files", "commands", "outputs", "process control"}.issubset(result["checks"])


@pytest.mark.anyio
async def test_docker_image_http_requires_provider_management(environment_api_client, service_database):
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={"type": "docker", "name": "Docker"},
    )
    assert provider.status_code == 201, provider.text
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    try:
        async with transaction(sessions) as session:
            await session.execute(
                update(RoleBindingRecord)
                .where(RoleBindingRecord.id == "rb_envrouterws123456")
                .values(role_key="viewer")
            )
    finally:
        await engine.dispose()
    response = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider.json()['id']}/test-image",
        json={
            "request_id": "envtest_" + "b" * 32,
            "workspace_id": WORKSPACE_ID,
            "configuration": {"image": "a13n-docker-environment:local"},
        },
    )
    assert response.status_code in {403, 404}, response.text
    cancel = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider.json()['id']}/test-image/envtest_{'b' * 32}/cancel",
        json={"workspace_id": WORKSPACE_ID},
    )
    assert cancel.status_code in {403, 404}, cancel.text


@pytest.mark.anyio
async def test_docker_image_http_rejects_huge_finite_memory(environment_api_client):
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={"type": "docker", "name": "Docker"},
    )
    assert provider.status_code == 201, provider.text
    response = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider.json()['id']}/test-image",
        json={
            "request_id": "envtest_" + "9" * 32,
            "workspace_id": WORKSPACE_ID,
            "configuration": {"memory_gb": 1e308},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "environment_invalid"


@pytest.mark.anyio
async def test_docker_image_cancellation_is_scoped_to_owner(environment_api_client, service_database, monkeypatch):
    other_org = "org_imageother123456"
    other_workspace = "ws_imageother123456"
    sibling_workspace = "ws_imagesibling1234"
    other_user = "usr_imageother123456"
    provider_a = "envp_imageowner12345"
    provider_b = "envp_imageother12345"
    provider_c = "envp_imageorg123456"
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    try:
        async with transaction(sessions) as session:
            session.add(
                OrganizationRecord(id=other_org, key="image-other", name="Other", created_at=NOW, updated_at=NOW)
            )
            session.add_all(
                (
                    WorkspaceRecord(
                        id=sibling_workspace,
                        organization_id=ORG_ID,
                        name="Sibling",
                        key="sibling",
                        created_at=NOW,
                        updated_at=NOW,
                        deleted_at=None,
                    ),
                    WorkspaceRecord(
                        id=other_workspace,
                        organization_id=other_org,
                        name="Other",
                        key="other",
                        created_at=NOW,
                        updated_at=NOW,
                        deleted_at=None,
                    ),
                    UserRecord(
                        id=other_user,
                        email="image-other@example.com",
                        normalized_email="image-other@example.com",
                        name="Other Builder",
                        status="active",
                        email_verified_at=NOW,
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                )
            )
            await session.flush()
            session.add(
                RoleBindingRecord(
                    id="rb_imageotherorg1234",
                    organization_id=other_org,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=other_org,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                RoleBindingRecord(
                    id="rb_imageotheruserorg",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=other_user,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            for index, (organization_id, workspace_id, user_id) in enumerate(
                (
                    (ORG_ID, sibling_workspace, USER_ID),
                    (other_org, other_workspace, USER_ID),
                    (ORG_ID, WORKSPACE_ID, other_user),
                )
            ):
                session.add(
                    RoleBindingRecord(
                        id=f"rb_imageowner{index:08d}",
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        principal_type="user",
                        principal_id=user_id,
                        resource_type="workspace",
                        resource_id=workspace_id,
                        role_key="builder",
                        created_by_user_id=USER_ID,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            for provider_id, organization_id in ((provider_a, ORG_ID), (provider_b, ORG_ID), (provider_c, other_org)):
                session.add(
                    EnvironmentProviderRecord(
                        id=provider_id,
                        organization_id=organization_id,
                        workspace_id=None,
                        type="docker",
                        name=provider_id,
                        configuration={},
                        configuration_source="deployment",
                        enabled=True,
                        credential_generation=0,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
    finally:
        await engine.dispose()

    started = asyncio.Event()
    stopped = asyncio.Event()
    executions = 0

    async def hold_work(self, request):
        nonlocal executions
        executions += 1
        started.set()
        while await self._valid(request):
            await asyncio.sleep(0.01)
        stopped.set()

    monkeypatch.setattr(image_jobs.DockerImageTestWorker, "_execute", hold_work)
    request_id = "envtest_" + "f" * 32

    async def cancel(provider_id, workspace_id, *, user_id=USER_ID):
        response = await environment_api_client.post(
            f"/api/v1/environment-providers/{provider_id}/test-image/{request_id}/cancel",
            json={"workspace_id": workspace_id},
            headers={"x-test-user": user_id, "x-test-workspace": workspace_id},
        )
        assert response.status_code == 204, response.text

    await cancel(provider_b, WORKSPACE_ID)  # An early cancel under another Provider cannot claim this ID.
    running = asyncio.create_task(
        environment_api_client.post(
            f"/api/v1/environment-providers/{provider_a}/test-image",
            json={"request_id": request_id, "workspace_id": WORKSPACE_ID, "configuration": {}},
            timeout=10,
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    await cancel(provider_b, WORKSPACE_ID)
    await cancel(provider_a, sibling_workspace)
    await cancel(provider_c, other_workspace)
    await cancel(provider_a, WORKSPACE_ID, user_id=other_user)
    await asyncio.sleep(0.3)
    assert not running.done()
    assert not stopped.is_set()
    await cancel(provider_a, WORKSPACE_ID)
    response = await asyncio.wait_for(running, 5)
    assert response.status_code == 200, response.text
    assert response.json()["error"] == "Image test canceled"
    await asyncio.wait_for(stopped.wait(), 5)

    request_id = "envtest_" + "1" * 32
    await cancel(provider_a, WORKSPACE_ID)
    early = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider_a}/test-image",
        json={"request_id": request_id, "workspace_id": WORKSPACE_ID, "configuration": {}},
    )
    assert early.status_code == 200, early.text
    assert early.json()["error"] == "Image test canceled"
    assert executions == 1


@pytest.mark.anyio
async def test_cancelled_docker_image_http_cleans_worker_container(environment_api_client, monkeypatch):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE for real cancellation coverage")
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={"type": "docker", "name": "Docker"},
    )
    assert provider.status_code == 201, provider.text
    reached = asyncio.Event()

    async def pause_after_container_start(self, argv, **kwargs):
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DockerCommands, "execute", pause_after_container_start)
    request = asyncio.create_task(
        environment_api_client.post(
            f"/api/v1/environment-providers/{provider.json()['id']}/test-image",
            json={"request_id": "envtest_" + "c" * 32, "workspace_id": WORKSPACE_ID, "configuration": {"image": image}},
            timeout=140,
        )
    )
    await asyncio.wait_for(reached.wait(), 20)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    from docker import from_env

    docker = from_env()
    try:
        for _ in range(50):
            containers = await asyncio.to_thread(docker.containers.list, all=True, filters={"name": "a13n-envtest_"})
            if not containers:
                break
            await asyncio.sleep(0.1)
        assert not containers
    finally:
        docker.close()


@pytest.mark.anyio
async def test_network_disconnect_cleans_real_worker_container(tmp_path, service_database, monkeypatch):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE for real network cancellation coverage")
    config = settings(tmp_path, service_database)
    await seed_database(config)
    app = create_app(
        config,
        components=Components(
            request_authenticator=authenticate,
            environment_provider_catalog=EnvironmentProviderCatalog(
                select_builtin_environment_providers((PROVIDER_KEY, "docker"))
            ),
        ),
    )
    reached = asyncio.Event()

    async def stalled_helper(self, argv, **kwargs):
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DockerCommands, "execute", stalled_helper)
    server = ServiceServer(app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        writer = None
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                    provider = await client.post(
                        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
                        json={"type": "docker", "name": "Docker"},
                    )
                    assert provider.status_code == 201, provider.text
                    provider_id = provider.json()["id"]
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                request_id = "envtest_" + "d" * 32
                body = (
                    f'{{"request_id":"{request_id}","workspace_id":"{WORKSPACE_ID}","configuration":{{"image":"{image}"}}}}'
                ).encode()
                writer.write(
                    f"POST /api/v1/environment-providers/{provider_id}/test-image HTTP/1.1\r\n".encode()
                    + b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
                await reached.wait()
                writer.close()
                await writer.wait_closed()
                async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                    canceled = await client.post(
                        f"/api/v1/environment-providers/{provider_id}/test-image/{request_id}/cancel",
                        json={"workspace_id": WORKSPACE_ID},
                    )
                    assert canceled.status_code == 204, canceled.text
                from docker import from_env

                docker = from_env()
                try:
                    while docker.containers.list(all=True, filters={"name": "a13n-envtest_"}):
                        await asyncio.sleep(0.1)
                finally:
                    docker.close()
        finally:
            if writer is not None:
                writer.close()
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


@pytest.mark.anyio
async def test_docker_image_http_timeout_reports_image_and_cleans_container(environment_api_client, monkeypatch):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE for real timeout coverage")
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={"type": "docker", "name": "Docker"},
    )
    assert provider.status_code == 201, provider.text
    from a13n_harness.providers.environment.docker.image_test import test_docker_image
    from a13n_service.environments import image_jobs

    async def short_test(engine, configuration):
        return await test_docker_image(engine, configuration, timeout_seconds=0.5)

    async def stalled_helper(self, argv, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(image_jobs, "test_docker_image", short_test)
    monkeypatch.setattr(DockerCommands, "execute", stalled_helper)
    response = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider.json()['id']}/test-image",
        json={"request_id": "envtest_" + "e" * 32, "workspace_id": WORKSPACE_ID, "configuration": {"image": image}},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["image_id"].startswith("sha256:")
    assert "timed out" in result["error"]
    from docker import from_env

    docker = from_env()
    try:
        assert not docker.containers.list(all=True, filters={"name": "a13n-envtest_"})
    finally:
        docker.close()


@pytest.mark.anyio
async def test_docker_connectivity_outage_preserves_enabled_provider(environment_api_client):
    provider = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers",
        json={
            "type": "docker",
            "name": "Unavailable Docker",
            "configuration": {"docker_host": "unix:///tmp/a13n-issue535-no-socket.sock"},
        },
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    for _ in range(35):
        response = await environment_api_client.get(f"/api/v1/environment-providers/{provider_id}/connectivity")
        assert response.status_code == 200, response.text
        if response.json()["status"] == "unavailable":
            break
        await asyncio.sleep(0.2)
    assert response.json()["status"] == "unavailable"
    retained = await environment_api_client.get(f"/api/v1/environment-providers/{provider_id}")
    assert retained.status_code == 200
    assert retained.json()["enabled"] is True


@pytest.mark.anyio
async def test_workspace_builder_tests_organization_docker_provider(environment_api_client, service_database):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE for the Organization Provider boundary")
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    provider_id = "envp_orgdocker123456"
    try:
        async with transaction(sessions) as session:
            session.add(
                EnvironmentProviderRecord(
                    id=provider_id,
                    organization_id=ORG_ID,
                    workspace_id=None,
                    type="docker",
                    name="Organization Docker",
                    configuration={},
                    configuration_source="deployment",
                    enabled=True,
                    credential_generation=0,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    finally:
        await engine.dispose()
    response = await environment_api_client.post(
        f"/api/v1/environment-providers/{provider_id}/test-image",
        json={"request_id": "envtest_" + "f" * 32, "workspace_id": WORKSPACE_ID, "configuration": {"image": image}},
        timeout=140,
    )
    assert response.status_code == 200, response.text
    assert response.json()["error"] is None
    assert response.json()["image_id"].startswith("sha256:")
