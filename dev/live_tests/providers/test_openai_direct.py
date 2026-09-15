"""Opt-in official OpenAI endpoints through real Control, Worker and native SDKs."""

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


@pytest.fixture(scope="module", params=["openai.chat_completions", "openai.responses"])
async def official_openai(request):
    if not request.config.getoption("--live-openai"):
        pytest.skip("Opt in with --live-openai; no credential reads or cloud calls by default")
    credential = os.environ.get("OPENAI_API_KEY", "").strip()
    if not credential:
        pytest.fail("OPENAI_API_KEY is required for the explicitly enabled OpenAI suite", pytrace=False)
    settings = ModelSettings(
        provider="openai",
        api_key=SecretStr(credential),
        model=os.environ.get("LIVE_TEST_OPENAI_MODEL", "gpt-4.1-nano"),
    )
    # An empty configuration selects the official endpoint. Ambient OPENAI_BASE_URL
    # and the optional OpenRouter TOML must never redirect this credential.
    async with configured_provider_lab("model", settings) as (journey, model):
        model = await journey.patch(
            journey.base + "/models/" + model["id"],
            {"model_api": request.param, "settings": {"timeout": 60, "max_tokens": 384, "temperature": 0}},
        )
        yield journey, model, settings


async def test_official_provider_discovery_and_model_test(official_openai):
    await direct_model.check_provider_discovery_and_model_test(official_openai)


async def test_official_stream_usage_and_continuation(official_openai):
    await direct_model.check_stream_usage_and_continuation(official_openai)


async def test_official_client_tool_structured_output_and_history(official_openai):
    await direct_model.check_client_tool_structured_output_and_history(official_openai)


@pytest.mark.parametrize("fault", ["invalid_credential", "missing_model"])
async def test_official_rejection_and_repair(official_openai, fault):
    await direct_model.check_rejection_and_repair(official_openai, fault)
