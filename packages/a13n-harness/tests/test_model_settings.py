from __future__ import annotations

from typing import Any, cast

import pytest
from a13n_harness import HarnessModelCharacteristics
from a13n_harness.models import (
    ModelCharacteristicsAlias,
    ModelCharacteristicsAliasCatalog,
    ModelSettingsAlias,
    ModelSettingsAliasCatalog,
    get_model_characteristics_alias_catalog,
    get_model_settings_alias_catalog,
    resolve_model_characteristics,
    resolve_model_settings,
)
from pydantic_ai.settings import ModelSettings


def _provider_settings(**values: Any) -> ModelSettings:
    return cast(ModelSettings, values)


def test_builtin_alias_catalogs_contain_only_anthropic_choices() -> None:
    characteristics_catalog = get_model_characteristics_alias_catalog()
    settings_catalog = get_model_settings_alias_catalog()

    assert tuple(characteristics_catalog) == (
        "anthropic:context-200k",
        "anthropic:context-400k",
        "anthropic:context-1m",
    )
    assert tuple(settings_catalog) == (
        "anthropic:interleaved-thinking",
        "anthropic:max-output-32k",
        "anthropic:max-output-64k",
        "anthropic:max-output-128k",
        "anthropic:thinking-disabled",
    )
    assert all(entry.provider == "anthropic" for entry in characteristics_catalog.entries)
    assert all(entry.provider == "anthropic" for entry in settings_catalog.entries)


def test_context_aliases_compose_and_concrete_fields_override_last() -> None:
    characteristics = resolve_model_characteristics(
        "anthropic:claude-sonnet-5",
        aliases=("anthropic:context-200k", "anthropic:context-1m"),
        overrides=HarnessModelCharacteristics(compact_threshold=0.8),
    )

    assert characteristics == HarnessModelCharacteristics(
        context_window_tokens=1_000_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.8,
    )


def test_context_alias_does_not_change_provider_request_settings() -> None:
    characteristics = resolve_model_characteristics(
        "gateway@anthropic:claude-sonnet-5",
        aliases=("anthropic:context-400k",),
    )
    settings = resolve_model_settings("gateway@anthropic:claude-sonnet-5")

    assert characteristics is not None
    assert characteristics.context_window_tokens == 400_000
    assert settings == {}


def test_characteristics_resolution_preserves_unknown_and_accepts_concrete_input() -> None:
    overrides = HarnessModelCharacteristics(context_window_tokens=123_456)

    assert resolve_model_characteristics("logical:primary") is None
    assert resolve_model_characteristics("logical:primary", overrides=overrides) == overrides


def test_custom_characteristics_alias_catalog_is_immutable_and_composable() -> None:
    def compact_earlier(characteristics: HarnessModelCharacteristics) -> HarnessModelCharacteristics:
        return characteristics.model_copy(update={"compact_threshold": 0.75})

    builtin_catalog = get_model_characteristics_alias_catalog()
    custom_catalog = builtin_catalog.with_updates(
        {
            "anthropic:compact-earlier": ModelCharacteristicsAlias(
                key="anthropic:compact-earlier",
                provider="anthropic",
                transform=compact_earlier,
            )
        }
    )

    characteristics = resolve_model_characteristics(
        "anthropic:claude-sonnet-5",
        aliases=("anthropic:context-200k", "anthropic:compact-earlier"),
        catalog=custom_catalog,
    )

    assert characteristics == HarnessModelCharacteristics(
        context_window_tokens=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.75,
    )
    assert "anthropic:compact-earlier" not in builtin_catalog


def test_max_output_aliases_compose_with_thinking() -> None:
    settings = resolve_model_settings(
        "anthropic:claude-sonnet-5",
        aliases=(
            "anthropic:max-output-32k",
            "anthropic:interleaved-thinking",
            "anthropic:max-output-128k",
        ),
    )

    assert settings == {
        "max_tokens": 131_072,
        "anthropic_thinking": {"type": "adaptive"},
    }


def test_interleaved_thinking_alias_preserves_unrelated_concrete_settings() -> None:
    overrides = _provider_settings(
        max_tokens=32_768,
        anthropic_effort="high",
        extra_headers={"X-Request-ID": "request-1"},
    )

    settings = resolve_model_settings(
        "anthropic:claude-sonnet-5",
        aliases=("anthropic:interleaved-thinking",),
        overrides=overrides,
    )

    assert settings == {
        "anthropic_thinking": {"type": "adaptive"},
        "max_tokens": 32_768,
        "anthropic_effort": "high",
        "extra_headers": {"X-Request-ID": "request-1"},
    }
    assert overrides == {
        "max_tokens": 32_768,
        "anthropic_effort": "high",
        "extra_headers": {"X-Request-ID": "request-1"},
    }


def test_thinking_disabled_alias_removes_earlier_effort() -> None:
    def add_effort(settings: ModelSettings) -> ModelSettings:
        resolved = dict(settings)
        resolved["anthropic_effort"] = "high"
        return cast(ModelSettings, resolved)

    catalog = get_model_settings_alias_catalog().with_updates(
        {
            "anthropic:effort-high": ModelSettingsAlias(
                key="anthropic:effort-high",
                provider="anthropic",
                transform=add_effort,
            )
        }
    )

    settings = resolve_model_settings(
        "gateway@anthropic:claude-sonnet-5",
        aliases=("anthropic:effort-high", "anthropic:thinking-disabled"),
        catalog=catalog,
    )

    assert settings == {"anthropic_thinking": {"type": "disabled"}}


def test_concrete_overrides_apply_after_aliases() -> None:
    settings = resolve_model_settings(
        "anthropic:claude-sonnet-5",
        aliases=("anthropic:thinking-disabled",),
        overrides=_provider_settings(
            anthropic_thinking={"type": "adaptive"},
            anthropic_effort="medium",
        ),
    )

    assert settings == {
        "anthropic_thinking": {"type": "adaptive"},
        "anthropic_effort": "medium",
    }


@pytest.mark.parametrize(
    "model",
    [
        "logical-model",
        "gateway@logical-model",
        "@anthropic:claude-sonnet-5",
        "gateway@@anthropic:claude-sonnet-5",
        "anthropic:",
        "ANTHROPIC:claude-sonnet-5",
        "anthropic :claude-sonnet-5",
        "anthropic: claude-sonnet-5",
        "gateway @anthropic:claude-sonnet-5",
        "gateway@ anthropic:claude-sonnet-5",
    ],
)
def test_alias_resolution_requires_a_valid_provider_qualified_model(model: str) -> None:
    with pytest.raises(ValueError):
        resolve_model_settings(
            model,
            aliases=("anthropic:interleaved-thinking",),
        )

    with pytest.raises(ValueError):
        resolve_model_characteristics(
            model,
            aliases=("anthropic:context-200k",),
        )


def test_alias_resolution_rejects_unknown_or_incompatible_aliases() -> None:
    with pytest.raises(ValueError, match="unknown model settings alias"):
        resolve_model_settings(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:missing",),
        )

    with pytest.raises(ValueError, match="unknown model settings alias"):
        resolve_model_settings(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:interleaved-thinking",),
            catalog=ModelSettingsAliasCatalog({}),
        )

    with pytest.raises(ValueError, match="requires provider 'anthropic'"):
        resolve_model_settings(
            "openai:gpt-5.5",
            aliases=("anthropic:interleaved-thinking",),
        )


def test_characteristics_alias_resolution_rejects_unknown_or_incompatible_aliases() -> None:
    with pytest.raises(ValueError, match="unknown model characteristics alias"):
        resolve_model_characteristics(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:missing",),
        )

    with pytest.raises(ValueError, match="unknown model characteristics alias"):
        resolve_model_characteristics(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:context-200k",),
            catalog=ModelCharacteristicsAliasCatalog({}),
        )

    with pytest.raises(ValueError, match="requires provider 'anthropic'"):
        resolve_model_characteristics(
            "openai:gpt-5.5",
            aliases=("anthropic:context-200k",),
        )


def test_alias_resolution_rejects_invalid_transform_results() -> None:
    def invalid(settings: ModelSettings) -> ModelSettings:
        del settings
        return cast(ModelSettings, object())

    catalog = ModelSettingsAliasCatalog(
        {
            "anthropic:invalid": ModelSettingsAlias(
                key="anthropic:invalid",
                provider="anthropic",
                transform=invalid,
            )
        }
    )

    with pytest.raises(TypeError, match="must return ModelSettings"):
        resolve_model_settings(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:invalid",),
            catalog=catalog,
        )


def test_characteristics_alias_resolution_rejects_invalid_transform_results() -> None:
    def invalid(characteristics: HarnessModelCharacteristics) -> HarnessModelCharacteristics:
        del characteristics
        return cast(HarnessModelCharacteristics, object())

    catalog = ModelCharacteristicsAliasCatalog(
        {
            "anthropic:invalid": ModelCharacteristicsAlias(
                key="anthropic:invalid",
                provider="anthropic",
                transform=invalid,
            )
        }
    )

    with pytest.raises(TypeError, match="must return HarnessModelCharacteristics"):
        resolve_model_characteristics(
            "anthropic:claude-sonnet-5",
            aliases=("anthropic:invalid",),
            catalog=catalog,
        )


def test_concrete_settings_need_no_provider_qualified_model() -> None:
    settings = resolve_model_settings(
        "logical:primary",
        overrides=ModelSettings(temperature=0.25),
    )

    assert settings == ModelSettings(temperature=0.25)


def test_alias_sequence_rejects_a_bare_string() -> None:
    with pytest.raises(TypeError, match="sequence of strings"):
        resolve_model_settings(
            "anthropic:claude-sonnet-5",
            aliases=cast(Any, "anthropic:interleaved-thinking"),
        )

    with pytest.raises(TypeError, match="sequence of strings"):
        resolve_model_characteristics(
            "anthropic:claude-sonnet-5",
            aliases=cast(Any, "anthropic:context-200k"),
        )


def test_resolved_settings_are_deeply_detached_from_overrides() -> None:
    overrides = _provider_settings(extra_body={"feature": {"enabled": True}})

    settings = resolve_model_settings("logical:primary", overrides=overrides)
    extra_body = cast(dict[str, dict[str, bool]], settings["extra_body"])
    extra_body["feature"]["enabled"] = False

    assert overrides == {"extra_body": {"feature": {"enabled": True}}}
