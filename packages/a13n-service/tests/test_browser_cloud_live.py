"""Explicit, read-only supplier authentication checks; these do not create browser sessions."""

import httpx2
import pytest
from a13n_service.providers.browser.cloud import CloudflareConfiguration
from pydantic import SecretStr

from .conftest import cloud_test_setting


@pytest.mark.anyio
async def test_cloud_list_sessions(cloud_browser_credentials: tuple[str, SecretStr]) -> None:
    provider, credential = cloud_browser_credentials
    if provider == "browserbase":
        url = "https://api.browserbase.com/v1/sessions"
        headers = {"X-BB-API-Key": credential.get_secret_value()}
    elif provider == "cloudflare":
        account = CloudflareConfiguration(account_id=cloud_test_setting("CLOUDFLARE_ACCOUNT_ID")).account_id
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/browser-rendering/devtools/session?limit=1"
        headers = {"Authorization": f"Bearer {credential.get_secret_value()}"}
    else:
        url = "https://api.onkernel.com/browsers?limit=1"
        headers = {"Authorization": f"Bearer {credential.get_secret_value()}"}
    # Responses may contain private session URLs. Never print bodies or request headers.
    try:
        async with httpx2.AsyncClient(follow_redirects=False, timeout=20) as client:
            response = await client.get(url, headers=headers)
            status = response.status_code
    except httpx2.HTTPError:
        pytest.fail(f"{provider} list request failed; check network connectivity", pytrace=False)
    assert status == 200, f"{provider} list request returned HTTP {status}"
