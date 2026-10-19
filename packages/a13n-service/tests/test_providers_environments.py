"""The hosted environment types: account endpoints, sealed credentials and recipes checked by type."""

import json

import pytest
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import transaction
from a13n_service.resources.providers.tables import EnvironmentProviderRow

pytestmark = pytest.mark.anyio

SECRET = "hosted-secret-value"
# Each hosted type's account as it is created, and the recipe fields one invalid recipe breaks.
ACCOUNTS: dict[str, tuple[dict, dict, dict]] = {
    "e2b": ({}, {"api_key": SECRET}, {"timeout_seconds": 10}),
    "daytona": ({"organization_id": "org-1"}, {"api_key": SECRET}, {"snapshot": "s" * 300}),
    "modal": (
        {"workspace": "team", "app_name": "sandboxes"},
        {"token_id": "ak-id", "token_secret": SECRET},
        {"memory": 64},
    ),
    "vercel": ({"team_id": "team_1", "project_id": "prj_1"}, {"api_key": SECRET}, {"vcpus": 1}),
    "sprites": ({"organization": "fly-org"}, {"api_key": SECRET}, {"privileged": True}),
    "runloop": ({"organization": "runloop-org"}, {"api_key": SECRET}, {"resource_size": "HUGE"}),
}


async def test_hosted_accounts_restrict_their_endpoints(service) -> None:  # type: ignore[no-untyped-def]
    """Hosted accounts expose vendor account fields; E2B selects a compatible service domain."""
    types = (await service.client.get("/api/v1/provider-types/environment")).json()["items"]
    fields = {item["type"]: set(item["configuration_schema"].get("properties", {})) for item in types}
    assert {name: fields[name] for name in ACCOUNTS} == {
        "e2b": {"domain"},
        "daytona": {"organization_id", "target"},
        "modal": {"workspace", "app_name", "environment_name"},
        "vercel": {"team_id", "project_id"},
        "sprites": {"organization"},
        "runloop": {"organization"},
    }
    collection = f"{service.api}/environment-providers"
    for config in ({"domain": "https://sandboxes.example"}, {"api_url": "https://10.0.0.1"}):
        body = {"type": "e2b", "name": "E2B", "config": config, "credential": {"api_key": "k"}}
        refused = await service.client.post(collection, json=body)
        assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "config", refused.text


async def test_hosted_accounts_seal_their_credential_and_check_their_recipes(service) -> None:  # type: ignore[no-untyped-def]
    collection = f"{service.api}/environment-providers"
    templates = f"{service.api}/environment-templates"
    for type_, (config, credential, broken) in ACCOUNTS.items():
        body = {"type": type_, "name": type_, "config": config}
        missing = await service.client.post(collection, json=body)
        assert missing.status_code == 400 and missing.json()["error"]["details"]["field"] == "credential", type_
        created = await service.client.post(collection, json={**body, "credential": credential})
        assert created.status_code == 201, created.text
        provider = created.json()
        assert provider["credential_configured"] and SECRET not in created.text
        async with transaction(service.runtime.storage) as session:
            row = await session.get(EnvironmentProviderRow, provider["id"])
        assert row is not None and row.credential is not None and SECRET not in json.dumps(row.credential)
        location = SecretLocation(row.organization_id, "environment_providers", "credential", row.id)
        assert json.loads(service.runtime.keys.reveal(Envelope.model_validate(row.credential), location)) == credential

        template = {"name": "Box", "provider_id": provider["id"]}
        accepted = await service.client.post(
            templates, json={**template, "config": {"recipe": {"snapshot": "envd"} if type_ == "daytona" else {}}}
        )
        assert accepted.status_code == 201, accepted.text
        rejected = await service.client.post(templates, json={**template, "config": {"recipe": broken}})
        assert rejected.status_code == 400 and rejected.json()["error"]["details"]["field"] == "config.recipe", type_


async def test_e2b_compatible_service_account_and_domain_changes(service) -> None:  # type: ignore[no-untyped-def]
    collection = f"{service.api}/environment-providers"
    created = await service.client.post(
        collection,
        json={
            "type": "e2b",
            "name": "Compatible E2B",
            "config": {"domain": "sandboxes.example.com"},
            "credential": {"api_key": SECRET},
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    assert provider["config"] == {"domain": "sandboxes.example.com"}
    assert SECRET not in created.text
    template = await service.client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Compatible Interpreter",
            "provider_id": provider["id"],
            "config": {"recipe": {"template": "base", "timeout_seconds": 3600}},
        },
    )
    assert template.status_code == 201, template.text
    path = f"{collection}/{provider['id']}"
    current = await service.client.get(path)
    body = {"config": {"domain": "other-sandboxes.example.com"}}
    moved = await service.client.patch(path, json=body, headers={"If-Match": current.headers["etag"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["config"] == body["config"]
    assert moved.json()["credential_configured"]
    assert SECRET not in moved.text
