"""Directory identity selects native rules, never the gateway's opaque alias."""

from unittest.mock import Mock

import pytest
from a13n_service.models.domain import CatalogRef
from a13n_service.models.profiles import catalog_profile
from a13n_service.models.providers import built_in_provider_registry
from pydantic_ai.providers.openai import OpenAIProvider


def test_reference_uses_the_actual_provider_profile_without_client_construction():
    native = Mock()
    native.model_profile.return_value = {"supports_thinking": True}
    profile = catalog_profile(
        CatalogRef(provider="openai", model="gpt-5.5"),
        provider_type="openai",
        model_api="openai.responses",
        native_provider=native,
    )
    assert profile == {"supports_thinking": True}
    native.model_profile.assert_called_once_with("gpt-5.5")


@pytest.mark.parametrize(
    "provider,api,reference",
    [
        ("openai", "openai.responses", CatalogRef(provider="anthropic", model="claude-sonnet-5")),
        ("anthropic", "anthropic.messages", CatalogRef(provider="openai", model="gpt-5.5")),
        ("openai", "openai.chat_completions", CatalogRef(provider="volcengine", model="opaque-seed")),
        ("openai", "openai.chat_completions", None),
    ],
)
def test_custom_and_incompatible_references_leave_native_upstream_behavior(provider, api, reference):
    native = Mock()
    assert catalog_profile(reference, provider_type=provider, model_api=api, native_provider=native) is None
    native.model_profile.assert_not_called()


def test_compatible_family_profile_survives_generic_openai_connection():
    profile = catalog_profile(
        CatalogRef(provider="deepseek", model="deepseek-reasoner"),
        provider_type="openai",
        model_api="openai.chat_completions",
        native_provider=Mock(),
    )
    assert profile is not None and profile["supports_thinking"]


def test_openai_reference_can_use_both_native_apis():
    native = Mock()
    native.model_profile.side_effect = OpenAIProvider.model_profile
    for api in ("openai.responses", "openai.chat_completions"):
        profile = catalog_profile(
            CatalogRef(provider="openai", model="gpt-5.5"),
            provider_type="openai",
            model_api=api,
            native_provider=native,
        )
        assert profile is not None and profile["supports_thinking"]


def test_xai_is_not_an_executable_provider():
    with pytest.raises(ValueError):
        built_in_provider_registry().integration("xai")
