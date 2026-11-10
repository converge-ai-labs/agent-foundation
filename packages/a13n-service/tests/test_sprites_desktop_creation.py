"""Durable Sprites installation progress and explicit retry preserve the original target."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.e2b.configuration import E2BProviderStateData
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.sprites.shared import SpritesTargetState
from a13n_service.providers.environment.errors import EnvironmentManagementError
from a13n_service.providers.environment.management import Pending
from a13n_service.providers.environment.recipes.e2b import E2BRecipe
from a13n_service.providers.environment.recipes.sprites import SpritesRecipe
from a13n_service.runs.environments import lifecycle

from .environments_support import backdate

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("provider_type,desktop", [("sprites", False), ("sprites", True), ("e2b", False)])
async def test_pending_publication_restart_failure_and_explicit_retry(
    service, runs_kit, monkeypatch, provider_type, desktop
):
    await runs_kit.pause_sweeps(service)
    p = await service.client.post(
        "/api/v1/environment-providers",
        json={
            "type": provider_type,
            "name": provider_type,
            "config": {"organization": "fixture"} if provider_type == "sprites" else {},
            "credential": {"api_key": "fixture"},
        },
    )
    assert p.status_code == 201, p.text
    recipe = {"desktop": desktop, "execution": "envd_http"}
    if provider_type == "e2b":
        recipe.update({"template": "selected-trixie", "preparation": "headless"})
    t = await service.client.post(
        "/api/v1/environment-templates",
        json={
            "name": "Desktop",
            "provider_id": p.json()["id"],
            "config": {"recipe": recipe},
        },
    )
    assert t.status_code == 201, t.text
    r = await service.client.post("/api/v1/environments", json={"template_id": t.json()["id"]})
    assert r.status_code == 201, r.text
    identity = r.json()["id"]
    op = await lifecycle.claim(service.runtime, identity, owner="first-worker")
    assert op is not None
    state = EnvironmentState(
        provider_key="sprites",
        state_version="1",
        state=SpritesTargetState(
            target_id="a13n-test",
            backing_id="sprite-immutable",
            environment_id=identity,
            configuration_fingerprint=SpritesRecipe(desktop=desktop, execution="envd_http").fingerprint,
            desktop_phase="installing",
            install_revision="a" * 64,
        ).model_dump(mode="json"),
    )
    phase = "desktop_phase"
    if provider_type == "e2b":
        phase = "preparation_phase"
        state = EnvironmentState(
            provider_key="e2b",
            state_version="1",
            state=E2BProviderStateData(
                sandbox_id="sandbox-immutable",
                environment_id=identity,
                configuration_fingerprint=E2BRecipe.model_validate(recipe).fingerprint,
                preparation_phase="installing",
                preparation_revision="a" * 64,
            ).model_dump(mode="json"),
        )
    provider = AsyncMock()
    provider.configure_envd_version = Mock()
    provider.create.side_effect = EnvironmentManagementError(
        failure("sprites", "sprites_desktop_pending", Category.UNAVAILABLE, certainty=Certainty.KNOWN),
        state,
        op.operation_id,
        identity,
    )
    if provider_type == "e2b":
        provider.create.side_effect = None
        provider.create.return_value = Pending(state, settled=True)
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    outcome = await lifecycle.perform(service.runtime, op)
    assert outcome.pending and outcome.fault is None
    await lifecycle.publish(service.runtime, op, outcome)
    progress = await service.client.get(f"/api/v1/environments/{identity}/creation")
    assert progress.json()["stage"] == "building" and not progress.json()["retryable"]
    assert progress.json()["profile"] == ("desktop" if desktop else "headless")
    assert await lifecycle.claim(service.runtime, identity, owner="early-worker") is None
    await backdate(service, identity, next_operation_at=timedelta(seconds=1))
    resumed = await lifecycle.claim(service.runtime, identity, owner="replacement-worker")
    assert resumed is not None and resumed.target.state == state
    failed = state if provider_type == "e2b" else state.model_copy(update={"state": {**state.state, phase: "failed"}})
    provider.create.side_effect = EnvironmentManagementError(
        failure(provider_type, "envd_preparation_failed", Category.INVALID, certainty=Certainty.KNOWN),
        failed,
        resumed.operation_id,
        identity,
    )
    await lifecycle.publish(service.runtime, resumed, await lifecycle.perform(service.runtime, resumed))
    progress = await service.client.get(f"/api/v1/environments/{identity}/creation")
    assert progress.json()["stage"] == "failed" and progress.json()["retryable"]
    assert await lifecycle.claim(service.runtime, identity, owner="sweep") is None
    row = await service.client.get(f"/api/v1/environments/{identity}")
    retry = await service.client.post(
        f"/api/v1/environments/{identity}/retry-creation", headers={"If-Match": row.headers["etag"]}
    )
    assert retry.status_code == 202, retry.text
    again = await lifecycle.claim(service.runtime, identity, owner="retry-worker")
    assert again is not None and again.operation_id != resumed.operation_id
    assert again.target.state == failed
    provider.create.side_effect = None
    provider.create.return_value = state.model_copy(update={"state": {**state.state, phase: "ready"}})
    await lifecycle.publish(service.runtime, again, await lifecycle.perform(service.runtime, again))
    row = await service.client.get(f"/api/v1/environments/{identity}")
    assert row.json()["status"] == "ready" and row.json()["failure"] is None
