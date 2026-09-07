"""Validated, non-clobbering first-use configuration publication.

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
from typing import Literal

import yaml
from anyio import to_thread
from pydantic import ConfigDict, Field

from a13n_harness_ui.errors import ConfigurationError, HarnessUiError
from a13n_harness_ui.subagents import BUILTIN_SUBAGENT_NAMES

from .loader import _parse_yaml_mapping, _scan_directory, load_harness_ui_configuration
from .models import ApiKeyAuthentication, LoadedHarnessUiConfiguration, ResourceId, StrictModel
from .mutation import (
    CandidateValidator,
    _fsync_directory,
    _publish_content,
    _source_bytes_with_digest,
    _source_digest_or_none,
)

_EMPTY_ROOT = b'schema_version: "2"\n'
_DIRECTORIES = ("models", "extensions", "mcp", "agents", "projects", "subagents")


class SetupApiKeyModel(StrictModel):
    route: str = Field(min_length=1, max_length=512)
    authentication: ApiKeyAuthentication


class SetupSelection(StrictModel):
    providers: tuple[Literal["codex", "grok"], ...] = ()
    api_key_model: SetupApiKeyModel | None = None
    instructions: str = Field(default="", max_length=1024 * 1024)
    connect_default: bool = False
    default_agent: ResourceId = "agent-default"
    project: ResourceId = "project-local"
    project_path: str = Field(min_length=1, max_length=4096)
    environment_profile: Literal["environment-native", "environment-sandbox"]
    shell_review: bool = True
    include_default_subagents: bool | None = None
    codex_model: Literal["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"] = "gpt-5.6-sol"

    codex_thinking: Literal["low", "medium", "high", "xhigh"] = "high"
    codex_context_window: int = Field(default=350000, ge=16000, le=872000)
    proactive_context_management_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)


class SetupPreview(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=False)

    generation: str
    files: dict[str, str]
    preserved_paths: tuple[str, ...]
    project_paths: tuple[str, ...]
    candidate_digest: str


class SetupPublication(StrictModel):
    completed: bool
    published_paths: tuple[str, ...]
    error_code: str | None = None
    error_message: str | None = None


def _capture(path: Path) -> dict[str, bytes]:
    for parent in (path.parent, *(path.parent / name for name in _DIRECTORIES)):
        for recovery in parent.glob(".a13n-harness-ui-setup-recovery-*"):
            if any(recovery.iterdir()):
                raise ConfigurationError(
                    f"An interrupted setup retained configuration at {recovery}. Review and restore or move it before retrying.",
                    code="setup_recovery_required",
                )
    entries: dict[str, bytes] = {}
    digest = _source_digest_or_none(path)
    if digest is not None:
        entries[path.name] = _source_bytes_with_digest(path, digest)
    for directory in _DIRECTORIES:
        for relative, source, _fingerprint in _scan_directory(
            path.parent / directory, markdown=directory == "subagents"
        ):
            digest = _source_digest_or_none(source)
            if digest is None:
                raise ConfigurationError(
                    "Configuration changed during setup discovery.", code="configuration_mutation_conflict"
                )
            entries[relative] = _source_bytes_with_digest(source, digest)
            if len(entries) > 2048 or sum(map(len, entries.values())) > 16 * 1024 * 1024:
                raise ConfigurationError("Configuration exceeds setup limits.", code="configuration_source_limit")
    return entries


def _generation(entries: dict[str, bytes]) -> str:
    return hashlib.sha256(
        json.dumps({key: hashlib.sha256(value).hexdigest() for key, value in sorted(entries.items())}).encode()
    ).hexdigest()


async def setup_generation(path: Path) -> str:
    return _generation(await to_thread.run_sync(_capture, path))


def _templates(selection: SetupSelection) -> dict[str, str]:
    resources: dict[str, dict[str, object]] = {}
    for provider in dict.fromkeys(selection.providers):
        codex = provider == "codex"
        model = selection.codex_model if codex else "grok-4.6"
        resources[f"models/{provider}.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": f"model-{provider}",
            "name": f"{provider.title()} coding",
            "route": f"{'openai-codex' if codex else 'grok'}:{model}",
            "authentication": {"kind": f"{provider}_subscription"},
            "settings": {
                "thinking": selection.codex_thinking,
                "openai_reasoning_summary": "detailed",
                "openai_store": False,
            }
            if codex
            else {},
            **(
                {
                    "model_characteristics": {
                        "context_window": selection.codex_context_window,
                        "proactive_context_management_threshold": selection.proactive_context_management_threshold,
                        "compact_threshold": selection.compact_threshold,
                    }
                }
                if codex
                else {}
            ),
        }
        capabilities: list[dict[str, object]] = [
            {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
            {"capability": "skills", "configuration": {}},
            {"capability": "compaction", "configuration": {}},
            {"capability": "handoff", "configuration": {}},
            {"capability": "runtime_context", "configuration": {}},
        ]
        if selection.shell_review:
            reviewer = "model-codex-review" if "codex" in selection.providers else "model-grok"
            capabilities.append(
                {
                    "capability": "ShellReviewCapability",
                    "configuration": {
                        "model": reviewer,
                        "risk_threshold": "high",
                        "on_flagged": "approval_required",
                        "on_error": "approval_required",
                    },
                }
            )
        resources[f"agents/{provider}.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": f"agent-{provider}",
            "name": f"{provider.title()} coding",
            "model": f"model-{provider}",
            "capabilities": capabilities,
        }
    if selection.shell_review and "codex" in selection.providers:
        resources["models/codex-review.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": "model-codex-review",
            "name": "Codex lightweight shell review",
            "route": "openai-codex:gpt-5.6-luna",
            "authentication": {"kind": "codex_subscription"},
            "settings": {"thinking": "low"},
        }
    if selection.api_key_model is not None:
        resources["models/api-key.yaml"] = {
            "schema_version": "1",
            "kind": "model",
            "id": "model-api-key",
            "name": "API key model",
            "route": selection.api_key_model.route,
            "authentication": selection.api_key_model.authentication.model_dump(mode="json"),
        }
        resources["agents/api-key.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-api-key",
            "name": "API key Agent",
            "model": "model-api-key",
            "capabilities": [
                {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
                {"capability": "skills", "configuration": {}},
                {"capability": "compaction", "configuration": {}},
                {"capability": "handoff", "configuration": {}},
                {"capability": "runtime_context", "configuration": {}},
            ],
        }
    if not selection.providers and selection.api_key_model is None:
        resources["agents/default.yaml"] = {
            "schema_version": "1",
            "kind": "agent",
            "id": "agent-default",
            "name": "Default Agent",
            "capabilities": [
                {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}},
                {"capability": "skills", "configuration": {}},
                {"capability": "compaction", "configuration": {}},
                {"capability": "handoff", "configuration": {}},
                {"capability": "runtime_context", "configuration": {}},
            ],
        }
    if selection.instructions.strip():
        for resource in resources.values():
            if resource["id"] == selection.default_agent:
                resource["instructions"] = selection.instructions
    resources[f"projects/{selection.project}.yaml"] = {
        "schema_version": "1",
        "kind": "project",
        "id": selection.project,
        "name": "Local project",
        "roots": [{"path": selection.project_path}],
    }
    return {name: yaml.safe_dump(value, sort_keys=False) for name, value in resources.items()}


def _same_connection(actual: dict[str, object], desired: dict[str, object]) -> bool:
    def authentication(resource: dict[str, object]) -> object:
        value = resource.get("authentication")
        return {key: item for key, item in value.items() if item is not None} if isinstance(value, dict) else value

    return actual.get("route") == desired.get("route") and authentication(actual) == authentication(desired)


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
    templates = _templates(selection)
    connection_model: str | None = None
    if selection.api_key_model is not None:
        connection_model = "model-api-key"
        old_model = existing.get(connection_model)
        if old_model is not None:
            desired = yaml.safe_load(templates["models/api-key.yaml"])
            if not _same_connection(old_model[1], desired):
                suffix = hashlib.sha256(json.dumps(desired, sort_keys=True).encode()).hexdigest()[:12]
                connection_model = f"model-api-key-{suffix}"
                desired["id"] = connection_model
                del templates["models/api-key.yaml"]
                templates[f"models/api-key-{suffix}.yaml"] = yaml.safe_dump(desired, sort_keys=False)
                starter = yaml.safe_load(templates["agents/api-key.yaml"])
                starter["model"] = connection_model
                templates["agents/api-key.yaml"] = yaml.safe_dump(starter, sort_keys=False)
    elif selection.providers:
        provider = selection.default_agent.removeprefix("agent-")
        connection_model = f"model-{provider if provider in selection.providers else selection.providers[0]}"
    if connection_model is not None and connection_model in existing:
        desired = next(
            yaml.safe_load(text) for text in templates.values() if yaml.safe_load(text)["id"] == connection_model
        )
        if not _same_connection(existing[connection_model][1], desired):
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
    if selection.connect_default and connection_model is not None:
        current_agent = existing.get(selection.default_agent)
        if current_agent is not None:
            name, resource = current_agent
            updated = {**resource, "model": connection_model}
            if selection.instructions.strip():
                updated["instructions"] = selection.instructions
            files[name] = yaml.safe_dump(updated, sort_keys=False)
        else:
            for name, text in list(files.items()):
                resource = yaml.safe_load(text)
                if resource["id"] == selection.default_agent:
                    resource["model"] = connection_model
                    files[name] = yaml.safe_dump(resource, sort_keys=False)
    root = _parse_yaml_mapping(path, baseline.get(path.name, _EMPTY_ROOT), code="settings_invalid")
    root.setdefault(
        "display", {"mode": "concise", "show_status": True, "max_tool_result_lines": 5, "max_tool_argument_chars": 8192}
    )
    if not isinstance(root, dict):
        raise ConfigurationError("The root configuration must be a mapping.", code="configuration_invalid")
    defaults = root.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigurationError("Configuration defaults must be a mapping.", code="configuration_invalid")
    root["defaults"] = {
        **defaults,
        "agent": selection.default_agent,
        "project": selection.project,
        "environment_profile": selection.environment_profile,
    }
    if selection.include_default_subagents is not None:
        root["subagents"] = {"include": list(BUILTIN_SUBAGENT_NAMES) if selection.include_default_subagents else []}
    files[path.name] = yaml.safe_dump(root, sort_keys=False)
    candidate = dict(baseline)
    candidate.update({name: text.encode() for name, text in files.items()})
    staging = Path(await to_thread.run_sync(tempfile.mkdtemp))
    try:
        await to_thread.run_sync(_write_candidate, staging, candidate)
        loaded = await load_harness_ui_configuration(staging / path.name, content_plugin_root=content_plugin_root)
        validate_candidate(loaded)
    finally:
        await to_thread.run_sync(shutil.rmtree, staging, True)
    if _generation(await to_thread.run_sync(_capture, path)) != _generation(baseline):
        raise ConfigurationError(
            "Configuration changed during setup preview. Retry setup.", code="configuration_mutation_conflict"
        )
    return SetupPreview(
        generation=_generation(baseline),
        files=files,
        preserved_paths=tuple(sorted(name for name in baseline if name not in files)),
        project_paths=tuple(root.path for root in loaded.projects[selection.project].roots),
        candidate_digest=loaded.source_digest,
    )


def _write_candidate(staging: Path, candidate: dict[str, bytes]) -> None:
    for name, content in candidate.items():
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _publish_root_defaults(path: Path, content: bytes, expected: str) -> None:
    """Preserve a racing atomic save rather than replacing it unconditionally.

    Detach the observed root into a recovery directory on the same filesystem,
    verify what was actually detached, then publish only if the root is absent.
    A crash can leave the named recovery directory for manual restoration.
    """
    recovery = Path(tempfile.mkdtemp(prefix=".a13n-harness-ui-setup-recovery-", dir=path.parent))
    retained = recovery / path.name
    completed = False
    try:
        os.rename(path, retained)
        _fsync_directory(recovery)
        _fsync_directory(path.parent)
        if _source_digest_or_none(retained) != expected:
            raise ConfigurationError(
                "The root defaults changed during setup publication.", code="configuration_mutation_conflict"
            )
        _publish_content(path, content, None)
        completed = True
    except BaseException as exc:
        if retained.exists():
            try:
                os.link(retained, path, follow_symlinks=False)
                _fsync_directory(path.parent)
                retained.unlink()
                _fsync_directory(recovery)
            except FileExistsError:
                raise ConfigurationError(
                    f"A concurrent root configuration was preserved. The displaced version remains at {retained}. Review both files before retrying setup.",
                    code="configuration_mutation_conflict",
                ) from exc
        raise
    finally:
        if completed and retained.exists():
            retained.unlink()
            _fsync_directory(recovery)
        if not any(recovery.iterdir()):
            recovery.rmdir()
            _fsync_directory(path.parent)


async def publish_setup(
    path: Path,
    selection: SetupSelection,
    *,
    expected_generation: str,
    validate_candidate: CandidateValidator,
    content_plugin_root: Path | None = None,
) -> SetupPublication:
    preview = await preview_setup(
        path, selection, validate_candidate=validate_candidate, content_plugin_root=content_plugin_root
    )
    if preview.generation != expected_generation:
        raise ConfigurationError(
            "Setup preview is stale. Review the current files and retry.", code="configuration_mutation_conflict"
        )
    baseline = await to_thread.run_sync(_capture, path)
    published: list[str] = []
    try:
        if _generation(baseline) != preview.generation:
            raise ConfigurationError(
                "Configuration changed before setup publication.", code="configuration_mutation_conflict"
            )
        for name, text in sorted(
            preview.files.items(), key=lambda item: (item[0] == path.name, not item[0].startswith("models/"), item[0])
        ):
            if await to_thread.run_sync(_capture, path) != baseline:
                raise ConfigurationError(
                    "Configuration changed during setup publication. Published files were retained.",
                    code="configuration_mutation_conflict",
                )
            content = text.encode()
            if baseline.get(name) == content:
                continue
            expected = hashlib.sha256(baseline[name]).hexdigest() if name in baseline else None
            if expected is not None:
                await to_thread.run_sync(_publish_root_defaults, path.parent / name, content, expected)
            else:
                await to_thread.run_sync(_publish_content, path.parent / name, content, expected)
            published.append(name)
            baseline[name] = content
        if await to_thread.run_sync(_capture, path) != baseline:
            raise ConfigurationError(
                "Configuration changed after setup publication.", code="configuration_mutation_conflict"
            )
        loaded: LoadedHarnessUiConfiguration = await load_harness_ui_configuration(
            path, content_plugin_root=content_plugin_root
        )
        validate_candidate(loaded)
        if loaded.source_digest != preview.candidate_digest:
            raise ConfigurationError(
                "Configuration changed during final setup verification. Published files were retained.",
                code="configuration_mutation_conflict",
            )
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
