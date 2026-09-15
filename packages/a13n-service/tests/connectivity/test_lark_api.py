"""Malformed provider status codes stay within the typed connectivity error boundary."""

import httpx2
import pytest
from a13n_service.connectivity.providers.lark.api import LarkApiError, read_lark_response

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "payload", [{}, {"code": None}, {"code": []}, {"code": {}}, {"code": False}, {"code": "0"}, {"code": 0.0}]
)
async def test_lark_response_requires_an_integer_status(payload):
    with pytest.raises(LarkApiError) as caught:
        await read_lark_response(httpx2.Response(200, json=payload), max_bytes=1024)
    assert caught.value.code == "invalid_provider_response"


@pytest.mark.parametrize(
    "code, expected", [(99991400, "rate_limited"), (99991401, "rate_limited"), (99991663, "provider_rejected")]
)
async def test_lark_provider_failures_keep_their_classification(code, expected):
    with pytest.raises(LarkApiError) as caught:
        await read_lark_response(
            httpx2.Response(200, headers={"retry-after": "20"}, json={"code": code}), max_bytes=1024
        )
    assert caught.value.code == expected
    if expected == "rate_limited":
        assert caught.value.retry_after_seconds == 20


async def test_lark_success_preserves_the_bounded_response_body():
    payload = {"code": 0, "data": {"items": []}}
    assert await read_lark_response(httpx2.Response(200, json=payload), max_bytes=1024) == payload
