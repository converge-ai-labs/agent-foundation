"""Managed Cloudflare authority, lifecycle receipts, and the external gateway boundary."""

from uuid import uuid4

import httpx2
import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.environment_templates.schemas import TemplateConfig, TemplateCreate
from a13n_service.resources.environment_templates.service import insert_template, validate_config
from a13n_service.resources.providers.schemas import ProviderCreate
from a13n_service.resources.providers.service import insert_provider, read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.cloudflare import reconcile
from a13n_service.runs.cloudflare.remote import IMAGE_MARKER, Cloud, CloudFailure
from a13n_service.runs.environments import lifecycle, renewal
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.settings import CloudflareProvisioning
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.cloudflare_deployments import DeploymentRow

pytestmark = pytest.mark.anyio
COLLECTION = "/api/v1/cloudflare-deployments"
PROVIDERS = "/api/v1/environment-providers"
INPUT = {"name": "Workspace sandbox", "account_id": "a" * 32, "api_token": "deployment-secret-fixture"}


async def test_disabled_service_has_no_manual_cloudflare_fallback(service):
    client = service.client
    assert (await client.get(COLLECTION)).json() == {"available": False, "items": []}
    create = await client.post(COLLECTION, json={**INPUT, "request_id": str(uuid4())})
    assert create.status_code == 503
    external = await client.post(
        PROVIDERS,
        json={
            "type": "cloudflare",
            "name": "Already deployed",
            "config": {"endpoint": "https://gateway.example.com"},
            "credential": {"api_key": "gateway-only-secret"},
        },
    )
    assert external.status_code == 400, external.text
    assert external.json()["error"]["details"]["field"] == "type"
    assert "gateway-only-secret" not in external.text
    assert (await client.get(PROVIDERS)).json()["items"] == []


async def test_type_discovery_keeps_recipe_without_manual_connection_inputs(service):
    response = await service.client.get("/api/v1/provider-types/environment")
    definition = next(item for item in response.json()["items"] if item["type"] == "cloudflare")
    assert definition["configuration_schema"] == {}
    assert definition["credential_schema"] is None
    assert definition["authentication"]["mode"] == "forbidden"
    assert "network_egress" in definition["environment_schema"]["properties"]


async def test_saved_manual_provider_is_readable_but_cannot_be_used(service):
    runtime, tenant, client = service.runtime, service.tenant, service.client
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    async with transaction(runtime.storage) as session:
        legacy = await insert_provider(
            session,
            None,
            EnvironmentProviderRow,
            scope,
            ProviderCreate(
                type="cloudflare",
                name="Existing",
                config={"endpoint": "https://gateway.example.com"},
                credential={"api_key": "gateway-only-secret"},
            ),
            registry=runtime.registry,
            keys=runtime.keys,
        )
        template = await insert_template(
            session,
            None,
            scope,
            TemplateCreate(name="Legacy template", provider_id=legacy.id),
            config=validate_config(runtime.registry, "cloudflare", TemplateConfig()),
        )
        pending = EnvironmentRow(
            id=new_object_id("env"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=legacy.id,
            template_id=template.id,
            name="Legacy instance",
            created_by_id=tenant.principal_id,
            generation=0,
            status="reserved",
        )
        session.add(pending)
        await lifecycle.begin(session, pending, "creating")
        running = EnvironmentRow(
            id=new_object_id("env"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=legacy.id,
            template_id=template.id,
            name="Legacy allocated instance",
            created_by_id=tenant.principal_id,
            status="ready",
            handle={"recipe": template.config["recipe"]},
            renew_at=await now(session),
        )
        session.add(running)
    path = f"{PROVIDERS}/{legacy.id}"
    saved = await client.get(path)
    assert saved.json()["managed_deployment"] is False
    assert saved.json()["enabled"] is True
    headers = {"If-Match": saved.headers["etag"]}
    changed = await client.patch(path, json={"credential": {"api_key": "replacement"}}, headers=headers)
    assert changed.status_code == 409
    template_path = "/api/v1/environment-templates"
    saved_template = await client.get(f"{template_path}/{template.id}")
    assert saved_template.json()["provider_id"] == legacy.id
    for response in (
        await client.post(f"{path}/test"),
        await client.patch(path, json={"enabled": True}, headers=headers),
        await client.post(template_path, json={"name": "Another template", "provider_id": legacy.id}),
        await client.post("/api/v1/environments", json={"template_id": template.id}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"]["reason"] == "manual_cloudflare_provider_unsupported"
    assert (await client.delete(path, headers=headers)).status_code == 409

    assert await lifecycle.claim(runtime, pending.id, owner="test") is None
    assert await renewal.claim(runtime, running.id) is None
    async with short_session(runtime.storage) as session:
        row = await session.get(EnvironmentRow, pending.id)
        assert row is not None and row.failure is not None
        assert row.failure["code"] == "manual_cloudflare_provider_unsupported"
        assert row.failure["certainty"] == "not_dispatched"
        assert row.failure["permanent"] is True
        ready = await session.get(EnvironmentRow, running.id)
        assert ready is not None and ready.renew_at is None and ready.status == "ready"

    docker = await client.post(PROVIDERS, json={"type": "docker", "name": "Replacement provider"})
    assert docker.status_code == 201, docker.text
    replacement = await client.post(template_path, json={"name": "Replacement", "provider_id": docker.json()["id"]})
    assert replacement.status_code == 201, replacement.text
    actor = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    async with transaction(runtime.storage) as session:
        reserved = await lifecycle.reserve(session, actor, scope, replacement.json()["id"], limit=1)
        assert reserved.status == "reserved"


@pytest.fixture
async def managed_service(serve, settings):
    config = settings.model_copy(
        update={
            "provisioning": settings.provisioning.model_copy(
                update={"cloudflare": CloudflareProvisioning(enabled=True)}
            )
        }
    )
    async with serve(settings=config) as served:
        yield served


async def test_publication_and_retirement_are_durable_and_keep_secrets_separate(managed_service, monkeypatch):
    runtime, client = managed_service.runtime, managed_service.client
    # The real sweep and test calls share the same durable protocol; prevent the
    # background fixture from connecting to Cloudflare during this test.
    original = reconcile.claim

    async def no_background_claim(runtime):
        return None

    monkeypatch.setattr(reconcile, "claim", no_background_claim)

    body = {**INPUT, "request_id": str(uuid4())}
    accepted = await client.post(COLLECTION, json=body)
    assert accepted.status_code == 202, accepted.text
    job = accepted.json()
    assert job["provider_id"] is None and job["status"] == "deploying"
    assert INPUT["api_token"] not in accepted.text
    assert (await client.post(COLLECTION, json=body)).json()["id"] == job["id"]
    mismatch = await client.post(COLLECTION, json={**body, "name": "Different target"})
    assert mismatch.status_code == 409
    async with short_session(runtime.storage) as session:
        row = await session.get_one(DeploymentRow, job["id"])
        assert INPUT["api_token"] not in str(row.deployment_token)
        assert reconcile.reveal(runtime, row, "deployment_token") == INPUT["api_token"]
        gateway = reconcile.reveal(runtime, row, "gateway_token")
        assert gateway != INPUT["api_token"]
    assert (await client.get(PROVIDERS)).json()["items"] == []

    events = []

    async def provision(runtime, row, cloud, resources, factory):
        events.append(row.stage)
        match row.stage:
            case "preflight":
                resources["endpoint"] = "https://gateway.example.com"
                return "smoke"
            case "smoke":
                return "remove_smoke"
            case "remove_smoke":
                resources["removed_smoke"] = True
                return "publish"
        raise AssertionError(row.stage)

    monkeypatch.setattr(reconcile, "provision", provision)

    async def pass_once():
        import asyncio

        caller = asyncio.current_task()

        async def claim_for_test(runtime):
            return await original(runtime) if asyncio.current_task() is caller else None

        with monkeypatch.context() as m:
            m.setattr(reconcile, "claim", claim_for_test)
            await reconcile.reconcile(runtime, lambda *_: None)

    for _ in range(3):
        await pass_once()
        assert (await client.get(PROVIDERS)).json()["items"] == []
    await pass_once()
    path = f"{COLLECTION}/{job['id']}"
    ready = await client.get(path)
    assert ready.json()["status"] == "ready"
    assert events == ["preflight", "smoke", "remove_smoke"]
    provider = f"{PROVIDERS}/{ready.json()['provider_id']}"
    current = await client.get(provider)
    assert current.json()["managed_deployment"] and gateway not in current.text
    async with short_session(runtime.storage) as session:
        account = await read_provider(session, EnvironmentProviderRow, ready.json()["provider_id"])
        assert account.type == "cloudflare" and account.enabled
    headers = {"If-Match": current.headers["etag"]}
    assert (await client.patch(provider, headers=headers, json={"credential": None})).status_code == 409
    assert (await client.delete(provider, headers=headers)).status_code == 409
    queued = await client.post(path + "/retire", headers={"If-Match": ready.headers["etag"]})
    assert queued.status_code == 202, queued.text
    disabled = await client.get(provider)
    assert disabled.json()["enabled"] is False
    assert (
        await client.patch(provider, headers={"If-Match": disabled.headers["etag"]}, json={"enabled": True})
    ).status_code == 409
    for _ in range(6):
        await pass_once()  # only smoke was allocated by the simulated deployment
    retired = (await client.get(path)).json()
    assert retired["status"] == "retired" and retired["provider_id"] is None
    assert set(retired["cleanup"].values()) == {"removed", "not_created"}
    assert (await client.get(provider)).status_code == 404
    async with short_session(runtime.storage) as session:
        row = await session.get_one(DeploymentRow, job["id"])
        assert row.deployment_token is row.gateway_token is None


@pytest.mark.parametrize("annotation", [None, "another-deployment"])
async def test_registry_does_not_delete_unowned_tag(annotation):
    methods = []

    async def handle(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx2.Response(200, json={"result": {"password": "registry-secret"}, "success": True})
        return httpx2.Response(
            200,
            json={"annotations": {IMAGE_MARKER: annotation}},
            headers={"Docker-Content-Digest": "sha256:" + "d" * 64},
        )

    settings = CloudflareProvisioning()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        cloud = Cloud(client, settings, "a" * 32, "a13n-env-fixture", "cfd_fixture", settings.image, "cf-secret")
        with pytest.raises(CloudFailure, match="image_ownership_mismatch"):
            await cloud.remove_image("sha256:" + "d" * 64)
    assert methods == ["POST", "GET"]
