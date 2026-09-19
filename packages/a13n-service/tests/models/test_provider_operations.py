from __future__ import annotations

import httpx2
import pytest
from a13n_service.models.provider_operations import NativeProviderOperations
from a13n_service.models.provider_runtime import ModelConnection
from a13n_service.models.providers import built_in_model_provider_catalog

from .test_runtime import _runtime_providers


class _AllowEndpoints:
    async def validate(self, endpoint: str, *, resolve_dns: bool) -> str:
        return endpoint


class _ProviderResolver:
    def __init__(self, provider: ModelConnection) -> None:
        self.provider = provider

    async def resolve_provider(self, **_: str) -> ModelConnection:
        return self.provider


@pytest.mark.anyio
@pytest.mark.parametrize("provider_type", ["google_vertex", "aws_bedrock"])
async def test_missing_provider_probe_is_unsupported_not_a_connection_failure(provider_type):
    from a13n_service.models.connection_test import test_connection

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: pytest.fail("must not dispatch"))
    ) as client:
        ops = NativeProviderOperations(
            provider_resolver=_ProviderResolver(_runtime_providers()[provider_type]),
            registry=built_in_model_provider_catalog(),
            http_client=client,
            endpoint_policy=_AllowEndpoints(),
        )
        result = await test_connection(
            ops.test(provider_id="p", organization_id="o", workspace_id="w"), timeout_seconds=1, subject="Provider"
        )
    assert result.code == "connection_test_unsupported"
    assert result.success is False
