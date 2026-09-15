from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.model_auth import GrokCredentials
from a13n_harness_ui.composition.models import ResolvedModelRecipe
from a13n_harness_ui.configuration import CodexSubscriptionAuthentication, GrokSubscriptionAuthentication
from a13n_harness_ui.model_runtime import (
    CodexSubscriptionSource,
    GrokSubscriptionSource,
    HarnessUiModelResolver,
)
from pydantic_ai.models import ModelResolutionContext
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
_CONTEXT = ModelResolutionContext(agent=Mock(), deps=Mock(spec=AgentContext, thread_id="thread-current"))


class _CodexSource:
    async def load(self) -> OpenAICodexCredentials:
        raise AssertionError("resolver construction must not load credentials")

    async def save(self, credentials: OpenAICodexCredentials) -> None:
        del credentials
        raise AssertionError("resolver construction must not save credentials")


class _GrokSource:
    async def load(self) -> GrokCredentials:
        raise AssertionError("resolver construction must not load credentials")

    async def save(self, credentials: GrokCredentials) -> None:
        del credentials
        raise AssertionError("resolver construction must not save credentials")


def _recipe(authentication: CodexSubscriptionAuthentication | GrokSubscriptionAuthentication) -> ResolvedModelRecipe:
    provider = "openai-codex" if isinstance(authentication, CodexSubscriptionAuthentication) else "grok"
    return ResolvedModelRecipe(
        model_id="model-primary",
        route=f"{provider}:model-name",
        authentication=authentication,
    )


async def test_codex_subscription_resolution_uses_official_provider_and_affinity() -> None:
    from a13n_harness.model_auth import CodexRequestModel
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai_codex import OpenAICodexProvider

    recipe = _recipe(CodexSubscriptionAuthentication(kind="codex_subscription"))
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"codex_subscription": CodexSubscriptionSource(source=_CodexSource())},
    )
    resolved = await resolver(_CONTEXT, recipe.model_id)
    assert isinstance(resolved, CodexRequestModel)
    assert isinstance(resolved.wrapped, OpenAIResponsesModel)
    assert isinstance(resolved.provider, OpenAICodexProvider)
    assert resolved._affinity_settings(None)["extra_headers"] == {
        name: derive_model_affinity_id("thread-current") for name in ("session-id", "thread-id", "x-client-request-id")
    }
    async with resolved:
        assert resolved.model_name == "model-name"


async def test_grok_subscription_resolution_delegates_to_harness_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = _recipe(GrokSubscriptionAuthentication(kind="grok_subscription"))
    source = _GrokSource()
    built = object()
    calls: list[dict[str, Any]] = []

    def build(model_name: str, **kwargs: Any) -> object:
        calls.append({"model_name": model_name, **kwargs})
        return built

    monkeypatch.setattr("a13n_harness.model_auth.build_grok_model", build)
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"grok_subscription": GrokSubscriptionSource(source=source)},
    )

    resolved = await resolver(_CONTEXT, recipe.model_id)

    assert resolved is built
    assert calls == [
        {
            "model_name": "model-name",
            "credential_source": source,
            "refresh": None,
        }
    ]


async def test_subscription_resolution_requires_compatible_host_wiring() -> None:
    recipe = _recipe(CodexSubscriptionAuthentication(kind="codex_subscription"))

    with pytest.raises(ModelResolutionError) as missing:
        await HarnessUiModelResolver({recipe.model_id: recipe})(_CONTEXT, recipe.model_id)
    assert missing.value.code == "model_account_integration_missing"

    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"codex_subscription": GrokSubscriptionSource(source=_GrokSource())},
    )
    with pytest.raises(ModelResolutionError) as incompatible:
        await resolver(_CONTEXT, recipe.model_id)
    assert incompatible.value.code == "model_account_integration_invalid"


async def test_fresh_resolver_keeps_sources_without_touching_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = _recipe(CodexSubscriptionAuthentication(kind="codex_subscription"))
    source = _CodexSource()
    expected = object()

    monkeypatch.setattr("a13n_harness.model_auth.CodexRequestModel", lambda *args, **kwargs: expected)
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"codex_subscription": CodexSubscriptionSource(source=source)},
    )

    resolved = await resolver.fresh()(_CONTEXT, recipe.model_id)

    assert resolved is expected


@pytest.mark.parametrize("header", [None, "x-session-id", "x-custom-affinity"])
async def test_api_recipe_affinity_uses_current_resolution_thread_and_immutable_recipe(header, monkeypatch):
    from a13n_harness.models.inference import RequestHeadersModel
    from a13n_harness_ui.configuration import ApiKeyAuthentication
    from a13n_harness_ui.model_runtime import model_recipe_id
    from pydantic_ai.models.test import TestModel

    recipe = ResolvedModelRecipe(
        model_id="primary",
        route="openai-chat:example-model",
        authentication=ApiKeyAuthentication(kind="api_key", env="TEST_KEY"),
        model_configuration={"session_affinity_header": header},
    )
    original_id = model_recipe_id(recipe)
    resolver = HarnessUiModelResolver({"primary": recipe})
    recipe.model_configuration["session_affinity_header"] = "x-later-edit"
    assert model_recipe_id(recipe) != original_id

    async def build(*args):
        return TestModel()

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)
    for thread_id in ("thread-root", "thread-child", "thread-fork", "thread-root"):
        context = ModelResolutionContext(agent=Mock(), deps=Mock(spec=AgentContext, thread_id=thread_id))
        model = await resolver.fresh()(context, "primary")
        if header:
            assert isinstance(model, RequestHeadersModel)
            assert model.common_headers == {header: derive_model_affinity_id(thread_id)}
        else:
            assert isinstance(model, TestModel)


@pytest.mark.parametrize("header", ["x-litellm-session-id", "X-Custom"])
def test_adapter_accepts_presets_and_custom_headers_but_rejects_static_values(header):
    from a13n_harness_ui.errors import CompositionError
    from a13n_harness_ui.model_adapters import PydanticAiModelAdapter

    adapter = PydanticAiModelAdapter()
    validated = adapter.validate(route="openai-chat:test", settings={}, model_cfg={"session_affinity_header": header})
    assert validated.model_cfg == {"session_affinity_header": header.lower()}
    with pytest.raises(CompositionError):
        adapter.validate(
            route="openai-chat:test",
            settings={"extra_headers": {header.upper(): "fixed"}},
            model_cfg=validated.model_cfg,
        )
    with pytest.raises(CompositionError):
        adapter.validate(route="xai:test", settings={}, model_cfg=validated.model_cfg)
