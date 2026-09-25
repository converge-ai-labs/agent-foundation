"""Allowlisted configuration presentation, separate from executable recipes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.configuration.models import AgentToolProxy, SidekickConfiguration
from a13n_harness_ui.configuration.views import AgentToolProxyView
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.model_fast import FastState, fast_state
from a13n_harness_ui.model_reasoning_mode import ReasoningModeState, reasoning_mode_state
from a13n_harness_ui.model_thinking import summarize_thinking
from a13n_harness_ui.surfaces import SurfaceModel, ThreadConfigurationResolution


class CapturedAgentSelection(SurfaceModel):
    name: str
    source_kind: Literal["agent", "markdown"]
    source_id: str
    model_id: str
    thinking_summary: str | None = None
    fast: FastState = "default"
    reasoning_mode: ReasoningModeState = "default"


class CapturedConfiguration(SurfaceModel):
    composition_id: str
    generation_digest: str
    thread_configuration_version: int
    project_id: str | None
    project_roots: tuple[str, ...]
    webui_sidekick: SidekickConfiguration | None = None
    memory_enabled: bool = False
    memory_scopes: tuple[str, ...] = ()
    agent: CapturedAgentSelection
    capability_ids: tuple[str, ...]
    harness_plugin_ids: tuple[str, ...]
    mcp_server_ids: tuple[str, ...]
    environment_profile_id: str
    environment_provider: str
    environment_adapter: str
    environment_bindings: tuple[EnvironmentBindingSelection, ...] = ()
    default_environment: str | None = None
    environment_run_extension_ids: tuple[str, ...]
    tools: tuple[str, ...] | None
    tool_proxy: AgentToolProxy | None
    children: tuple[CapturedAgentSelection, ...] = Field(max_length=100)
    omitted_children: int = Field(ge=0)
    omitted_fields: tuple[str, ...] = (
        "instructions",
        "system_prompt",
        "global_guidance",
        "authentication",
        "model_settings",
        "model_configuration",
        "capability_configuration",
        "plugin_configuration",
        "mcp_transport",
        "environment_configuration",
        "child_definitions",
        "dependency_imports",
    )


def captured_configuration(composition_id: str, value: ResolvedRunComposition) -> CapturedConfiguration:
    root = value.root
    return CapturedConfiguration(
        composition_id=composition_id,
        generation_digest=value.generation_digest,
        thread_configuration_version=value.thread_configuration_version,
        project_id=value.project_id,
        project_roots=value.project_roots,
        webui_sidekick=value.webui_sidekick,
        memory_enabled=value.memory_enabled,
        memory_scopes=(
            ("global", *((f"project:{value.project_id}",) if value.project_id is not None else ()))
            if value.memory_enabled
            else ()
        ),
        agent=CapturedAgentSelection(
            name=root.roster_name,
            source_kind=root.source_kind,
            source_id=root.source_id,
            model_id=root.model.model_id,
            thinking_summary=summarize_thinking(root.model.route, root.model.settings),
            fast=fast_state(root.model.route, root.model.settings),
            reasoning_mode=reasoning_mode_state(root.model.route, root.model.settings),
        ),
        capability_ids=tuple(item.capability for item in root.capabilities),
        harness_plugin_ids=tuple(item.plugin_id for item in root.harness_plugins),
        mcp_server_ids=tuple(item.server_id for item in root.mcp_servers),
        environment_profile_id=value.environment_profile.profile_id,
        environment_provider=value.environment_profile.provider_key,
        environment_adapter=value.environment_profile.adapter_key,
        environment_bindings=tuple(item.selection for item in value.environment_bindings),
        default_environment=value.default_environment,
        environment_run_extension_ids=tuple(item.extension_id for item in value.environment_run_extensions),
        tools=root.tools,
        tool_proxy=root.tool_proxy,
        children=tuple(
            CapturedAgentSelection(
                name=child.name,
                source_kind=child.source_kind,
                source_id=child.source_id,
                model_id=child.definition.model.model_id,
                thinking_summary=summarize_thinking(child.definition.model.route, child.definition.model.settings),
                fast=fast_state(child.definition.model.route, child.definition.model.settings),
                reasoning_mode=reasoning_mode_state(child.definition.model.route, child.definition.model.settings),
            )
            for child in root.children[:100]
        ),
        omitted_children=max(0, len(root.children) - 100),
    )


class ThreadConfigurationInspection(SurfaceModel):
    thread_id: str
    next_run: ThreadConfigurationResolution
    next_generation_digest: str | None
    next_model_id: str | None
    next_capability_ids: tuple[str, ...]
    next_tool_proxy: AgentToolProxyView | None
    captured: CapturedConfiguration | None
    capture_source: Literal["active_operation", "selected_continuation", "none"]
    receipt_id: str | None = None
    run_id: str | None = None
    continuation_id: str | None = None
