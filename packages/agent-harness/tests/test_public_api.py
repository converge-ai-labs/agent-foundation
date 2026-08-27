from __future__ import annotations

import a13n_environment_provider as environment_provider
import a13n_harness as harness
import a13n_harness.capabilities as capabilities
import a13n_harness.environment as environment
import a13n_harness.environment.advanced as advanced_environment
import a13n_harness.filters as filters
import a13n_harness.tools as tools
import a13n_harness.toolsets as toolsets


def test_root_facade_exports_documented_capabilities_and_toolsets() -> None:
    expected = {
        "AgentSpec",
        "BoundSkillCatalog",
        "BoundSkillCatalogItem",
        "CodeActCapability",
        "CodeActConfig",
        "CodeActPolicyToolset",
        "CodeActToolPolicy",
        "CompactionCapability",
        "DocumentsCapability",
        "DocumentsRunCapability",
        "DocumentsToolset",
        "DynamicEnvironmentCapability",
        "Environment",
        "EnvironmentAccess",
        "EnvironmentEntry",
        "EnvironmentMount",
        "EnvironmentSource",
        "FILE_VIEW_RULES",
        "FileContextCapability",
        "FileSkillSource",
        "FileToolset",
        "FileViewRule",
        "HandoffCapability",
        "GatewayModelProviderFactory",
        "HandoffToolset",
        "MediaCapability",
        "MediaRunCapability",
        "MediaToolset",
        "MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV",
        "MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV",
        "ModelConfiguration",
        "AbstractModelCostCapability",
        "CatalogModelCostCapability",
        "NoModelCostCapability",
        "PricingCatalog",
        "ModelPricingEntry",
        "OfficialModelCatalog",
        "OfficialModelEntry",
        "get_default_pricing_catalog",
        "get_model_configuration_alias_catalog",
        "get_model_settings_alias_catalog",
        "get_official_model_catalog",
        "ModelConfigurationAlias",
        "ModelConfigurationAliasCatalog",
        "ModelConfigurationTransform",
        "ModelPatch",
        "ModelProviderFactory",
        "ModelSettingsAlias",
        "ModelSettingsAliasCatalog",
        "ModelSettingsTransform",
        "RequestHeadersModel",
        "SelfHealingModelCapability",
        "MonitoredProcessCapability",
        "MonitoredProcessRunCapability",
        "MonitoredProcessToolset",
        "RunModelResolver",
        "RunSkillPaths",
        "RunUsageLedger",
        "RuntimeContextCapability",
        "ShellToolset",
        "SkillPath",
        "SkillSelectionRunCapability",
        "SkillsCapability",
        "TaskStateRunCapability",
        "ToolMetadataKey",
        "ToolRuntimeMetadata",
        "UserInteractionCapability",
        "UserInteractionToolset",
        "WebCapability",
        "WebRunCapability",
        "WebToolset",
        "WorkingStateCapability",
        "WorkingStateToolset",
        "infer_model",
        "resolve_model_configuration",
        "resolve_model_settings",
    }

    assert expected <= set(harness.__all__)
    assert all(hasattr(harness, name) for name in expected)
    assert "HarnessStreamEvent" in harness.__all__
    assert hasattr(harness, "HarnessStreamEvent")
    assert "HarnessStreamItem" not in harness.__all__
    assert not hasattr(harness, "HarnessStreamItem")
    assert "ModelRunBinding" not in harness.__all__
    assert not hasattr(harness, "ModelRunBinding")
    assert not hasattr(harness.HarnessBuilder, "build_code")
    assert hasattr(harness.RunBindings, "embedded")
    assert not hasattr(harness.RunBindings, "local")
    assert "EnvironmentSkillSource" not in harness.__all__
    assert not hasattr(harness, "EnvironmentSkillSource")
    assert "EnvironmentSkillSource" not in capabilities.__all__
    assert not hasattr(capabilities, "EnvironmentSkillSource")


def test_feature_facades_export_documented_families() -> None:
    expected_capabilities = {
        "BoundSkillCatalog",
        "BoundSkillCatalogItem",
        "CodeActCapability",
        "CodeActConfig",
        "CompactionCapability",
        "DocumentsCapability",
        "DocumentsRunCapability",
        "FileContextCapability",
        "FileSkillSource",
        "HandoffCapability",
        "MediaCapability",
        "MediaRunCapability",
        "MonitoredProcessCapability",
        "MonitoredProcessRunCapability",
        "RuntimeContextCapability",
        "SkillSelectionRunCapability",
        "SkillsCapability",
        "TaskStateRunCapability",
        "UserInteractionCapability",
        "WebCapability",
        "WebRunCapability",
        "WorkingStateCapability",
    }
    expected_toolsets = {
        "ClientToolsToolset",
        "CodeActPolicyToolset",
        "CodeActToolPolicy",
        "DelegateResult",
        "DelegationToolset",
        "DocumentsToolset",
        "FILE_VIEW_RULES",
        "FileToolset",
        "FileViewRule",
        "HandoffToolset",
        "MediaToolset",
        "MonitoredProcessToolset",
        "ShellToolset",
        "UserInteractionToolset",
        "WebToolset",
        "WorkingStateToolset",
        "DEFAULT_TOOL_OUTPUT_CHARS",
        "FINAL_TOOL_OUTPUT_HARD_CHARS",
        "MAX_TOOL_OUTPUT_SPILL_BYTES",
        "ToolOutputDisclosure",
        "acknowledge_tool_output",
        "continuation_disclosure",
        "create_tool_output_disclosure",
        "disclose_mapping_field",
        "disclose_sequence_field",
        "disclose_text_fields",
        "disclose_text_paths",
        "fit_text_fields_to_limit",
        "tool_output_bytes",
        "tool_output_size",
        "tool_output_text",
    }
    expected_filters = {
        "ColdStartFilterCapability",
        "ColdStartFilterConfiguration",
        "ContentFilterCapability",
        "ContentFilterConfiguration",
        "MediaFamily",
        "MessageIntegrityFilterCapability",
    }

    assert expected_capabilities <= set(capabilities.__all__)
    assert expected_toolsets == set(toolsets.__all__)
    assert expected_filters == set(filters.__all__)


def test_environment_and_managed_tool_import_routes_are_public() -> None:
    removed_harness_provider_symbols = {
        "DirectLocalEnvironmentConfiguration",
        "DirectLocalEnvironmentProviderBinding",
        "DirectLocalFilePolicy",
        "DirectLocalOutputPolicy",
        "DirectLocalPortPolicy",
        "DirectLocalProcessPolicy",
        "DirectLocalRootConfiguration",
        "DirectLocalShellProfile",
        "EnvironmentProviderFactory",
        "EnvironmentProviderFactoryCatalog",
    }
    expected_environment = {
        "DynamicEnvironmentCapability",
        "ENVIRONMENT_RUN_EXTENSION_ENTRY_POINT_GROUP",
        "Environment",
        "EnvironmentAccess",
        "EnvironmentEntry",
        "EnvironmentMount",
        "EnvironmentRunExtension",
        "EnvironmentRunExtensionContext",
        "EnvironmentRunExtensionFactory",
        "EnvironmentRunExtensionFactoryCatalog",
        "EnvironmentRunExtensionFactoryContext",
        "EnvironmentRunExtensionFactoryReference",
        "EnvironmentRunExtensionFactoryRegistration",
        "EnvironmentSource",
        "build_environment_run_extension_factory_catalog",
        "discover_environment_run_extension_factory_references",
    }
    expected_advanced_environment = {
        "BoundEnvironment",
        "BoundEnvironmentProvider",
        "CompositeEnvironmentRunBinding",
        "EnvironmentBindingRequest",
        "EnvironmentProviderBinding",
        "EnvironmentRunBinding",
        "EnvironmentTopologyController",
        "EnvironmentTopologyRequest",
        "NoopEnvironmentRunBinding",
        "create_environment_provider_binding",
        "create_environment_run_binding",
        "create_noop_environment_run_binding",
    }
    expected_tools = {
        "ClientToolDefinition",
        "ClientToolsCapability",
        "ClientToolsRunCapability",
        "ClientToolsSpec",
        "ClientToolsetDefinition",
        "InvocationPolicyCapability",
        "InvocationPolicyDecision",
    }

    assert expected_environment <= set(environment.__all__)
    assert expected_advanced_environment <= set(advanced_environment.__all__)
    assert set(advanced_environment.__all__).isdisjoint(harness.__all__)
    assert all(not hasattr(harness, name) for name in advanced_environment.__all__)
    assert not hasattr(environment, "BoundEnvironment")
    assert harness.AgentContext.__annotations__["environment"] == "Environment"
    assert environment.EnvironmentRunExtensionContext.__annotations__["environment"] == "Environment"
    assert removed_harness_provider_symbols.isdisjoint(environment.__all__)
    assert removed_harness_provider_symbols.isdisjoint(harness.__all__)
    assert {
        "DirectLocalProviderConfiguration",
        "DirectLocalRootConfiguration",
        "DirectLocalShellProfile",
        "EnvironmentProvider",
        "EnvironmentProviderFactory",
        "EnvironmentProviderFactoryCatalog",
        "EnvironmentProviderSpec",
        "EnvironmentResource",
    } <= set(environment_provider.__all__)
    assert expected_tools <= set(tools.__all__)
    assert "InvocationAuthorizationCapability" not in tools.__all__
    assert "InvocationAuthorizationToolset" not in tools.__all__
