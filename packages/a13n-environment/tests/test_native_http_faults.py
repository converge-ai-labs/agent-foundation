"""Native acknowledgements cannot turn dispatched effects into safe retries."""

import asyncio

import httpx2
import pytest
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.native.http import NativeHTTP


@pytest.mark.parametrize(
    "body", [b"not-json", b"x" * (32 * 1024 * 1024 + 1)], ids=["invalid-json", "oversized-response"]
)
@pytest.mark.parametrize("method", ["POST", "GET"])
def test_invalid_acknowledgement_certainty_without_replay(body, method):
    async def scenario():
        calls = []

        def reply(request):
            calls.append(request.method)
            return httpx2.Response(200, content=body)

        transport = NativeHTTP("runloop", "https://fixture.invalid", "fixture", 1)
        await transport.client.aclose()
        transport.client = httpx2.AsyncClient(base_url="https://fixture.invalid", transport=httpx2.MockTransport(reply))
        try:
            with pytest.raises(EnvironmentProviderError) as exc:
                await transport.request(method, "/mutation")
            assert exc.value.certainty.value == ("unknown" if method == "POST" else "known")
            assert exc.value.recovery_hint.value == ("reconcile" if method == "POST" else "none")
            assert calls == [method]
        finally:
            await transport.close()

    asyncio.run(scenario())
