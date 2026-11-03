"""Cloud Browser definitions through the Service account API."""

from dataclasses import replace

import pytest
from a13n_service.distribution import OSS
from a13n_service.providers.browser.builtins import builtins
from a13n_service.providers.registry import Registry
from a13n_service.settings import Browsers

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("kind", "config"),
    [("browserbase", {"project_id": "project"}), ("kernel", {"organization_id": "org"})],
)
async def test_cloud_account_registration(service, kind, config):
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, *builtins(Browsers(root=None)))))
    service.app.state.runtime = service.runtime
    response = await service.client.get("/api/v1/provider-types/browser")
    assert response.status_code == 200
    definition = next(item for item in response.json()["items"] if item["type"] == kind)
    assert definition["supports_stop"] is False
    assert ("timeout" if kind == "browserbase" else "timeout_seconds") in definition["browser_recipe_schema"][
        "properties"
    ]
    response = await service.client.post(
        f"{service.api}/browser-providers",
        json={"type": kind, "name": "Cloud", "config": config, "credential": {"api_key": "fixture-secret"}},
    )
    assert response.status_code == 201
    account = response.json()
    assert account["credential_configured"] is True
    assert "fixture-secret" not in response.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": account["id"], "name": "Cloud browser"}
    )
    assert response.status_code == 201
