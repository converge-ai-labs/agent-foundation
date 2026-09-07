"""Process pool isolation for native requests as well as bounded OAuth calls."""

import httpx2
import pytest
from a13n_service.connectivity.http import cookie_free_jar


@pytest.mark.anyio
async def test_shared_pool_never_retains_or_sends_provider_cookies():
    received = []

    def send(request):
        received.append(dict(request.headers))
        return httpx2.Response(200, headers={"set-cookie": "account=private; Path=/; Secure"}, json={"ok": True})

    async with httpx2.AsyncClient(cookies=cookie_free_jar(), transport=httpx2.MockTransport(send)) as pool:
        for account in ("first", "second"):
            async with pool.stream(
                "POST", "https://provider.example/api", headers={"authorization": f"Bearer {account}"}
            ) as response:
                await response.aread()
            assert not pool.cookies
    assert [headers["authorization"] for headers in received] == ["Bearer first", "Bearer second"]
    assert all("cookie" not in headers for headers in received)
