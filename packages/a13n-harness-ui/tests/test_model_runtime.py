from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.providers.model.oauth import GrokCredentials
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

    monkeypatch.setattr("a13n_harness.providers.model.oauth.build_grok_model", build)
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


@pytest.mark.parametrize("fresh", [False, True])
async def test_codex_resolution_passes_current_thread_and_bound_source_without_loading_credentials(
    monkeypatch: pytest.MonkeyPatch, fresh: bool
) -> None:
    from a13n_harness_ui.model_accounts.codex import BoundCodexCredentialSource

    recipe = _recipe(CodexSubscriptionAuthentication(kind="codex_subscription"))
    source = _CodexSource()
    expected = object()
    build = Mock(return_value=expected)
    bind = Mock(wraps=BoundCodexCredentialSource)
    monkeypatch.setattr("a13n_harness.models.codex.CodexRequestModel", build)
    monkeypatch.setattr("a13n_harness_ui.model_accounts.codex.BoundCodexCredentialSource", bind)
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"codex_subscription": CodexSubscriptionSource(source=source)},
    )

    resolved = await (resolver.fresh() if fresh else resolver)(_CONTEXT, recipe.model_id)

    assert resolved is expected
    bind.assert_called_once_with(source)
    bound = build.call_args.kwargs["credential_source"]
    assert isinstance(bound, BoundCodexCredentialSource)
    build.assert_called_once_with("model-name", credential_source=bound, thread_id="thread-current")


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


@pytest.mark.parametrize("base_url", [None, "https://jev-gateway.example/custom", "http://localhost:8080"])
async def test_jev_is_resolved_as_a_normal_api_key_model(monkeypatch, base_url):
    from a13n_harness.providers.model import routes
    from a13n_harness_ui.configuration import ApiKeyAuthentication
    from a13n_harness_ui.model_adapters import PydanticAiModelAdapter
    from pydantic_ai.models.typesafe import TypeSafeModel

    async def validate(self, endpoint):
        return endpoint

    monkeypatch.setattr(routes.EndpointPolicy, "validate", validate)
    monkeypatch.setenv("TEST_TYPESAFE_KEY", "fixture")
    configuration = PydanticAiModelAdapter().validate(
        route="typesafe:jev-latest", settings={}, model_cfg={"base_url": base_url} if base_url else {}
    )
    recipe = ResolvedModelRecipe(
        model_id="model-jev",
        route="typesafe:jev-latest",
        authentication=ApiKeyAuthentication(kind="api_key", env="TEST_TYPESAFE_KEY"),
        model_configuration=configuration.model_cfg,
    )
    resolver = HarnessUiModelResolver({recipe.model_id: recipe})
    model = await resolver(_CONTEXT, recipe.model_id)
    assert isinstance(model, TypeSafeModel)
    async with model:
        assert model.model_name == "jev-latest"
        assert model.profile["supports_text_output"] is False
        assert model.provider is not None
        assert str(model.provider.base_url).rstrip("/") == (base_url or "https://api.typesafe.ai")
    # A subsequent Run reconstructs its own native client.
    fresh = await resolver.fresh()(_CONTEXT, recipe.model_id)
    async with fresh:
        assert fresh is not model and fresh.provider is not model.provider
