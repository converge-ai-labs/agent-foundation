"""Coverage and real model-adapter checks for the explicit platform catalog."""

import json

import pytest
from a13n_service.platform_catalog import catalog
from a13n_service.platform_tools import validate_catalog
from jsonschema import Draft202012Validator
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel, _function_declaration_from_tool
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

# Every required capability must remain selectable through the OSS platform catalog.
REQUIRED_TOOL_NAMES = {
    "list_agents",
    "create_agent",
    "validate_agent_configuration",
    "get_agent",
    "update_agent_metadata",
    "archive_agent",
    "duplicate_agent",
    "list_agent_revisions",
    "update_agent_configuration",
    "get_agent_revision",
    "compare_agent_revisions",
    "set_default_agent_revision",
    "unarchive_agent",
    "list_toolsets",
    "get_toolset_definition",
    "search_toolsets",
    "get_media_defaults",
    "get_model_catalog",
    "list_models",
    "create_model",
    "get_model",
    "update_model",
    "list_connections",
    "create_connection",
    "get_connection",
    "update_connection",
    "revoke_connection",
    "test_connection",
    "list_connection_tools",
    "get_connection_tool_definition",
    "search_connection_tools",
    "list_connector_apps",
    "get_connector_app",
    "list_connector_actions",
    "get_connector_action_definition",
    "search_connector_actions",
    "list_mcp_servers",
    "list_skills",
    "create_skill",
    "validate_skill_source",
    "get_skill",
    "update_skill_metadata",
    "archive_skill",
    "list_skill_revisions",
    "update_skill_source",
    "get_skill_revision",
    "set_default_skill_revision",
    "unarchive_skill",
    "list_environment_templates",
    "create_environment_template",
    "get_environment_template",
    "update_environment_template",
    "list_browser_providers",
    "get_browser_provider",
    "test_browser_provider",
    "list_connector_providers",
    "get_connector_provider",
    "test_connector_provider",
    "list_environment_providers",
    "get_environment_provider",
    "test_environment_provider",
    "list_model_providers",
    "get_model_provider",
    "discover_model_provider_models",
    "test_model_provider",
    "list_web_providers",
    "get_web_provider",
    "test_web_provider",
    "list_finding_analyses",
    "list_findings",
    "create_finding",
    "get_finding",
    "list_runs",
    "get_run",
    "get_run_configuration",
    "list_run_attempts",
    "get_run_attempt",
    "list_attempt_spans",
    "read_content",
    "list_run_items",
    "get_run_lineage",
    "list_sessions",
    "get_session",
    "get_message_authors",
    "list_session_threads",
    "list_threads",
    "get_thread",
    "list_inbox",
    "get_inbox_entry",
    "list_thread_runs",
    "list_tool_executions",
    "get_tool_execution",
    "get_tool_execution_result",
    "get_trace_backend",
    "list_traces",
    "search_traces",
    "get_trace",
    "list_trace_spans",
    "get_trace_span",
    "list_schedules",
    "create_schedule",
    "preview_schedule",
    "archive_schedule",
    "get_schedule",
    "update_schedule",
    "dispatch_schedule",
    "enable_schedule",
    "list_schedule_occurrences",
    "get_schedule_occurrence",
    "pause_schedule",
    "get_schedule_status",
}


def test_catalog_keeps_required_capabilities_and_valid_input_schemas():
    tools = catalog()
    validate_catalog(tools)
    assert REQUIRED_TOOL_NAMES <= {tool.name for tool in tools}
    for tool in tools:
        schema = tool.inputs.model_json_schema()
        Draft202012Validator.check_schema(schema)
        assert "request_body" not in schema["properties"]
        if "expected_version" in schema["properties"]:
            assert "expected_version" in schema["required"]


@pytest.mark.parametrize("route", ["openai-chat", "openai-responses", "anthropic", "google"])
def test_catalog_uses_real_provider_preparation_and_serialization(route):
    # Fixture credentials never leave the process: these are the SDK's real request
    # preparation/serialization paths, without making an external model request.
    if route == "openai-chat":
        model = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="fixture"))
    elif route == "openai-responses":
        model = OpenAIResponsesModel("gpt-5", provider=OpenAIProvider(api_key="fixture"))
    elif route == "anthropic":
        model = AnthropicModel("claude-sonnet-4-5", provider=AnthropicProvider(api_key="fixture"))
    else:
        model = GoogleModel("gemini-2.5-pro", provider=GoogleProvider(api_key="fixture"))
    for contract in catalog():
        tool = ToolDefinition(
            name=contract.name,
            description=contract.description,
            parameters_json_schema=contract.inputs.model_json_schema(),
        )
        _, prepared = model.prepare_request(None, ModelRequestParameters(function_tools=[tool]))
        mapped = prepared.function_tools[0]
        if isinstance(model, OpenAIChatModel):
            wire = model._map_tool_definition(mapped, {})
        elif isinstance(model, OpenAIResponsesModel):
            wire = model._map_tool_definition(mapped, visibility="public")
        elif isinstance(model, AnthropicModel):
            wire = model._map_tool_definition(mapped, {}, visibility="public")
        else:
            wire = _function_declaration_from_tool(mapped)
        encoded = json.dumps(wire, allow_nan=False)
        assert contract.name in encoded
        assert len(encoded) > 50
