"""Snapshot ownership, account discovery and side-effect boundaries."""

from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration, resolved_runtime
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_service.providers.environments.daytona import desktop_build as build
from a13n_service.providers.environments.daytona.configuration import DaytonaRecipe

pytestmark = pytest.mark.anyio


def native(manifest, **changes):
    return {
        "id": "snap-1",
        "name": "owned-name",
        "organizationId": "org-1",
        "general": False,
        "state": "active",
        "buildInfo": {"dockerfileContent": manifest["dockerfile"]},
        **changes,
    }


async def test_current_credential_validation_and_exact_snapshot_reuse(monkeypatch):
    manifest = build.specification()
    http = AsyncMock()
    http.request.side_effect = [{"organizationId": "org-1"}, native(manifest)]
    monkeypatch.setattr(build, "NativeHTTP", lambda *args: http)
    api = build.DesktopBuildAPI(DaytonaConnectionConfiguration(), TokenCredential(api_key="test-secret"))
    result = await api.find("owned-name", manifest, "snap-1")
    assert result.id == "snap-1"
    assert http.request.call_args_list[0].args == ("GET", "api-keys/current")
    assert http.request.call_args_list[1].args == ("GET", "snapshots/snap-1")
    await api.close()
    http.close.assert_awaited_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"organizationId": "someone-else"},
        {"general": True},
        {"id": "replacement"},
        {"name": "other-name"},
        {"buildInfo": {"dockerfileContent": "different"}},
    ],
)
async def test_mismatched_snapshot_never_deleted(monkeypatch, changes):
    manifest = build.specification()
    http = AsyncMock()
    http.request.side_effect = [{"organizationId": "org-1"}, native(manifest, **changes)]
    monkeypatch.setattr(build, "NativeHTTP", lambda *args: http)
    api = build.DesktopBuildAPI(DaytonaConnectionConfiguration(), TokenCredential(api_key="test-secret"))
    with pytest.raises(EnvironmentProviderError):
        await api.delete("owned-name", manifest, "snap-1")
    assert all(c.args[0] == "GET" for c in http.request.call_args_list)


async def test_explicit_account_constraint_prevents_build(monkeypatch):
    http = AsyncMock()
    http.request.return_value = {"organizationId": "other"}
    monkeypatch.setattr(build, "NativeHTTP", lambda *args: http)
    api = build.DesktopBuildAPI(
        DaytonaConnectionConfiguration(organization_id="expected"), TokenCredential(api_key="secret")
    )
    with pytest.raises(EnvironmentProviderError):
        await api.create("owned-name", build.specification())
    assert http.request.await_count == 1


async def test_auto_defaults_and_explicit_custom_snapshot():
    recipe = DaytonaRecipe(desktop=True)
    assert recipe.snapshot == "auto" and recipe.desktop_assistance
    assert not DaytonaRecipe(snapshot="custom", desktop=True).desktop_assistance
    with pytest.raises(ValueError):
        DaytonaRecipe()


async def test_account_discovery_closes_transport(monkeypatch):
    from a13n_harness.providers.environment.daytona import shared

    http = AsyncMock()
    http.request.return_value = {"organizationId": "org-1"}
    monkeypatch.setattr(shared, "NativeHTTP", lambda *args: http)
    runtime = await resolved_runtime(DaytonaConnectionConfiguration(), TokenCredential(api_key="secret"))
    assert runtime.configuration.organization_id == "org-1"
    http.close.assert_awaited_once()
