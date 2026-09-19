"""Installed typed credentials cross actual HTTP, encryption and native dispatch."""

import json
from pathlib import Path

import httpx2
import pytest
from a13n_harness.providers.connector.contracts import ConnectionBinding
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_service.api import install_api_conventions
from a13n_service.connectivity.connectors import router
from a13n_service.connectivity.connectors.management import ProviderSnapshot, decode_credentials, open_provider
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.iam import authenticate_request
from a13n_service.iam.http.resource_dependencies import resolve_workspace
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage import short_session
from anyio import run_process
from fastapi import FastAPI

from .conftest import WORKSPACE_ID, actor
from .connector_helpers import AllowEndpoint

pytestmark = pytest.mark.anyio


async def test_installed_connector_account_credentials(
    connectivity_sessions,
    credential_protector,
    tmp_path,
    monkeypatch,
    external_runtime_factory,
    execution_authorization,
):
    target = tmp_path / "installed"
    await run_process(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(Path(__file__).resolve().parents[4] / "examples/provider-plugin"),
        ]
    )
    monkeypatch.syspath_prepend(str(target))
    selected = load_provider_catalogs(("acme",))
    calls = []
    external_user = None

    def remote(request):
        nonlocal external_user
        calls.append(
            (
                request.url.path,
                request.headers.get("x-api-key"),
                request.headers.get("x-revision"),
                request.headers.get("x-tier"),
            )
        )
        if request.url.path == "/connections":
            external_user = json.loads(request.content)["user"]
            return httpx2.Response(200, json={"id": "external-account"})
        if request.url.path == "/connections/external-account" and request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "external_ref": "external-account",
                    "connector_key": "crm",
                    "external_user_correlation": external_user,
                    "status": "ready",
                    "safe_metadata": {},
                    "provider_version": "v1",
                },
            )
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(remote)) as vendor:
        catalog = selected.connector
        connector_http = ConnectorHttpClient(vendor, AllowEndpoint(), response_max_bytes=4096)
        service = ConnectorProviderService(connectivity_sessions, catalog, connector_http, credential_protector)
        app = FastAPI()
        install_api_conventions(app)
        app.include_router(router.router)
        app.dependency_overrides[authenticate_request] = actor
        app.dependency_overrides[resolve_workspace] = lambda: WORKSPACE_ID
        monkeypatch.setattr(router, "_connector_providers", lambda request: service)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="https://foundation.example"
        ) as client:
            metadata = (await client.get("/api/v1/connector-provider-types")).json()["items"]
            definition = next(item for item in metadata if item["type"] == "acme_connector")
            assert definition["authentication"]["mode"] == "required"
            assert definition["setup_label"] == "Create CRM credentials"
            credentials = {"authorization": {"token": "first-secret"}, "revision": 7, "tier": "sandbox"}
            response = await client.post(
                f"/api/v1/workspaces/{WORKSPACE_ID}/connector-providers",
                headers={"Idempotency-Key": "installed-create"},
                json={
                    "name": "Installed CRM",
                    "type": "acme_connector",
                    "configuration": {"region": "eu"},
                    "credentials": credentials,
                },
            )
            assert response.status_code == 201, response.text
            resource = response.json()
            path = f"/api/v1/connector-providers/{resource['id']}"
            assert "first-secret" not in response.text

            async def check_account():
                response = await client.post(
                    path + "/test",
                    headers={"Idempotency-Key": f"test-{resource['version']}"},
                    json={"expected_version": resource["version"]},
                )
                return response

            assert (await check_account()).status_code == 200
            assert calls[-1] == ("/account", "first-secret", "7", "sandbox")
            async with short_session(connectivity_sessions) as session:
                record = await session.get(ConnectorProviderRecord, resource["id"])
                assert record.ciphertext and b"first-secret" not in record.ciphertext
                snapshot = record.credential_snapshot()
                provider = ProviderSnapshot.from_record(record)
            assert json.loads(snapshot.decrypt(credential_protector)) == credentials
            async with open_provider(
                catalog, connector_http, provider, decode_credentials(snapshot.decrypt(credential_protector))
            ) as native:
                connected = native.connect(
                    ConnectionBinding(
                        connector_key="crm", external_ref="verified-external", external_user_correlation="trusted-user"
                    )
                )
                assert (
                    await connected.execute_tool(
                        tool_key="lookup", provider_version="v1", arguments={}, request_id="managed-call"
                    )
                ).result == {"ok": True}
            assert calls[-1] == ("/execute", "first-secret", "7", "sandbox")
            from dataclasses import replace

            from a13n_harness import AgentSpec, HarnessBuilder
            from a13n_harness.providers.endpoint_policy import EndpointPolicy
            from a13n_service.connectivity.connections.domain import CreateConnectionRequest
            from a13n_service.connectivity.connections.service import ConnectionService
            from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
            from a13n_service.connectivity.execution import AttemptToolScope
            from a13n_service.connectivity.mcp.transport import RemoteTransport
            from a13n_service.connectivity.selection_domain import ConnectionRunSelection
            from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
            from pydantic_ai.models.test import TestModel

            from .conftest import NOW, ORG_ID

            accounts = ConnectionService(connectivity_sessions, EndpointPolicy(), clock=lambda: NOW)
            account = await accounts.create(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                idempotency_key="crm-account",
                request=CreateConnectionRequest.model_validate(
                    {
                        "name": "CRM",
                        "source": {"kind": "connector", "provider_id": resource["id"], "connector_key": "crm"},
                    }
                ),
            )
            setup = ConnectorConnectionService(
                connectivity_sessions,
                catalog,
                connector_http,
                credential_protector,
                correlation_secret=b"c" * 32,
                public_origin="https://foundation.example",
                setup_ttl_seconds=600,
                clock=lambda: NOW,
            )
            await setup.start_setup(
                actor=actor(),
                connection_id=account.id,
                idempotency_key="crm-setup",
                expected_version=account.version,
                setup={},
                browser_nonce="b" * 64,
                return_url="/",
            )
            from datetime import timedelta

            from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler

            reconciler = ConnectorReconciler(
                connectivity_sessions,
                catalog,
                connector_http,
                setup.setup_coordinator,
                instance_id="fixture",
                poll_interval_seconds=1,
                lease_seconds=60,
                clock=lambda: NOW + timedelta(seconds=6),
            )
            assert await reconciler.reconcile_once()
            account = await accounts.get(actor=actor(), connection_id=account.id)
            assert account.status == "ready"
            policy = EndpointPolicy()
            runtime = external_runtime_factory(catalog, RemoteTransport(policy), policy, connector_http=connector_http)

            async def guard(session=None):
                return None

            capability = await runtime._connector(
                ConnectionRunSelection(
                    kind="connector",
                    model_alias="conn_crm",
                    authorization_generation=account.authorization_generation,
                    connection_id=account.id,
                    connector_provider_id=resource["id"],
                    tools=("lookup",),
                ),
                guard,
                AttemptToolScope(
                    replace(actor(), auth_method="internal"),
                    ORG_ID,
                    WORKSPACE_ID,
                    FrozenRunConnectivity(()),
                    (),
                    authorization=await execution_authorization(),
                ),
            )
            executable = HarnessBuilder().build(
                AgentSpec(), output_type=str, model=TestModel(), capabilities=[capability]
            )
            assert "succeeded" in (await executable.run("lookup record")).output_or_raise()
            assert calls[-1] == ("/execute", "first-secret", "7", "sandbox")
            from a13n_service.connectivity.connections.checks import ConnectionChecks

            checks = ConnectionChecks(
                connectivity_sessions, catalog, connector_http, credential_protector, None, clock=lambda: NOW
            )
            checked = await checks.check(actor=actor(), connection_id=account.id, expected_version=account.version)
            assert checked.last_check.status == "passed"

            previous_generation = resource["credential_generation"]
            response = await client.patch(path, json={"expected_version": resource["version"], "name": "Renamed"})
            assert response.status_code == 200, response.text
            resource = response.json()
            assert resource["credential_generation"] == previous_generation
            assert (await check_account()).status_code == 200
            replacement = {"authorization": {"token": "rotated-secret"}, "revision": 9, "tier": "live"}
            from a13n_service.iam.models import SecurityAuditRecord
            from sqlalchemy import func, select

            async def credential_state():
                async with short_session(connectivity_sessions) as session:
                    record = await session.get(ConnectorProviderRecord, resource["id"])
                    return (
                        record.ciphertext,
                        record.credential_generation,
                        record.version,
                        await session.scalar(select(func.count()).select_from(SecurityAuditRecord)),
                    )

            before = await credential_state()
            count = len(calls)
            response = await client.post(
                path + "/credentials",
                headers={"Idempotency-Key": "missing"},
                json={"expected_version": resource["version"]},
            )
            assert response.status_code == 400, response.text
            assert await credential_state() == before
            assert len(calls) == count
            replacement_body = {"expected_version": resource["version"], "credentials": replacement}
            response = await client.post(
                path + "/credentials", headers={"Idempotency-Key": "replace"}, json=replacement_body
            )
            replaced_state = await credential_state()
            replay = await client.post(
                path + "/credentials", headers={"Idempotency-Key": "replace"}, json=replacement_body
            )
            assert replay.json() == response.json()
            assert await credential_state() == replaced_state
            assert response.status_code == 200, response.text
            resource = response.json()
            assert (await check_account()).status_code == 200
            assert calls[-1] == ("/account", "rotated-secret", "9", "live")
            assert "succeeded" in (await executable.run("lookup again")).output_or_raise()
            assert calls[-1] == ("/execute", "rotated-secret", "9", "live")
            removal_body = {"expected_version": resource["version"], "credentials": None}
            response = await client.post(
                path + "/credentials", headers={"Idempotency-Key": "remove"}, json=removal_body
            )
            removed_state = await credential_state()
            replay = await client.post(path + "/credentials", headers={"Idempotency-Key": "remove"}, json=removal_body)
            assert replay.json() == response.json()
            assert await credential_state() == removed_state
            assert response.status_code == 200, response.text
            old_version = resource["version"]
            resource = response.json()
            assert not resource["credential_configured"]
            assert resource["credential_generation"] == previous_generation + 2
            count = len(calls)
            assert (await check_account()).status_code == 409
            assert len(calls) == count
            checked = await checks.check(actor=actor(), connection_id=account.id, expected_version=account.version)
            assert checked.last_check.status == "unavailable"
            assert checked.last_check.error_code == "invalid_connector_provider_configuration"
            assert len(calls) == count
            assert (
                await client.patch(path, json={"expected_version": old_version, "credentials": replacement})
            ).status_code == 409
            response = await client.patch(
                path, json={"expected_version": resource["version"], "credentials": replacement}
            )
            resource = response.json()
            assert (await check_account()).status_code == 200
            response = await client.patch(path, json={"expected_version": resource["version"], "status": "disabled"})
            resource = response.json()
            count = len(calls)
            assert (await check_account()).status_code == 409
            assert len(calls) == count
            for access in ["optional", "public"]:
                response = await client.post(
                    f"/api/v1/workspaces/{WORKSPACE_ID}/connector-providers",
                    headers={"Idempotency-Key": access},
                    json={"name": access, "type": "acme_connector", "configuration": {"access": access}},
                )
                assert response.status_code == 201, response.text
                resource = response.json()
                assert not resource["credential_configured"]
                path = f"/api/v1/connector-providers/{resource['id']}"
                assert (await check_account()).status_code == 200
                assert calls[-1][1] is None
                no_auth = await accounts.create(
                    actor=actor(),
                    workspace_id=WORKSPACE_ID,
                    idempotency_key=access + "-connection",
                    request=CreateConnectionRequest.model_validate(
                        {
                            "name": access,
                            "source": {"kind": "connector", "provider_id": resource["id"], "connector_key": "crm"},
                        }
                    ),
                )
                await setup.start_setup(
                    actor=actor(),
                    connection_id=no_auth.id,
                    idempotency_key=access + "-setup",
                    expected_version=no_auth.version,
                    setup={},
                    browser_nonce="b" * 64,
                    return_url="/",
                )
                assert await reconciler.reconcile_once()
                no_auth = await accounts.get(actor=actor(), connection_id=no_auth.id)
                assert no_auth.status == "ready"
                count = len(calls)
                checked = await checks.check(actor=actor(), connection_id=no_auth.id, expected_version=no_auth.version)
                assert checked.last_check.status == "passed"
                assert len(calls) == count + 1
                assert calls[-1][1] is None
        assert not vendor.is_closed
