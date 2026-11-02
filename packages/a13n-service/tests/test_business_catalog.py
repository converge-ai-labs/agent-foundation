"""Coverage and real model-adapter checks for the explicit business catalog."""

import json

import pytest
from a13n_service.business_catalog import catalog
from a13n_service.business_tools import validate_catalog
from jsonschema import Draft202012Validator
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel, _function_declaration_from_tool
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

# Acceptance inventory, not dispatch aliases. The removed names never enter the runtime catalog.
DISPOSITION = {
    "list_agents": "list_agents",
    "create_agent": "create_agent",
    "validate_agent": "validate_agent_configuration",
    "get_agent": "get_agent",
    "update_agent": "update_agent_metadata",
    "archive_agent": "archive_agent",
    "duplicate_agent": "duplicate_agent",
    "list_agent_revisions": "list_agent_revisions",
    "create_agent_revision": "update_agent_configuration",
    "get_agent_revision": "get_agent_revision",
    "compare_agent_revisions": "compare_agent_revisions",
    "set_default_agent_revision": "set_default_agent_revision",
    "unarchive_agent": "unarchive_agent",
    "list_toolsets": "list_toolsets",
    "define_toolsets": "get_toolset_definition",
    "index_toolsets": "search_toolsets",
    "get_media_defaults": "get_media_defaults",
    "get_model_catalog": "get_model_catalog",
    "list_models": "list_models",
    "create_model": "create_model",
    "get_model": "get_model",
    "update_model": "update_model",
    "list_connections": "list_connections",
    "create_connection": "create_connection",
    "get_connection": "get_connection",
    "update_connection": "update_connection",
    "revoke_connection": "revoke_connection",
    "test_connection": "test_connection",
    "list_connection_tools": "list_connection_tools",
    "define_connection_tools": "get_connection_tool_definition",
    "index_connection_tools": "search_connection_tools",
    "list_connector_apps": "list_connector_apps",
    "get_connector_app": "get_connector_app",
    "list_connector_actions": "list_connector_actions",
    "define_connector_actions": "get_connector_action_definition",
    "index_connector_actions": "search_connector_actions",
    "list_mcp_servers": "list_mcp_servers",
    "list_skills": "list_skills",
    "create_skill": "create_skill",
    "validate_skill": "validate_skill_source",
    "get_skill": "get_skill",
    "update_skill": "update_skill_metadata",
    "archive_skill": "archive_skill",
    "list_skill_revisions": "list_skill_revisions",
    "create_skill_revision": "update_skill_source",
    "get_skill_revision": "get_skill_revision",
    "set_default_skill_revision": "set_default_skill_revision",
    "unarchive_skill": "unarchive_skill",
    "list_environment_templates": "list_environment_templates",
    "create_environment_template": "create_environment_template",
    "get_environment_template": "get_environment_template",
    "update_environment_template": "update_environment_template",
    "list_browser_providers": "list_browser_providers",
    "get_browser_provider": "get_browser_provider",
    "test_browser_provider": "test_browser_provider",
    "list_connector_providers": "list_connector_providers",
    "get_connector_provider": "get_connector_provider",
    "test_connector_provider": "test_connector_provider",
    "list_environment_providers": "list_environment_providers",
    "get_environment_provider": "get_environment_provider",
    "test_environment_provider": "test_environment_provider",
    "list_model_providers": "list_model_providers",
    "get_model_provider": "get_model_provider",
    "discover_model_provider_models": "discover_model_provider_models",
    "test_model_provider": "test_model_provider",
    "list_web_providers": "list_web_providers",
    "get_web_provider": "get_web_provider",
    "test_web_provider": "test_web_provider",
    "list_finding_analyses": "list_finding_analyses",
    "list_findings": "list_findings",
    "create_finding": "create_finding",
    "get_finding": "get_finding",
    "list_runs": "list_runs",
    "get_run": "get_run",
    "get_run_agent": "get_run_configuration",
    "list_run_attempts": "list_run_attempts",
    "get_run_attempt": "get_run_attempt",
    "list_attempt_spans": "list_attempt_spans",
    "get_run_content": "read_content",
    "list_run_items": "list_run_items",
    "read_run_item_content": "read_content",
    "get_run_lineage": "get_run_lineage",
    "list_sessions": "list_sessions",
    "get_session": "get_session",
    "get_message_authors": "get_message_authors",
    "list_session_threads": "list_session_threads",
    "list_threads": "list_threads",
    "get_thread": "get_thread",
    "list_inbox": "list_inbox",
    "get_inbox_entry": "get_inbox_entry",
    "list_thread_runs": "list_thread_runs",
    "list_tool_executions": "list_tool_executions",
    "get_tool_execution": "get_tool_execution",
    "get_tool_execution_result": "get_tool_execution_result",
    "get_trace_backend": "get_trace_backend",
    "list_traces": "list_traces",
    "search_traces": "search_traces",
    "get_trace": "get_trace",
    "list_trace_spans": "list_trace_spans",
    "get_trace_span": "get_trace_span",
    "read_span_content": "read_content",
    "list_schedules": "list_schedules",
    "create_schedule": "create_schedule",
    "preview_schedule": "preview_schedule",
    "archive_schedule": "archive_schedule",
    "get_schedule": "get_schedule",
    "update_schedule": "update_schedule",
    "dispatch_schedule": "dispatch_schedule",
    "enable_schedule": "enable_schedule",
    "list_schedule_occurrences": "list_schedule_occurrences",
    "get_schedule_occurrence": "get_schedule_occurrence",
    "pause_schedule": "pause_schedule",
    "get_schedule_status": "get_schedule_status",
}


def test_every_baseline_capability_has_an_explicit_business_destination():
    tools = catalog()
    validate_catalog(tools)
    assert len(DISPOSITION) == 113
    assert set(DISPOSITION.values()) <= {tool.name for tool in tools}
    for tool in tools:
        schema = tool.inputs.model_json_schema()
        Draft202012Validator.check_schema(schema)
        assert "request_body" not in schema["properties"]
        if "expected_version" in schema["properties"]:
            assert "expected_version" in schema["required"]
    removed = {name for name, destination in DISPOSITION.items() if name != destination}
    assert not removed & {tool.name for tool in tools}


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
