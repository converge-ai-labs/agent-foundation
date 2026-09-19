"""Cloud provider authoring, encrypted persistence, and normal Worker construction."""

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_service.environments.configuration import load_configuration
from a13n_service.environments.domain import (
    CreateManagedEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
)
from a13n_service.environments.lifecycle import EnvironmentLifecycle, LifecycleOperation
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio
BACKENDS = {
    "daytona": {"organization_id": "org-fixture"},
    "modal": {"workspace": "fixture", "app_name": "fixture"},
    "vercel": {"team_id": "team-fixture", "project_id": "project-fixture"},
    "sprites": {"organization": "fixture"},
    "runloop": {"organization": "fixture"},
}


@pytest.fixture
def provider_catalog():
    return ProviderCatalog(select_builtin_environment_providers(Settings().environments.provider_builtins))


@pytest.mark.parametrize("key", BACKENDS)
async def test_cloud_provider_service_roundtrip(
    key, environment_service, environment_sessions, provider_catalog, protector, tmp_path
):
    service = environment_service
    types = {item.type: item for item in (await service.provider_types(actor())).items}
    assert set(BACKENDS) <= types.keys()
    metadata = types[key]
    assert metadata.supports_managed and metadata.supports_destroy
    assert metadata.supports_stop == (key != "sprites")
    assert metadata.template_configuration_schema["type"] == "object"
    assert metadata.credential_schema["properties"]
    credentials = (
        {"token_id": "fixture-id", "token_secret": "private-fixture"}
        if key == "modal"
        else {"api_key": "private-fixture"}
    )
    provider = await service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type=key, name=key, configuration=BACKENDS[key], credential=credentials),
    )
    assert provider.credential_configured
    assert "private-fixture" not in provider.model_dump_json()
    template = await service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="template-" + key,
        request=CreateTemplateRequest(
            name=key,
            provider_id=provider.id,
            configuration={},
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    environment = await service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="environment-" + key,
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    async with short_session(environment_sessions) as session:
        stored_provider = await session.get(EnvironmentProviderRecord, provider.id)
        stored_environment = await session.get(EnvironmentRecord, environment.id)
        configuration = await load_configuration(session, stored_environment)
        operation = LifecycleOperation(
            environment_id=environment.id,
            provider_type=key,
            provider_configuration=stored_provider.configuration,
            credential=stored_provider.credential_snapshot(),
            configuration=configuration,
            state=None,
            operation_id="operation-fixture",
            fence=1,
            owner="owner-fixture",
            action="prepare",
            previous_status="unprepared",
        )
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector)
    adapter = await lifecycle.construct(operation)
    try:
        assert adapter.provider_key == key
        assert adapter.dump_state() is None
        assert adapter.descriptor.operation_families == frozenset({"files", "shell"})
        assert adapter.availability.status == "preparing"
    finally:
        await adapter.close()


@pytest.mark.parametrize("key", ["sprites", "vercel"])
async def test_native_recreation_advances_persisted_generation(
    key, environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    import asyncio
    import json
    import sys
    from datetime import UTC, datetime
    from urllib.parse import parse_qs, urlsplit

    import httpx2
    from a13n_harness.providers.environment.native.http import NativeHTTP
    from a13n_harness.providers.environment.sprites import provider as sprites
    from a13n_service.storage import transaction
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    target = None
    allocations = 0

    async def command(argv, stdin=None):
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate(stdin)
        assert process.returncode == 0, stderr.decode()
        return stdout

    async def reply(request):
        nonlocal target, allocations
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/cmd"):
            stdout = await command([body["command"], *body["args"]])
            return httpx2.Response(
                200, content=json.dumps({"stream": "stdout", "data": stdout.decode()}) + '\n{"command":{"exitCode":0}}'
            )
        if request.method == "POST":
            allocations += 1
            target = {**body, "id": f"sprite-{allocations}", "status": "running", "createdAt": allocations}
        if target is None:
            return httpx2.Response(404)
        result = (
            target
            if key == "sprites"
            else {
                "sandbox": target,
                "session": {
                    "id": f"session-{allocations}",
                    "status": "running",
                    "timeout": 3600000,
                },
            }
        )
        return httpx2.Response(200, json=result)

    def transport_init(self, provider_key, url, token, timeout, **kwargs):
        self.key = provider_key
        self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(reply))

    monkeypatch.setattr(NativeHTTP, "__init__", transport_init)

    async def exec_socket(socket):
        argv = parse_qs(urlsplit(socket.request.path).query)["cmd"]
        frame = await socket.recv()
        assert frame[0] == 0 and await socket.recv() == b"\x04"
        await socket.send(b"\x01" + await command(argv, frame[1:]))
        await socket.send(b"\x03\x00")

    async with serve(exec_socket, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        def local_connect(url, **kwargs):
            parsed = urlsplit(url)
            return connect(f"ws://127.0.0.1:{port}{parsed.path}?{parsed.query}", **kwargs)

        monkeypatch.setattr(sprites, "connect", local_connect)
        provider = await environment_service.create_provider(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateProviderRequest(
                type=key, name=key, configuration=BACKENDS[key], credential={"api_key": "fixture"}
            ),
        )
        template = await environment_service.create_template(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="template-native",
            request=CreateTemplateRequest(
                name=key,
                provider_id=provider.id,
                configuration={"root": str(tmp_path), "python": sys.executable},
                retention={"idle": {"stop_after": None, "delete_after": None}},
            ),
        )
        instance = await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="instance-native",
            request=CreateManagedEnvironmentRequest(template_id=template.id),
        )
        lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector)
        identities = []
        states = []
        for generation in [1, 1, 2]:
            if len(identities) == 2:
                target = None  # Fixture-owned cloud loses its backing target, not its Service record.
            async with transaction(environment_sessions) as session:
                row = await session.get(EnvironmentRecord, instance.id)
                backend = await session.get(EnvironmentProviderRecord, provider.id)
                config = await load_configuration(session, row)
                operation = lifecycle._claim(row, backend, config, "prepare", datetime.now(UTC))
            result = await lifecycle.execute(operation)
            try:
                assert result.generation == generation
                await result.environment.operations.files.write_text("/proof", "native", mode="upsert")
                assert (await result.environment.operations.files.read_text("/proof")).text == "native"
            finally:
                await result.environment.close()
            async with short_session(environment_sessions) as session:
                row = await session.get(EnvironmentRecord, instance.id)
                assert row.generation == generation and row.status == "running"
                identities.append(row.target_identity)
                states.append(row.state)
        assert allocations == 2
        assert identities[0] == identities[1] != identities[2]
        assert states[0]["state"]["target_id"] == states[2]["state"]["target_id"]
        assert states[0]["state"]["backing_id"] != states[2]["state"]["backing_id"]
