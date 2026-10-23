"""Draft Vercel account discovery returns choices, never credential or upstream error bodies."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_service.resources.providers import vercel_options as options

pytestmark = pytest.mark.anyio


@pytest.fixture
def cloud(monkeypatch):
    client = AsyncMock()

    @asynccontextmanager
    async def opened(*args, **kwargs):
        yield client

    monkeypatch.setattr(options, "open_http", opened)
    return client


async def test_lists_teams_and_project_pagination(service, cloud):
    cloud.get.return_value = httpx2.Response(
        200,
        request=httpx2.Request("GET", "https://api.vercel.com/v2/teams"),
        json={"teams": [{"id": "team_a", "name": "A"}], "pagination": {"next": 100}},
    )
    r = await service.client.post("/api/v1/environment-provider-options/vercel", json={"api_key": "test-secret"})
    assert r.status_code == 200 and r.json() == {"items": [{"id": "team_a", "name": "A"}], "cursor": 100}
    cloud.get.return_value = httpx2.Response(
        200,
        request=httpx2.Request("GET", "https://api.vercel.com/v10/projects"),
        json={"projects": [{"id": "prj_a", "name": "P", "accountId": "team_a"}], "pagination": {"next": None}},
    )
    r = await service.client.post(
        "/api/v1/environment-provider-options/vercel",
        json={"api_key": "test-secret", "team_id": "team_a", "cursor": 100},
    )
    assert r.status_code == 200 and r.json()["items"][0]["id"] == "prj_a"
    assert cloud.get.call_args.kwargs["params"] == {"limit": "100", "until": "100", "teamId": "team_a"}
    assert "test-secret" not in r.text


async def test_cross_account_project_is_not_returned(service, cloud):
    cloud.get.return_value = httpx2.Response(
        200,
        request=httpx2.Request("GET", "https://api.vercel.com/v10/projects"),
        json={
            "projects": [{"id": "prj_other", "name": "Other", "accountId": "team_other"}],
            "pagination": {"next": None},
        },
    )
    r = await service.client.post(
        "/api/v1/environment-provider-options/vercel", json={"api_key": "test-secret", "team_id": "team_a"}
    )
    assert r.status_code == 503
    assert "prj_other" not in r.text and "test-secret" not in r.text


async def test_vendor_error_is_sanitized(service, cloud):
    cloud.get.return_value = httpx2.Response(
        403,
        request=httpx2.Request("GET", "https://api.vercel.com/v2/teams"),
        json={"error": {"message": "test-secret"}},
    )
    r = await service.client.post("/api/v1/environment-provider-options/vercel", json={"api_key": "test-secret"})
    assert r.status_code == 503 and "test-secret" not in r.text


async def test_archived_workspace_cannot_dispatch_credential_lookup(service, cloud):
    current = await service.client.get(service.workspace)
    archived = await service.client.post(service.workspace + "/archive", headers={"If-Match": current.headers["etag"]})
    assert archived.status_code == 200
    response = await service.client.post("/api/v1/environment-provider-options/vercel", json={"api_key": "test-secret"})
    assert response.status_code == 422 and "test-secret" not in response.text
    cloud.get.assert_not_awaited()
