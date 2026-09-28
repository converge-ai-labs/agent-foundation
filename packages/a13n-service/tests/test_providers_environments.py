"""The hosted environment types: accounts that name no endpoint, sealed credentials and recipes checked by type."""

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


async def test_hosted_accounts_name_no_endpoint(service) -> None:  # type: ignore[no-untyped-def]
    """Their SDKs dial fixed vendor APIs; E2B's domain and API URL, which would name the hosts its SDK dials, are
    not offered, so no tenant field reaches a host the endpoint policy has not checked."""
    types = (await service.client.get("/api/v1/provider-types/environment")).json()["items"]
    fields = {item["type"]: set(item["configuration_schema"].get("properties", {})) for item in types}
    assert {name: fields[name] for name in ACCOUNTS} == {
        "e2b": set(),
        "daytona": {"organization_id", "target"},
        "modal": {"workspace", "app_name", "environment_name"},
        "vercel": {"team_id", "project_id"},
        "sprites": {"organization"},
        "runloop": {"organization"},
    }
    collection = f"{service.api}/environment-providers"
    for config in ({"domain": "sandboxes.example"}, {"api_url": "https://10.0.0.1"}):
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
        accepted = await service.client.post(templates, json={**template, "config": {"recipe": {}}})
        assert accepted.status_code == 201, accepted.text
        rejected = await service.client.post(templates, json={**template, "config": {"recipe": broken}})
        assert rejected.status_code == 400 and rejected.json()["error"]["details"]["field"] == "config.recipe", type_
