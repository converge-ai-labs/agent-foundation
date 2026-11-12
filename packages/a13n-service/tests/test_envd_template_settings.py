"""Public template validation and first-claim freezing of daemon settings."""

import asyncio

import pytest
from a13n_service.infra.db import short_session
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments.tables import EnvironmentRow

pytestmark = pytest.mark.anyio


async def test_api_validates_settings_and_template_edits_leave_claimed_recipe_frozen(service):
    sweeps = [task for task in service.app.state.background if task.get_name() == "control-sweeps"]
    for task in sweeps:
        task.cancel()
    await asyncio.gather(*sweeps, return_exceptions=True)
    provider = await service.client.post(
        f"{service.api}/environment-providers",
        json={"name": "Envd fixture", "type": "e2b", "credential": {"api_key": "test-key"}},
    )
    assert provider.status_code == 201, provider.text
    selected = {"limits": {"max_concurrent_file_transfers": 32}, "idle_timeout_ms": 90000}
    recipe = {"execution": "envd_http", "template": "team-envd", "envd": selected}
    body = {"name": "Shared Envd", "provider_id": provider.json()["id"], "config": {"recipe": recipe}}
    invalid = await service.client.post(
        f"{service.api}/environment-templates",
        json={**body, "config": {"recipe": {**recipe, "envd": {"limits": {"max_processes": 513}}}}},
    )
    assert invalid.status_code == 400, invalid.text
    template = await service.client.post(f"{service.api}/environment-templates", json=body)
    assert template.status_code == 201, template.text
    assert template.json()["config"]["recipe"]["envd"] == selected
    created = await service.client.post(f"{service.api}/environments", json={"template_id": template.json()["id"]})
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    claim = await lifecycle.claim(service.runtime, identity, owner="envd-settings-test")
    assert claim is not None and claim.target.recipe["envd"] == selected
    changed = await service.client.patch(
        f"{service.api}/environment-templates/{template.json()['id']}",
        headers={"if-match": template.headers["etag"]},
        json={"config": {"recipe": {**recipe, "envd": {"limits": {"max_concurrent_file_transfers": 16}}}}},
    )
    assert changed.status_code == 200, changed.text
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identity)
        assert row.handle["recipe"]["envd"] == selected
