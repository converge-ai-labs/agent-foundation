"""Validated, last-write-wins first-use configuration publication.

The root defaults are published last. A failed multi-file publication reports its
completed paths; it is deliberately not presented as a filesystem transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal, Self

import yaml
from a13n_harness.spec import HarnessModelCharacteristics
from anyio import to_thread
from pydantic import ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.errors import ConfigurationError, HarnessUiError
from a13n_harness_ui.model_presets import known_model_capabilities, starter_tool_capabilities
from a13n_harness_ui.resource_names import coding_agent_name, model_name
from a13n_harness_ui.subagents import BUILTIN_SUBAGENT_NAMES

from .loader import _parse_yaml_mapping, _read_bounded_stable, _scan_directory, load_harness_ui_configuration
from .models import (
    ApiKeyAuthentication,
    CapabilitySelection,
    ModelCharacteristics,
    ResourceId,
    StrictModel,
    ToolsConfiguration,
)
from .mutation import CandidateValidator, _publish_content

_EMPTY_ROOT = b'schema_version: "1"\n'
_DIRECTORIES = ("models", "extensions", "mcp", "agents", "projects", "subagents")


class SetupApiKeyModel(StrictModel):
    route: str = Field(min_length=1, max_length=512)
    authentication: ApiKeyAuthentication
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_characteristics: ModelCharacteristics = Field(
        default_factory=lambda: HarnessModelCharacteristics(context_window=350000)
    )


class SetupSelection(StrictModel):
    providers: tuple[Literal["codex", "grok"], ...] = ()
    api_key_model: SetupApiKeyModel | None = None
    instructions: str = Field(default="", max_length=1024 * 1024)
    new_agent_id: ResourceId | None = None
    new_agent_name: str = Field(default="", max_length=128)
    new_model_id: ResourceId | None = None
    new_model_name: str = Field(default="", max_length=128)
    existing_model_id: ResourceId | None = None
    connect_default: bool = False
    tool_capabilities: tuple[CapabilitySelection, ...] | None = None
    default_agent: ResourceId = "agent-default"
    project: ResourceId | None = None
    project_path: str | None = Field(default=None, min_length=1, max_length=4096)
    environment_profile: Literal["environment-native", "environment-sandbox"]
    shell_review: bool = True
    include_default_subagents: bool | None = None
    codex_model: Literal["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"] = "gpt-5.6-sol"

    grok_model: Literal["grok-4.6", "grok-4.5", "grok-4.20-0309-reasoning"] = "grok-4.6"

    codex_thinking: Literal["low", "medium", "high", "xhigh"] = "high"
    codex_service_tier: Literal["priority", "default"] | None = None
    codex_context_window: int = Field(default=350000, ge=16000, le=872000)
    proactive_context_management_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)

    @property
    def is_addition(self) -> bool:
        return self.new_agent_id is not None or self.new_model_id is not None

    @model_validator(mode="after")
    def _creation_target(self) -> Self:
        if (self.project is None) != (self.project_path is None):
            raise ValueError("An optional setup Project requires both project and project_path")
        if self.new_model_id is not None and (self.new_agent_id is not None or self.existing_model_id is not None):
            raise ValueError("Add Model cannot also create an Agent or select an existing Model")
        if self.new_model_id is not None and self.tool_capabilities is not None:
            raise ValueError("Tools belong to an Agent; configure them when adding an Agent for this Model")
        if self.existing_model_id is not None and (
            self.new_agent_id is None or self.providers or self.api_key_model is not None
        ):
            raise ValueError("An existing Model can only be selected when adding an Agent without a new connection")
        return self


class SetupPreview(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=False)

    files: dict[str, str]
    preserved_paths: tuple[str, ...]
    project_paths: tuple[str, ...]


class SetupPublication(StrictModel):
    completed: bool
    published_paths: tuple[str, ...]
    error_code: str | None = None
    error_message: str | None = None


def _capture(path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for source in (path, path.parent / "AGENTS.md"):
        if os.path.lexists(source):
            entries[source.name], _fingerprint = _read_bounded_stable(source, 1024 * 1024)
    for directory in _DIRECTORIES:
        for relative, source, _fingerprint in _scan_directory(
            path.parent / directory, markdown=directory == "subagents"
        ):
            entries[relative], _fingerprint = _read_bounded_stable(source, 1024 * 1024)
            if len(entries) > 2048 or sum(map(len, entries.values())) > 16 * 1024 * 1024:
                raise ConfigurationError("Configuration exceeds setup limits.", code="configuration_source_limit")
    return entries


def _model_characteristics(route: str, authored: HarnessModelCharacteristics | None = None) -> dict[str, JsonValue]:
    """Seed only omitted media capabilities; preserve every authored policy field."""
    characteristics = authored if authored is not None else HarnessModelCharacteristics()
    if "capabilities" not in characteristics.model_fields_set:
        known = known_model_capabilities(route)
        if known is not None:
            characteristics = characteristics.model_copy(update={"capabilities": known})
    document = characteristics.model_dump(mode="json")
    # frozenset iteration differs between processes; previews must have stable bytes.
    document["capabilities"] = sorted(capability.value for capability in characteristics.capabilities)
    return document


def _templates(selection: SetupSelection, *, existing_model: dict[str, object] | None = None) -> dict[str, str]:
    resources: dict[str, dict[str, object]] = {}
    providers = selection.providers
    if existing_model is not None:
        authentication = existing_model.get("authentication")
        kind = authentication.get("kind") if isinstance(authentication, dict) else None
        providers = ("codex",) if kind == "codex_subscription" else ("grok",) if kind == "grok_subscription" else ()
    shell_review = selection.shell_review and selection.new_model_id is None
    for provider in dict.fromkeys(providers):
        codex = provider == "codex"
        model = selection.codex_model if codex else selection.grok_model
        display_name = model_name("codex" if codex else "grok-subscription", model)
        resources[f"models/{provider}.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": f"model-{provider}",
            "name": display_name,
            "route": f"{'openai-codex' if codex else 'grok'}:{model}",
            "authentication": {"kind": f"{provider}_subscription"},
            "settings": {
                "thinking": selection.codex_thinking,
                "openai_reasoning_summary": "detailed",
                "openai_store": False,
                **(
                    {"openai_service_tier": selection.codex_service_tier}
                    if selection.codex_service_tier is not None
                    else {}
                ),
            }
            if codex
            else {},
            "model_characteristics": _model_characteristics(
                f"{'openai-codex' if codex else 'grok'}:{model}",
                HarnessModelCharacteristics(
                    context_window=selection.codex_context_window,
                    proactive_context_management_threshold=selection.proactive_context_management_threshold,
                    compact_threshold=selection.compact_threshold,
                )
                if codex
                else None,
            ),
        }
        capabilities: list[dict[str, object]] = [
            {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
            {"capability": "skills", "configuration": {}},
            {"capability": "compaction", "configuration": {}},
            {"capability": "handoff", "configuration": {}},
            {"capability": "runtime_context", "configuration": {}},
        ]
        resources[f"agents/{provider}.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": f"agent-{provider}",
            "name": coding_agent_name(display_name),
            "model": f"model-{provider}",
            "capabilities": capabilities,
        }
    if shell_review and "codex" in providers:
        resources["models/codex-review.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": "model-codex-review",
            "name": "Codex shell review",
            "route": "openai-codex:gpt-5.6-luna",
            "authentication": {"kind": "codex_subscription"},
            "settings": {"thinking": "low"},
            "model_characteristics": _model_characteristics("openai-codex:gpt-5.6-luna"),
        }
    if shell_review and "grok" in providers and "codex" not in providers:
        resources["models/grok-shell-review.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": "model-grok-shell-review",
            "name": "Grok shell review",
            "route": "grok:grok-4.6",
            "authentication": {"kind": "grok_subscription"},
            "settings": {"thinking": "low"},
            "model_characteristics": _model_characteristics("grok:grok-4.6"),
        }
    if selection.api_key_model is not None:
        api_provider, _, api_model = selection.api_key_model.route.partition(":")
        display_name = model_name(api_provider, api_model)
        resources["models/api-key.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": "model-api-key",
            "name": display_name,
            "route": selection.api_key_model.route,
            "authentication": selection.api_key_model.authentication.model_dump(mode="json"),
            "settings": selection.api_key_model.settings,
            "model_configuration": selection.api_key_model.model_configuration,
            "model_characteristics": _model_characteristics(
                selection.api_key_model.route, selection.api_key_model.model_characteristics
            ),
        }
        resources["agents/api-key.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-api-key",
            "name": coding_agent_name(display_name),
            "model": "model-api-key",
            "capabilities": [
                {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
                {"capability": "skills", "configuration": {}},
                {"capability": "compaction", "configuration": {}},
                {"capability": "handoff", "configuration": {}},
                {"capability": "runtime_context", "configuration": {}},
            ],
        }
    if not providers and selection.api_key_model is None:
        resources["agents/default.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-default",
            "name": "Default Agent",
            **({"model": selection.existing_model_id} if selection.existing_model_id is not None else {}),
            "capabilities": [
                {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
                {"capability": "skills", "configuration": {}},
                {"capability": "compaction", "configuration": {}},
                {"capability": "handoff", "configuration": {}},
                {"capability": "runtime_context", "configuration": {}},
            ],
        }
    if selection.new_agent_id is not None:
        agents = [value for value in resources.values() if value["kind"] == "agent"]
        if len(agents) != 1 or not agents[0].get("model") or not selection.new_agent_name.strip():
            raise ConfigurationError("Adding an Agent requires one connection and a name.", code="setup_agent_invalid")
        agent = agents[0]
        model = next((value for value in resources.values() if value["id"] == agent.get("model")), None)
        suffix = selection.new_agent_id.removeprefix("agent-")
        resources = {key: value for key, value in resources.items() if value is not agent and value is not model}
        agent.update(
            id=selection.new_agent_id,
            name=selection.new_agent_name,
            model=selection.existing_model_id or f"model-agent-{suffix}",
        )
        resources[f"agents/{suffix}.yaml"] = agent
        if selection.existing_model_id is None:
            assert model is not None
            model.update(id=f"model-agent-{suffix}")
            resources[f"models/agent-{suffix}.yaml"] = model
    if selection.new_model_id is not None:
        models = [value for value in resources.values() if value["kind"] == "model"]
        if len(models) != 1 or not selection.new_model_name.strip():
            raise ConfigurationError("Adding a Model requires one connection and a name.", code="setup_model_invalid")
        model = models[0]
        model.update(id=selection.new_model_id, name=selection.new_model_name)
        resources = {f"models/{selection.new_model_id.removeprefix('model-')}.yaml": model}
    for resource in resources.values():
        if resource["kind"] != "agent":
            continue
        selected_model = existing_model or next(
            (item for item in resources.values() if item["id"] == resource.get("model")), {}
        )
        route = selected_model.get("route", "")
        authentication = selected_model.get("authentication", {})
        configuration = selected_model.get("model_configuration", {})
        kind = authentication.get("kind") if isinstance(authentication, dict) else None
        base_url = configuration.get("base_url") if isinstance(configuration, dict) else None
        agent_capabilities = resource["capabilities"]
        assert isinstance(agent_capabilities, list) and isinstance(route, str)
        agent_capabilities.extend(
            [capability.model_dump(mode="json") for capability in selection.tool_capabilities]
            if selection.tool_capabilities is not None
            else starter_tool_capabilities(route, authentication=kind, base_url=base_url)
        )
    if selection.instructions.strip():
        for resource in resources.values():
            if resource["id"] == (selection.new_agent_id or selection.default_agent):
                resource["instructions"] = selection.instructions
    if not selection.is_addition and selection.project is not None:
        resources[f"projects/{selection.project}.yaml"] = {
            "schema_version": "1",
            "kind": "project",
            "id": selection.project,
            "name": "Local project",
            "roots": [{"path": selection.project_path}],
        }
    return {name: yaml.safe_dump(value, sort_keys=False, allow_unicode=True) for name, value in resources.items()}


def _same_connection(actual: dict[str, object], desired: dict[str, object]) -> bool:
    def authentication(resource: dict[str, object]) -> object:
        value = resource.get("authentication")
        return {key: item for key, item in value.items() if item is not None} if isinstance(value, dict) else value

    return (
        actual.get("route") == desired.get("route")
        and authentication(actual) == authentication(desired)
        and actual.get("model_configuration", {}) == desired.get("model_configuration", {})
    )


async def preview_setup(
    path: Path,
    selection: SetupSelection,
    *,
    validate_candidate: CandidateValidator,
    content_plugin_root: Path | None = None,
) -> SetupPreview:
    baseline = await to_thread.run_sync(_capture, path)
    existing: dict[str, tuple[str, dict[str, object]]] = {}
    for name, content in baseline.items():
        if name != path.name and name.endswith(".yaml"):
            resource = _parse_yaml_mapping(path.parent / name, content, code="configuration_resource_invalid")
            if isinstance(resource, dict) and isinstance(resource.get("id"), str):
                existing[resource["id"]] = (name, resource)
    if selection.is_addition and path.name not in baseline:
        raise ConfigurationError("Run setup before adding resources.", code="setup_required")
    selected_model = existing.get(selection.existing_model_id) if selection.existing_model_id is not None else None
    if selection.existing_model_id is not None and (selected_model is None or selected_model[1].get("kind") != "model"):
        raise ConfigurationError("The selected Model is unavailable.", code="setup_model_invalid")
    root = _parse_yaml_mapping(path, baseline.get(path.name, _EMPTY_ROOT), code="settings_invalid")
    security = root.get("security", {})
    if not isinstance(security, dict):
        raise ConfigurationError("Configuration security must be a mapping.", code="settings_invalid")
    seed_review = selection.new_model_id is None and "shell_review" not in security
    templates = _templates(
        selection if seed_review else selection.model_copy(update={"shell_review": False}),
        existing_model=selected_model[1] if selected_model is not None else None,
    )
    connection_model: str | None = None
    if selection.new_agent_id is not None:
        connection_model = "model-" + selection.new_agent_id if selection.existing_model_id is None else None
        current_agent = existing.get(selection.new_agent_id)
        desired_agent = next(
            yaml.safe_load(text) for text in templates.values() if yaml.safe_load(text)["id"] == selection.new_agent_id
        )
        if current_agent is not None and current_agent[1] != desired_agent:
            raise ConfigurationError(
                "This Agent name is already in use. Choose another name.", code="configuration_mutation_conflict"
            )
    elif selection.new_model_id is not None:
        connection_model = selection.new_model_id
    elif selection.api_key_model is not None:
        connection_model = "model-api-key"
        old_model = existing.get(connection_model)
        if old_model is not None:
            desired = yaml.safe_load(templates["models/api-key.yaml"])
            if not _same_connection(old_model[1], desired):
                suffix = hashlib.sha256(json.dumps(desired, sort_keys=True).encode()).hexdigest()[:12]
                connection_model = f"model-api-key-{suffix}"
                desired["id"] = connection_model
                del templates["models/api-key.yaml"]
                templates[f"models/api-key-{suffix}.yaml"] = yaml.safe_dump(
                    desired, sort_keys=False, allow_unicode=True
                )
                starter = yaml.safe_load(templates["agents/api-key.yaml"])
                starter["model"] = connection_model
                templates["agents/api-key.yaml"] = yaml.safe_dump(starter, sort_keys=False, allow_unicode=True)
    elif selection.providers:
        provider = selection.default_agent.removeprefix("agent-")
        connection_model = f"model-{provider if provider in selection.providers else selection.providers[0]}"
        old_model = existing.get(connection_model)
        if old_model is not None:
            model_path, desired = next(
                (name, yaml.safe_load(text))
                for name, text in templates.items()
                if yaml.safe_load(text)["id"] == connection_model
            )
            if not _same_connection(old_model[1], desired):
                suffix = hashlib.sha256(json.dumps(desired, sort_keys=True).encode()).hexdigest()[:12]
                previous_model = connection_model
                connection_model = f"{previous_model}-{suffix}"
                desired["id"] = connection_model
                del templates[model_path]
                templates[f"models/{connection_model.removeprefix('model-')}.yaml"] = yaml.safe_dump(
                    desired, sort_keys=False, allow_unicode=True
                )
                for name, text in list(templates.items()):
                    resource = yaml.safe_load(text)
                    if resource.get("model") == previous_model:
                        resource["model"] = connection_model
                        templates[name] = yaml.safe_dump(resource, sort_keys=False, allow_unicode=True)
    if connection_model is not None and connection_model in existing:
        desired = next(
            yaml.safe_load(text) for text in templates.values() if yaml.safe_load(text)["id"] == connection_model
        )
        if (selection.is_addition and existing[connection_model][1] != desired) or not _same_connection(
            existing[connection_model][1], desired
        ):
            raise ConfigurationError(
                "The existing Model was edited and no longer matches this connection. Review the Model resource before retrying.",
                code="configuration_mutation_conflict",
            )
    files: dict[str, str] = {}
    for name, text in templates.items():
        resource_id = yaml.safe_load(text)["id"]
        if resource_id in existing:
            continue
        if name in baseline:
            raise ConfigurationError(
                f"Setup destination {name} belongs to another resource. Move it or use an existing Agent before retrying.",
                code="configuration_mutation_conflict",
            )
        files[name] = text
    if not selection.is_addition and selection.connect_default and connection_model is not None:
        current_agent = existing.get(selection.default_agent)
        if current_agent is not None:
            name, resource = current_agent
            updated = {**resource, "model": connection_model}
            if selection.instructions.strip():
                updated["instructions"] = selection.instructions
            files[name] = yaml.safe_dump(updated, sort_keys=False, allow_unicode=True)
        else:
            for name, text in list(files.items()):
                resource = yaml.safe_load(text)
                if resource["id"] == (selection.new_agent_id or selection.default_agent):
                    resource["model"] = connection_model
                    files[name] = yaml.safe_dump(resource, sort_keys=False, allow_unicode=True)
    if seed_review:
        reviewer_model = next(
            (
                yaml.safe_load(text)["id"]
                for name, text in templates.items()
                if name in {"models/codex-review.yaml", "models/grok-shell-review.yaml"}
            ),
            selection.existing_model_id or connection_model,
        )
        root["security"] = {
            **security,
            "shell_review": {
                "enable": selection.shell_review and reviewer_model is not None,
                "risk_threshold": "extra_high",
                "on_flagged": "approval_required",
                "on_error": "allow",
                **({"model": reviewer_model} if reviewer_model is not None and selection.shell_review else {}),
            },
        }
        files[path.name] = yaml.safe_dump(root, sort_keys=False, allow_unicode=True)
    if not selection.is_addition:
        tools = root.setdefault("tools", {})
        if isinstance(tools, dict):
            for name, value in ToolsConfiguration().model_dump(mode="json").items():
                tools.setdefault(name, value)
        root.setdefault(
            "display",
            {"mode": "concise", "show_status": True, "max_tool_result_lines": 5, "max_tool_argument_chars": 8192},
        )
        if not isinstance(root, dict):
            raise ConfigurationError("The root configuration must be a mapping.", code="configuration_invalid")
        defaults = root.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ConfigurationError("Configuration defaults must be a mapping.", code="configuration_invalid")
        root["defaults"] = {
            **defaults,
            "agent": selection.default_agent,
            **({"project": selection.project} if selection.project is not None else {}),
            "environment_profile": selection.environment_profile,
        }
        if selection.include_default_subagents is not None:
            subagents = root.setdefault("subagents", {})
            if not isinstance(subagents, dict):
                raise ConfigurationError("Configuration subagents must be a mapping.", code="configuration_invalid")
            subagents["include"] = list(BUILTIN_SUBAGENT_NAMES) if selection.include_default_subagents else []
        files[path.name] = yaml.safe_dump(root, sort_keys=False, allow_unicode=True)
    candidate = dict(baseline)
    candidate.update({name: text.encode() for name, text in files.items()})
    staging = Path(await to_thread.run_sync(tempfile.mkdtemp))
    try:
        await to_thread.run_sync(_write_candidate, staging, candidate)
        loaded = await load_harness_ui_configuration(staging / path.name, content_plugin_root=content_plugin_root)
        validate_candidate(loaded)
    finally:
        await to_thread.run_sync(shutil.rmtree, staging, True)
    return SetupPreview(
        files=files,
        preserved_paths=tuple(sorted(name for name in baseline if name not in files)),
        project_paths=()
        if selection.is_addition or selection.project is None
        else tuple(root.path for root in loaded.projects[selection.project].roots),
    )


def _write_candidate(staging: Path, candidate: dict[str, bytes]) -> None:
    for name, content in candidate.items():
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


async def publish_setup(
    path: Path,
    selection: SetupSelection,
    *,
    validate_candidate: CandidateValidator,
    content_plugin_root: Path | None = None,
) -> SetupPublication:
    preview = await preview_setup(
        path, selection, validate_candidate=validate_candidate, content_plugin_root=content_plugin_root
    )
    published: list[str] = []
    try:
        for name, text in sorted(
            preview.files.items(), key=lambda item: (item[0] == path.name, not item[0].startswith("models/"), item[0])
        ):
            await to_thread.run_sync(_publish_content, path.parent / name, text.encode())
            published.append(name)
        return SetupPublication(completed=True, published_paths=tuple(published))
    except (HarnessUiError, OSError) as exc:
        return SetupPublication(
            completed=False,
            published_paths=tuple(published),
            error_code=exc.code if isinstance(exc, HarnessUiError) else "setup_publication_failed",
            error_message=str(exc)
            if isinstance(exc, HarnessUiError)
            else "Cannot publish configuration files. Check directory permissions and retry.",
        )
