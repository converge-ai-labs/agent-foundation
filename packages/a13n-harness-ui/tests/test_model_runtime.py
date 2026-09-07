from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_harness_ui.composition.models import ResolvedModelRecipe
from a13n_harness_ui.configuration import CodexSubscriptionAuthentication, GrokSubscriptionAuthentication
from a13n_harness_ui.model_runtime import (
    CodexSubscriptionSource,
    GrokSubscriptionSource,
    HarnessUiModelResolver,
)
from pydantic_ai.models import ModelResolutionContext

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
_CONTEXT = cast(ModelResolutionContext[AgentContext], None)


class _CodexSource:
    async def load(self) -> CodexCredentials:
        raise AssertionError("resolver construction must not load credentials")

    async def save(self, credentials: CodexCredentials) -> None:
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


async def test_codex_subscription_resolution_delegates_to_harness_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = _recipe(CodexSubscriptionAuthentication(kind="codex_subscription"))
    source = _CodexSource()
    built = object()
    calls: list[dict[str, Any]] = []

    async def refresh(credentials: CodexCredentials) -> CodexCredentials:
        return credentials

    def build(model_name: str, **kwargs: Any) -> object:
        calls.append({"model_name": model_name, **kwargs})
        return built

    monkeypatch.setattr("a13n_harness_ui.model_runtime.build_codex_model", build)
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={
            "codex_subscription": CodexSubscriptionSource(source=source, refresh=refresh),
        },
    )

    resolved = await resolver(_CONTEXT, recipe.model_id)

    assert resolved is built
    assert calls == [
        {
            "model_name": "model-name",
            "credential_source": source,
            "refresh": refresh,
            "originator": "a13n-harness-ui",
        }
    ]


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

    monkeypatch.setattr("a13n_harness_ui.model_runtime.build_grok_model", build)
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

    monkeypatch.setattr("a13n_harness_ui.model_runtime.build_codex_model", lambda *args, **kwargs: expected)
    resolver = HarnessUiModelResolver(
        {recipe.model_id: recipe},
        subscription_sources={"codex_subscription": CodexSubscriptionSource(source=source)},
    )

    resolved = await resolver.fresh()(_CONTEXT, recipe.model_id)

    assert resolved is expected
