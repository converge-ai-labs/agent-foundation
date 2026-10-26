"""Vercel ownership and snapshot provenance guard every reuse and cleanup."""

from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.vercel.shared import VercelConnectionConfiguration
from a13n_service.providers.environment.vercel_build import BuildTarget
from a13n_service.providers.environments.vercel import desktop_build as build
from a13n_service.providers.environments.vercel.configuration import VercelRecipe

pytestmark = pytest.mark.anyio


def api(monkeypatch):
    http = AsyncMock()
    monkeypatch.setattr(build.HttpReference, "transport", lambda _: http)
    return build.DesktopBuildAPI(
        VercelConnectionConfiguration(team_id="team_1", project_id="prj_1"), TokenCredential(api_key="secret")
    ), http


def snapshot(**changes):
    return {"id": "snap_1", "sourceSessionId": "ses_1", "status": "created", "creationMethod": "manual", **changes}


@pytest.mark.parametrize(
    "changes", [{"id": "snap_other"}, {"sourceSessionId": "other"}, {"creationMethod": "automatic"}, {"expiresAt": 123}]
)
async def test_replaced_snapshot_is_never_deleted(monkeypatch, changes):
    client, http = api(monkeypatch)
    http.request.return_value = {"snapshot": snapshot(**changes)}
    with pytest.raises(EnvironmentProviderError):
        await client.delete_snapshot("owned", BuildTarget(session_id="ses_1", created_at=1), "snap_1")
    assert all(call.args[0] == "GET" for call in http.request.call_args_list)


@pytest.mark.parametrize("change", ["name", "tags", "session", "persistent", "createdAt"])
async def test_changed_builder_is_never_deleted(monkeypatch, change):
    client, http = api(monkeypatch)
    manifest = build.specification()
    sandbox = {
        "name": "owned",
        "persistent": False,
        "createdAt": 1,
        "status": "active",
        "tags": client._tags("owned", manifest),
    }
    session = {"id": "ses_1", "status": "running", "timeout": 10000}
    if change == "session":
        session["id"] = "other"
    else:
        sandbox[change] = {"name": "other", "tags": {}, "persistent": True, "createdAt": 2}[change]
    http.request.return_value = {"sandbox": sandbox, "session": session}
    with pytest.raises(EnvironmentProviderError):
        await client.delete_builder("owned", manifest, BuildTarget(session_id="ses_1", created_at=1))
    assert all(call.args[0] == "GET" for call in http.request.call_args_list)


async def test_snapshot_lookup_uses_current_credentials_and_source_session(monkeypatch):
    client, http = api(monkeypatch)
    http.request.return_value = {"snapshot": snapshot()}
    result = await client.find_snapshot("owned", BuildTarget(session_id="ses_1", created_at=1), "snap_1")
    assert result.id == "snap_1" and result.state == "created"
    await client.close()
    http.close.assert_awaited_once()


async def test_ambiguous_snapshots_fail_closed(monkeypatch):
    client, http = api(monkeypatch)
    http.request.side_effect = [
        {"snapshots": [snapshot()], "pagination": {"next": "next"}},
        {"snapshots": [snapshot(id="snap_2")], "pagination": {"next": None}},
    ]
    with pytest.raises(EnvironmentProviderError):
        await client.find_snapshot("owned", BuildTarget(session_id="ses_1", created_at=1), None)
    assert http.request.await_count == 2


async def test_automatic_service_recipe_and_custom_compatibility():
    recipe = VercelRecipe(desktop=True)
    assert recipe.snapshot == "auto" and recipe.execution == "envd_http" and recipe.desktop_assistance
    assert recipe.timeout_seconds == 2700
    assert VercelRecipe(desktop=True, timeout_seconds=1800).timeout_seconds == 1800
    assert VercelRecipe().execution == "native"
    assert not VercelRecipe(desktop=True, execution="envd_http", snapshot="snap_custom").desktop_assistance
    assert not VercelRecipe(desktop=True, desktop_assistance=False).desktop_assistance
    with pytest.raises(ValueError):
        VercelRecipe(execution="envd_http", snapshot="auto")


async def test_deleted_snapshot_metadata_confirms_cleanup(monkeypatch):
    client, http = api(monkeypatch)
    http.request.side_effect = [
        {"snapshot": snapshot()},
        {},
        {"snapshot": snapshot(status="deleted")},
    ]
    await client.delete_snapshot("owned", BuildTarget(session_id="ses_1", created_at=1), "snap_1")
    assert [call.args[0] for call in http.request.call_args_list] == ["GET", "DELETE", "GET"]
