"""Cloud Browser definitions through the Service account API."""

from dataclasses import replace

import pytest
from a13n_service.distribution import OSS
from a13n_service.providers.browser.builtins import builtins
from a13n_service.providers.registry import Registry
from a13n_service.settings import Browsers

pytestmark = pytest.mark.anyio


async def test_cloud_account_registration(service):
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, *builtins(Browsers(root=None)))))
    service.app.state.runtime = service.runtime
    response = await service.client.get("/api/v1/provider-types/browser")
    assert response.status_code == 200
    items = response.json()["items"]
    for kind, config, timeout_field in (
        ("browserbase", {"project_id": "project"}, "timeout"),
        ("kernel", {"organization_id": "org"}, "timeout_seconds"),
        ("browser_use", {"project_id": "project"}, "timeout_minutes"),
        ("cloudflare", {"account_id": "a" * 32}, "keep_alive_seconds"),
    ):
        definition = next(item for item in items if item["type"] == kind)
        assert definition["supports_stop"] is False
        assert definition["supports_viewer"] is False
        assert timeout_field in definition["browser_recipe_schema"]["properties"]
        created = await service.client.post(
            f"{service.api}/browser-providers",
            json={"type": kind, "name": f"Cloud {kind}", "config": config, "credential": {"api_key": "fixture-secret"}},
        )
        assert created.status_code == 201
        account = created.json()
        assert account["credential_configured"] is True
        assert "fixture-secret" not in created.text
        browser = await service.client.post(
            f"{service.api}/browsers", json={"provider_id": account["id"], "name": f"Cloud browser {kind}"}
        )
        assert browser.status_code == 201
