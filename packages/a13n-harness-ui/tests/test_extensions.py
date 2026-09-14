from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import Annotated, Any, cast

import pytest
from a13n_harness.capabilities import WebCapability
from a13n_harness.plugin_factories import HarnessPluginFactory
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.extensions import catalog as catalog_module
from pydantic import Field
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.settings import ModelSettings


@dataclass
class _ProviderSettingsCapability(AbstractCapability):
    settings: ModelSettings
    count: int = 1


@dataclass
class _AliasedSettingsCapability(AbstractCapability):
    settings: Annotated[ModelSettings, Field(alias="request_settings")]


def _settings_catalog() -> HarnessUiExtensionCatalog:
    return HarnessUiExtensionCatalog(
        host_capabilities={
            "_ProviderSettingsCapability": _ProviderSettingsCapability,
            "_AliasedSettingsCapability": _AliasedSettingsCapability,
        }
    )


class _PluginFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "vendor.duplicate"

    def validate_configuration(self, configuration):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous factory must not be selected")

    def create_plugin(self, context):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous factory must not be selected")


def test_catalog_marks_builtin_and_host_collision_ambiguous() -> None:
    catalog = HarnessUiExtensionCatalog(host_capabilities={"web": WebCapability})

    references = [item for item in catalog.references if item.kind == "capability" and item.key == "web"]

    assert len(references) == 2
    assert all(item.configurable is False for item in references)
    with pytest.raises(CompositionError) as error:
        catalog.capabilities((("web", {}),))
    assert error.value.code == "extension_catalog_ambiguous"


def test_catalog_marks_host_to_host_factory_collision_ambiguous() -> None:
    catalog = HarnessUiExtensionCatalog(host_plugin_factories=(_PluginFactory(), _PluginFactory()))

    references = [
        item for item in catalog.references if item.kind == "harness_plugin" and item.key == "vendor.duplicate"
    ]

    assert len(references) == 2
    assert all(item.configurable is False for item in references)
    with pytest.raises(CompositionError) as error:
        catalog.plugin_catalog(("vendor.duplicate",))
    assert error.value.code == "extension_catalog_ambiguous"


def test_catalog_refresh_failure_retains_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = HarnessUiExtensionCatalog()
    previous = catalog.references
    entry = importlib.metadata.EntryPoint(
        name="vendor.new",
        value="vendor.module:NewCapability",
        group="a13n_harness_ui.capabilities",
    )
    monkeypatch.setattr(
        catalog_module,
        "_capability_entry_points",
        lambda: {"vendor.new": (entry,)},
    )

    def fail_provider_discovery():  # type: ignore[no-untyped-def]
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(
        catalog_module,
        "discover_environment_provider_references",
        fail_provider_discovery,
    )

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        catalog.refresh()

    assert catalog.references == previous
    with pytest.raises(CompositionError) as error:
        catalog.capabilities((("vendor.new", {}),))
    assert error.value.code == "capability_catalog_invalid"


def test_catalog_projection_returns_detached_models() -> None:
    catalog = HarnessUiExtensionCatalog()

    first = catalog.references
    second = catalog.references

    assert first == second
    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))


@pytest.mark.parametrize(
    ("key", "configuration"),
    [
        ("ShellReviewCapability", {"model": "logical:review", "extra_option": True}),
        ("_ProviderSettingsCapability", {"settings": {}, "extra_option": True}),
        ("Thinking", {"effort": "low", "extra_option": True}),
        ("_AliasedSettingsCapability", {"request_settings": {}, "extra_option": True}),
    ],
)
def test_catalog_reports_native_unexpected_constructor_arguments(key, configuration) -> None:
    with pytest.raises(CompositionError) as error:
        _settings_catalog().capabilities(((key, configuration),))
    assert error.value.code == "capability_configuration_invalid"
    expected_error = ValueError if key == "ShellReviewCapability" else TypeError
    assert isinstance(error.value.__cause__, expected_error)


@pytest.mark.parametrize("key", ["ShellReviewCapability", "_ProviderSettingsCapability"])
def test_catalog_preserves_settings_without_adding_native_type_validation(key: str) -> None:
    settings: dict[str, Any] = {"temperature": 0.5, "openai_store": False, "anthropic_effort": "low"}
    configuration = (
        {"model": "logical:review", "model_settings": settings}
        if key == "ShellReviewCapability"
        else {"settings": settings}
    )
    selected = _settings_catalog().capabilities(((key, configuration),))[0]
    capability = cast(Any, selected.capability)
    actual = capability.config.model_settings if key == "ShellReviewCapability" else capability.settings
    assert actual == settings

    settings["temperature"] = "not-a-number"
    selected = _settings_catalog().capabilities(((key, configuration),))[0]
    capability = cast(Any, selected.capability)
    actual = capability.config.model_settings if key == "ShellReviewCapability" else capability.settings
    assert actual == settings  # The native Model, not UI constructor wrapping, owns request validation.


def test_catalog_uses_actual_native_constructor_names_not_validation_aliases() -> None:
    settings = {"openai_store": False, "temperature": 0.5}
    capability = (
        _settings_catalog().capabilities((("_AliasedSettingsCapability", {"settings": settings}),))[0].capability
    )
    assert isinstance(capability, _AliasedSettingsCapability)
    assert capability.settings == settings


def test_catalog_does_not_coerce_or_validate_native_constructor_scalar_arguments() -> None:
    capability = (
        _settings_catalog()
        .capabilities((("_ProviderSettingsCapability", {"settings": {}, "count": "2"}),))[0]
        .capability
    )
    assert isinstance(capability, _ProviderSettingsCapability)
    assert capability.count == "2"


def test_catalog_preserves_explicit_keyword_metadata() -> None:
    from pydantic_ai.capabilities import SetToolMetadata

    capability = HarnessUiExtensionCatalog().capabilities((("SetToolMetadata", {"code_mode": True}),))[0].capability
    assert isinstance(capability, SetToolMetadata)
    assert capability.metadata == {"code_mode": True}


@pytest.mark.parametrize("explicit", [False, True])
def test_native_tool_specs_materialize_typed_composable_tools(explicit: bool) -> None:
    from a13n_harness_ui.configuration.models import AgentResource
    from pydantic_ai.capabilities import NativeTool
    from pydantic_ai.native_tools import ImageGenerationTool, WebSearchTool

    tools = [{"kind": "web_search", "external_web_access": False}, {"kind": "image_generation", "quality": "low"}]
    agent = AgentResource.model_validate(
        {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-native",
            "name": "Native",
            "capabilities": [
                {"capability": "NativeTool", "configuration": {"tool": tool} if explicit else tool} for tool in tools
            ]
            + [{"capability": "web", "configuration": {"search": {"mode": "off"}}}],
        }
    )
    selected = HarnessUiExtensionCatalog().capabilities(
        tuple((item.capability, item.configuration) for item in agent.capabilities)
    )
    search, images, web = (item.capability for item in selected)
    assert isinstance(search, NativeTool) and isinstance(search.tool, WebSearchTool)
    assert search.tool.external_web_access is False
    assert isinstance(images, NativeTool) and isinstance(images.tool, ImageGenerationTool)
    assert images.tool.quality == "low"
    assert isinstance(web, WebCapability) and web.configuration.search.mode == "off"


def test_native_image_generation_constructs_tool_with_ui_saver() -> None:
    from a13n_harness.capabilities import NativeImageGenerationCapability
    from a13n_harness_ui.capability_runtime import save_native_image
    from pydantic_ai.native_tools import ImageGenerationTool

    selected = HarnessUiExtensionCatalog().capabilities(
        (("native_image_generation", {"quality": "high", "output_format": "webp"}),)
    )[0]
    capability = selected.capability
    assert isinstance(capability, NativeImageGenerationCapability)
    assert capability.tool == ImageGenerationTool(quality="high", output_format="webp")
    assert capability.get_native_tools() == [capability.tool]
    assert capability.saver is save_native_image


@pytest.mark.parametrize(
    "tool",
    [
        {"kind": "web_search"},
        {"kind": "web_fetch", "enable_citations": True},
        {"kind": "x_search", "allowed_x_handles": ["pydantic"]},
        {"kind": "code_execution"},
        {"kind": "image_generation", "output_format": "png"},
        {"kind": "memory"},
        {"kind": "mcp_server", "id": "docs", "url": "https://mcp.example/mcp"},
        {"kind": "file_search", "file_store_ids": ["vs_docs"]},
        {"kind": "advisor", "model": "claude-opus-4-6", "max_tokens": 4096},
    ],
)
def test_all_nine_native_specifications_use_upstream_deserialization(tool):
    from pydantic_ai.capabilities import NativeTool

    selected = HarnessUiExtensionCatalog().capabilities((("NativeTool", tool),))[0].capability
    assert isinstance(selected, NativeTool)
    assert selected.tool.kind == tool["kind"]
    assert not isinstance(selected.tool, dict)
