"""Opt-in GLM journeys through the official BigModel endpoint."""

import os

import pytest
from pydantic import SecretStr

from . import direct_model
from .provider_config import ModelSettings
from .real_providers import configured_provider_lab

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def official_zhipu(request):
    if not request.config.getoption("--live-zhipu"):
        pytest.skip("Opt in with --live-zhipu; no credential reads or cloud calls by default")
    credential = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not credential:
        pytest.fail("ZHIPU_API_KEY is required for the explicitly enabled Zhipu suite", pytrace=False)
    selected_model = os.environ.get("LIVE_TEST_ZHIPU_MODEL", "glm-4.7-flash")
    if selected_model not in {"glm-4.7-flash", "glm-4.5-air"}:
        pytest.fail("Choose glm-4.7-flash or glm-4.5-air with verified trial quota", pytrace=False)
    settings = ModelSettings(
        provider="zhipu",
        api_key=SecretStr(credential),
        model=selected_model,
    )
    # The official endpoint is fixed. The caller must verify trial quota before
    # explicitly selecting Air; no automatic fallback to a metered model occurs.
    async with configured_provider_lab("model", settings) as (journey, model):
        model = await journey.patch(
            journey.base + "/models/" + model["id"],
            {
                "settings": {
                    "timeout": 60,
                    "max_tokens": 384,
                    "temperature": 0,
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
            },
        )
        yield journey, model, settings


async def test_official_provider_discovery_and_model_test(official_zhipu):
    # BigModel currently omits the free Flash model from its /models catalog.
    # Manual model IDs must remain usable without requiring catalog membership.
    await direct_model.check_provider_discovery_and_model_test(
        official_zhipu, require_catalog_entry=official_zhipu[1]["upstream_model"] != "glm-4.7-flash"
    )


async def test_official_stream_usage_and_continuation(official_zhipu):
    await direct_model.check_stream_usage_and_continuation(official_zhipu)


async def test_official_client_tool_structured_output_and_history(official_zhipu):
    await direct_model.check_client_tool_structured_output_and_history(official_zhipu)


@pytest.mark.parametrize("fault", ["invalid_credential", "missing_model"])
async def test_official_rejection_and_repair(official_zhipu, fault):
    await direct_model.check_rejection_and_repair(official_zhipu, fault)
