from __future__ import annotations

import a13n_environment as environment_provider
import a13n_harness as harness
import a13n_harness.capabilities as capabilities
import a13n_harness.capability_types as capability_types
import a13n_harness.environment as environment
import a13n_harness.environment.advanced as advanced_environment
import a13n_harness.filters as filters
import a13n_harness.models as models
import a13n_harness.pricing as pricing
import a13n_harness.providers.model.oauth as model_auth
import a13n_harness.tools as tools
import a13n_harness.toolsets as toolsets


def test_root_facade_is_the_exact_primary_code_first_api() -> None:
    expected = {
        "AbstractHarnessPlugin",
        "AgentContext",
        "AgentDefinition",
        "AgentIdentityRef",
        "AgentInstanceContext",
        "AgentInstanceRef",
        "AgentSpec",
        "AgentStreamEventProtocol",
        "DefinitionError",
        "DeferredToolResume",
        "DelegationContextPolicy",
        "Environment",
        "EnvironmentAccess",
        "EnvironmentEntry",
        "EnvironmentMount",
        "ExecutableAgent",
        "HarnessBuilder",
        "HarnessError",
        "HarnessEvent",
        "HarnessExtensionEvent",
        "HarnessInstrumentation",
        "HarnessModelCharacteristics",
        "HarnessObservationContext",
        "HarnessRunResult",
        "HarnessRunResultEvent",
        "HarnessRunStream",
        "HarnessState",
        "HarnessStreamEvent",
        "HarnessTraceContent",
        "IdentityError",
        "InputError",
        "ModelCapability",
        "ModelRecoveryPolicy",
        "ModelResolutionError",
        "NativeRunInput",
        "PluginError",
        "PluginOrdering",
        "RunBindings",
        "RunCleanupError",
        "RunError",
        "RunInputFactory",
        "RunInputValue",
        "RunModelResolver",
        "RunPreparationContext",
        "SafeFailure",
        "SemanticRunInput",
        "StateError",
        "SubagentDefinition",
        "SubagentIdentityPolicy",
        "ToolRecoveryMode",
        "__version__",
        "derive_child_identity",
        "infer_model",
    }

    assert expected == set(harness.__all__)
    assert all(hasattr(harness, name) for name in expected)
    assert not hasattr(harness.HarnessBuilder, "build_code")
    assert not hasattr(harness.ExecutableAgent, "close")
    assert not hasattr(harness.ExecutableAgent, "__aenter__")
    assert hasattr(harness.RunBindings, "embedded")
    assert not hasattr(harness.RunBindings, "local")

    specialized = {
        "CapabilityTypeCatalog",
        "CatalogModelCostCapability",
        "EnvironmentRunExtensionFactory",
        "RuntimeContextCapability",
        "create_model_http_client",
        "get_current_pricing_catalog",
        "get_default_pricing_catalog",
    }
    assert specialized.isdisjoint(harness.__all__)
    assert all(not hasattr(harness, name) for name in specialized)
    assert capability_types.CapabilityTypeCatalog is not None
    assert pricing.CatalogModelCostCapability is not None
    assert environment.EnvironmentRunExtensionFactory is not None
    assert capabilities.RuntimeContextCapability is not None
    assert models.create_model_http_client is not None
    assert pricing.get_default_pricing_catalog is not None
    assert pricing.get_current_pricing_catalog is not None
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
        "FileContextCapability",
        "FileSkillSource",
        "HandoffCapability",
        "AsyncDelegateRequest",
        "AsyncExecutionView",
        "AsyncResumeRequest",
        "InlineSubagentCollectionState",
        "InlineSubagentState",
        "MAX_SUBAGENT_ACTIVITY_OUTPUT_CHARS",
        "MediaCapability",
        "RuntimeContextCapability",
        "AgentToolReviewer",
        "ToolReviewer",
        "ToolReviewAssessment",
        "ToolReviewConfig",
        "ToolReviewError",
        "ToolReviewPolicy",
        "ToolReviewRequest",
        "ToolReviewResult",
        "ToolReviewRule",
        "ToolRiskLevel",
        "ResolvedDelegationContext",
        "SubagentActivitySnapshot",
        "SubagentCancelRequest",
        "SubagentCancelResult",
        "SubagentCapability",
        "SubagentDelegationPlan",
        "SubagentExecutionView",
        "SubagentInfoRequest",
        "SubagentInfoResult",
        "SubagentOperator",
        "SubagentOperatorContext",
        "SubagentToolCallContext",
        "SubagentStatus",
        "SubagentSteerRequest",
        "SubagentSteerResult",
        "SubagentToolCallSnapshot",
        "SubagentToolCallStatus",
        "SubagentWaitRequest",
        "SubagentWaitResult",
        "SkillsCapability",
        "TaskStateBinding",
        "ToolProxyCapability",
        "ToolProxyConfig",
        "ToolProxyGroup",
        "UserInteractionCapability",
        "WEB_SCRAPE_BACKEND_ENV",
        "WEB_SCRAPE_BACKEND_PRIORITY_ENV",
        "WEB_SCRAPE_MODE_ENV",
        "WEB_SEARCH_BACKEND_ENV",
        "WEB_SEARCH_BACKEND_PRIORITY_ENV",
        "WEB_SEARCH_CONTEXT_SIZE_ENV",
        "WEB_SEARCH_MODE_ENV",
        "WebCapability",
        "WebConfiguration",
        "WebBinding",
        "WebScrapeBackendBinding",
        "WebScrapeConfiguration",
        "WebSearchBackendBinding",
        "WebSearchConfiguration",
        "WorkingStateCapability",
    }
    expected_toolsets = {
        "AUDIO_UNDERSTANDING_MODEL_ENV",
        "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV",
        "IMAGE_UNDERSTANDING_MODEL_ENV",
        "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV",
        "VIDEO_UNDERSTANDING_MODEL_ENV",
        "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV",
        "AgentMediaUnderstandingProvider",
        "AsyncSubagentToolset",
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
        "MediaUnderstandingError",
        "MediaUnderstandingProvider",
        "MediaUnderstandingRequest",
        "MediaUnderstandingResult",
        "NativeInputMediaKind",
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


def test_model_auth_feature_facade_is_public_without_root_reexports() -> None:
    expected = {
        "CodexDeviceAuthorization",
        "CodexDeviceAuthorizationFlow",
        "DeviceAuthorizationError",
        "CodexLoginFlow",
        "CodexLoginResult",
        "CredentialPersistenceError",
        "CredentialRefreshError",
        "RefreshNotDispatched",
        "ProcessGrokCredentialSource",
        "GrokCredentialSource",
        "GrokCredentials",
        "GrokRefresh",
        "GrokDeviceAuthorization",
        "GrokDeviceAuthorizationFlow",
        "GrokOAuthFlow",
        "ModelAuthenticationError",
        "OAuthFlow",
        "build_grok_model",
        "refresh_grok_credentials",
    }

    assert expected == set(model_auth.__all__)
    assert all(hasattr(model_auth, name) for name in expected)
    assert expected.isdisjoint(harness.__all__)


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
        "EnvironmentRunCallback",
        "EnvironmentRunCallbacks",
        "EnvironmentRunExtension",
        "EnvironmentRunExtensionContext",
        "EnvironmentRunExtensionFactory",
        "EnvironmentRunExtensionFactoryCatalog",
        "EnvironmentRunExtensionFactoryContext",
        "EnvironmentRunExtensionFactoryReference",
        "EnvironmentRunExtensionFactoryRegistration",
        "build_environment_run_extension_factory_catalog",
        "discover_environment_run_extension_factory_references",
    }
    expected_advanced_environment = {
        "BoundEnvironment",
        "CompositeBoundEnvironment",
        "EmptyEnvironmentRuntime",
        "EnvironmentRuntime",
        "ManagedEnvironmentRuntime",
        "NoopBoundEnvironment",
        "create_empty_environment_runtime",
        "create_environment_runtime",
    }
    expected_tools = {
        "ClientToolDefinition",
        "ClientToolsCapability",
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
        "Environment",
        "EnvironmentProvider",
        "EnvironmentProviderCatalog",
        "EnvironmentProviderReference",
        "EnvironmentProviderRegistration",
        "EnvironmentProviderSpec",
        "EnvironmentState",
        "build_environment_provider_catalog",
        "discover_environment_provider_references",
    } <= set(environment_provider.__all__)
    assert {
        "EnvironmentProviderFactory",
        "EnvironmentProviderFactoryCatalog",
        "EnvironmentResource",
    }.isdisjoint(environment_provider.__all__)
    assert expected_tools <= set(tools.__all__)
    assert "InvocationAuthorizationCapability" not in tools.__all__
    assert "InvocationAuthorizationToolset" not in tools.__all__


def test_memory_has_one_public_capability_and_independent_backend_contract():
    from a13n_harness.capabilities.memory import MemoryCapability
    from a13n_harness.memory import MemoryBackend, MemoryScope
    from a13n_harness.memory_plugins import MemoryBackendCatalog, MemoryBackendPlugin

    assert capabilities.MemoryCapability is MemoryCapability
    assert capabilities.MemoryScope is MemoryScope
    assert MemoryCapability.id == "a13n.memory"
    assert not hasattr(capabilities, "Mem0Capability")
    assert not hasattr(capabilities, "MemoryRunCapability")
    assert MemoryBackend.__abstractmethods__ == {"search", "list", "add", "get", "update", "delete"}
    assert MemoryBackendPlugin is not harness.AbstractHarnessPlugin
    assert MemoryBackendCatalog() == {}
