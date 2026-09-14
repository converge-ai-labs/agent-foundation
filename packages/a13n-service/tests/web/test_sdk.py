"""Generated Python bindings against real Service routes, storage and preconditions."""

from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app

from ..models.conftest import WORKSPACE_ID, actor
from ..models.test_router import settings

pytestmark = pytest.mark.anyio


async def test_generated_sdk_real_web_provider_crud(web_sessions, service_sqlite_database, tmp_path, monkeypatch):
    # SDKs remain standalone projects. Only this integration test adds the source
    # package to its import path; Service never depends on or bundles its SDK.
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "sdk/python"))
    from a13n import Client
    from a13n.generated.api.web_providers import (
        get_workspaces_workspace_web_providers_provider_id as get_account,
    )
    from a13n.generated.api.web_providers import (
        patch_workspaces_workspace_web_providers_provider_id as update_account,
    )
    from a13n.generated.api.web_providers import (
        post_workspaces_workspace_web_providers as create_account,
    )
    from a13n.generated.models import (
        CreateWebProviderRequest,
        CreateWebProviderRequestCredential,
        UpdateWebProviderRequest,
        WebProvider,
    )

    async def authenticate(_request):
        return actor()

    app = create_app(
        settings(tmp_path, service_sqlite_database), components=Components(request_authenticator=authenticate)
    )
    async with app.router.lifespan_context(app):
        async with Client("http://testserver", "test-token", transport=httpx2.ASGITransport(app=app)) as client:
            created = await client.execute(
                lambda api: create_account.asyncio_detailed(
                    WORKSPACE_ID,
                    client=api,
                    body=CreateWebProviderRequest(
                        type_="brave",
                        name="SDK account",
                        credential=CreateWebProviderRequestCredential.from_dict({"api_key": "integration-secret"}),
                    ),
                )
            )
            assert created.status_code == 201
            assert isinstance(created.parsed, WebProvider)
            assert b"integration-secret" not in created.content
            etag = created.headers["ETag"]
            assert created.headers["X-Request-ID"]
            provider_id = created.parsed.id
            fetched = await client.execute(
                lambda api: get_account.asyncio_detailed(
                    WORKSPACE_ID,
                    provider_id,
                    client=api,
                )
            )
            assert fetched.headers["ETag"] == etag
            changed = await client.execute(
                lambda api: update_account.asyncio_detailed(
                    WORKSPACE_ID,
                    provider_id,
                    client=api,
                    if_match=etag,
                    body=UpdateWebProviderRequest(enabled=False),
                )
            )
            assert changed.status_code == 200
            assert isinstance(changed.parsed, WebProvider) and changed.parsed.enabled is False
            assert changed.headers["ETag"] != etag
            conflict = await client.execute(
                lambda api: update_account.asyncio_detailed(
                    WORKSPACE_ID,
                    provider_id,
                    client=api,
                    if_match=etag,
                    body=UpdateWebProviderRequest(enabled=True),
                )
            )
            assert conflict.status_code == 412
            assert b"error" in conflict.content
