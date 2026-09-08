"""Stable bounded loading for the Harness UI multi-file configuration tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import yaml
from anyio import to_thread
from pydantic import TypeAdapter, ValidationError

from a13n_harness_ui.content_plugins import ContentPluginStore, InstalledContentPlugin
from a13n_harness_ui.errors import ConfigurationError

from .models import (
    AgentResource,
    CanonicalSubagent,
    EnvironmentProfileResource,
    EnvironmentRunExtensionResource,
    ExtensionResource,
    HarnessPluginResource,
    HarnessUiDocument,
    LoadedHarnessUiConfiguration,
    McpFileValueSource,
    McpServerResource,
    ModelResource,
    ProjectResource,
    SourceDocument,
    canonical_digest,
)

_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_FILES = 4096
_MAX_DIRECTORY_ENTRIES = 4096
_MAX_YAML_NODES = 100_000
_MAX_YAML_DEPTH = 64
_STABLE_READ_ATTEMPTS = 3
# Version normalized snapshots independently of user-owned source byte digests.
_NORMALIZATION_VERSION = "2"
_YAML_DIRECTORIES = ("models", "extensions", "mcp", "agents", "projects")
_RESOURCE_TYPES: dict[str, type[Any]] = {
    "models": ModelResource,
    "mcp": McpServerResource,
    "agents": AgentResource,
    "projects": ProjectResource,
}
_EXTENSION_ADAPTER = TypeAdapter(ExtensionResource)


type ConfigurationTreeFingerprint = tuple[tuple[str, tuple[int, int, int, int]], ...]


async def configuration_tree_fingerprint(
    path: Path,
    *,
    content_plugin_root: Path | None = None,
) -> ConfigurationTreeFingerprint:
    """Return a bounded metadata-only fingerprint for change observation."""

    selected = path.expanduser().resolve(strict=False)
    if selected.suffix != ".yaml":
        raise _error("settings_path_invalid", "Harness UI configuration must use lower-case .yaml.", selected)
    scanned = await to_thread.run_sync(_scan_tree, selected)
    values = [(relative_path, fingerprint) for relative_path, _source_path, fingerprint in scanned]
    if content_plugin_root is not None:
        registrations = await ContentPluginStore(content_plugin_root).fingerprint()
        values.extend((f"content-plugins/{name}", fingerprint) for name, fingerprint in registrations)
    return tuple(values)


async def load_harness_ui_configuration(
    path: Path,
    *,
    content_plugin_root: Path | None = None,
) -> LoadedHarnessUiConfiguration:
    """Read and validate one coherent complete source-tree and Content Plugin generation."""

    selected = path.expanduser().resolve(strict=False)
    if selected.suffix != ".yaml":
        raise _error("settings_path_invalid", "Harness UI configuration must use lower-case .yaml.", selected)

    plugin_store = None if content_plugin_root is None else ContentPluginStore(content_plugin_root)
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        plugin_before = () if plugin_store is None else await plugin_store.fingerprint()
        content_plugins = () if plugin_store is None else await plugin_store.list()
        before = await to_thread.run_sync(_scan_tree, selected)
        captured: list[tuple[str, bytes, tuple[int, int, int, int]]] = []
        total_bytes = 0
        changed = False
        for relative_path, source_path, expected in before:
            content, fingerprint = await to_thread.run_sync(
                _read_bounded_stable,
                source_path,
                _MAX_SOURCE_BYTES,
            )
            if fingerprint != expected:
                changed = True
                break
            total_bytes += len(content)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise _error(
                    "configuration_source_limit",
                    "The Harness UI configuration tree exceeds its total size limit.",
                    selected,
                )
            captured.append((relative_path, content, fingerprint))
        if changed:
            continue
        after = await to_thread.run_sync(_scan_tree, selected)
        if before != after:
            continue
        parsed = _parse_complete_tree(selected, captured, content_plugins)
        if plugin_store is not None:
            parsed = parsed.model_copy(
                update={
                    "source_digest": (
                        canonical_digest((parsed.source_digest, tuple(plugin_store.diagnostics)))
                        if plugin_store.diagnostics
                        else parsed.source_digest
                    ),
                    "content_plugin_diagnostics": (*plugin_store.diagnostics, *parsed.content_plugin_diagnostics),
                }
            )
        plugin_after = () if plugin_store is None else await plugin_store.fingerprint()
        if plugin_before != plugin_after:
            continue
        return parsed

    raise _error(
        "settings_source_unstable",
        "Harness UI configuration sources changed during bounded reads.",
        selected,
    )


def empty_harness_ui_configuration(
    content_plugins: tuple[InstalledContentPlugin, ...] = (),
) -> LoadedHarnessUiConfiguration:
    """Return an empty onboarding generation when the default root is absent."""

    content = 'schema_version: "2"\n'
    root_digest = hashlib.sha256(content.encode()).hexdigest()
    source = SourceDocument(
        relative_path="a13n-harness-ui.yaml",
        source_digest=root_digest,
        resource_kind="root",
        content=content,
    )
    return LoadedHarnessUiConfiguration(
        document=HarnessUiDocument(),
        root_digest=root_digest,
        source_digest=canonical_digest(
            (
                _NORMALIZATION_VERSION,
                (source.relative_path, source.source_digest),
                *tuple(
                    (f"content-plugins/{item.plugin_id}/registration.json", canonical_digest(item))
                    for item in content_plugins
                ),
            )
        ),
        sources=(source,),
        content_plugins=content_plugins,
    )


def _scan_tree(path: Path) -> tuple[tuple[str, Path, tuple[int, int, int, int]], ...]:
    root = (path.name, path, _regular_file_fingerprint(path))
    entries: list[tuple[str, Path, tuple[int, int, int, int]]] = [root]
    guidance = path.parent / "AGENTS.md"
    if os.path.lexists(guidance):
        entries.append(("AGENTS.md", guidance, _regular_file_fingerprint(guidance)))
    for directory_name in (*_YAML_DIRECTORIES, "subagents"):
        directory = path.parent / directory_name
        entries.extend(_scan_directory(directory, markdown=directory_name == "subagents"))
        if len(entries) > _MAX_FILES:
            raise _error(
                "configuration_source_limit",
                "The Harness UI configuration tree contains too many sources.",
                path,
            )
    return tuple(entries)


def _scan_directory(
    directory: Path,
    *,
    markdown: bool,
) -> tuple[tuple[str, Path, tuple[int, int, int, int]], ...]:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise _error("settings_unavailable", "A configuration directory cannot be read.", directory) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error(
            "configuration_source_invalid",
            "A configuration source directory must be a non-symlink directory.",
            directory,
        )
    selected: list[tuple[str, Path, tuple[int, int, int, int]]] = []
    try:
        with os.scandir(directory) as iterator:
            for index, entry in enumerate(iterator, start=1):
                if index > _MAX_DIRECTORY_ENTRIES:
                    raise _error(
                        "configuration_source_limit",
                        "A configuration directory exceeds its entry limit.",
                        directory,
                    )
                wanted = (
                    entry.name != "README.md" and entry.name.endswith(".md")
                    if markdown
                    else entry.name.endswith(".yaml") or (directory.name == "mcp" and entry.name.endswith(".json"))
                )
                if not wanted:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise _error(
                        "configuration_source_invalid",
                        "A selected configuration source must be a non-symlink regular file.",
                        Path(entry.path),
                    )
                metadata = entry.stat(follow_symlinks=False)
                source_path = Path(entry.path)
                relative = f"{directory.name}/{entry.name}"
                selected.append((relative, source_path, _fingerprint(metadata)))
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("settings_unavailable", "A configuration directory cannot be listed.", directory) from exc
    return tuple(sorted(selected, key=lambda item: item[0]))


def _parse_complete_tree(
    root_path: Path,
    captured: list[tuple[str, bytes, tuple[int, int, int, int]]],
    content_plugins: tuple[InstalledContentPlugin, ...],
) -> LoadedHarnessUiConfiguration:
    if not captured or captured[0][0] != root_path.name:
        raise RuntimeError("configuration capture omitted its root")
    document = _validate_model(
        HarnessUiDocument,
        _parse_yaml_mapping(root_path, captured[0][1], code="settings_invalid"),
        root_path,
        code="settings_invalid",
    )
    models: dict[str, ModelResource] = {}
    plugins: dict[str, HarnessPluginResource] = {}
    profiles: dict[str, EnvironmentProfileResource] = {}
    run_extensions: dict[str, EnvironmentRunExtensionResource] = {}
    mcp: dict[str, McpServerResource] = {}
    agents: dict[str, AgentResource] = {}
    subagents: dict[str, CanonicalSubagent] = {}
    projects: dict[str, ProjectResource] = {}
    sources: list[SourceDocument] = []

    for relative_path, content, _fingerprint_value in captured:
        source_path = root_path.parent / relative_path
        digest = hashlib.sha256(content).hexdigest()
        text = _decode_source(source_path, content, code="configuration_source_invalid")
        if relative_path == root_path.name:
            sources.append(
                SourceDocument(
                    relative_path=relative_path,
                    source_digest=digest,
                    resource_kind="root",
                    content=text,
                )
            )
            continue
        if relative_path == "AGENTS.md":
            sources.append(
                SourceDocument(
                    relative_path=relative_path, source_digest=digest, resource_kind="instructions", content=text
                )
            )
            continue
        directory = relative_path.split("/", 1)[0]
        if directory == "mcp":
            resources = _parse_mcp_resources(source_path, content)
            for resource in resources:
                _insert_unique(mcp, resource.id, resource, source_path)
            sources.append(
                SourceDocument(
                    relative_path=relative_path,
                    source_digest=digest,
                    resource_kind="mcp_server",
                    resource_id=resources[0].id if len(resources) == 1 else None,
                    resource_ids=tuple(item.id for item in resources) if len(resources) != 1 else (),
                    # Source files remain authoritative; do not duplicate literal values in snapshots or show.
                    content="",
                )
            )
            continue
        if directory == "subagents":
            resource = parse_canonical_markdown(source_path, content)
            _insert_unique(subagents, resource.id, resource, source_path)
            kind = "subagent"
            resource_id = resource.id
        else:
            raw = _parse_yaml_mapping(source_path, content, code="configuration_resource_invalid")
            if directory == "extensions":
                resource = _validate_extension(raw, source_path)
                if isinstance(resource, HarnessPluginResource):
                    _insert_unique(plugins, resource.id, resource, source_path)
                elif isinstance(resource, EnvironmentProfileResource):
                    _insert_unique(profiles, resource.id, resource, source_path)
                else:
                    _insert_unique(run_extensions, resource.id, resource, source_path)
            else:
                model_type = _RESOURCE_TYPES[directory]
                resource = _validate_model(
                    model_type,
                    raw,
                    source_path,
                    code="configuration_resource_invalid",
                )
                expected_kind = {
                    "models": "model",
                    "mcp": "mcp_server",
                    "agents": "agent",
                    "projects": "project",
                }[directory]
                if resource.kind != expected_kind:
                    raise _error(
                        "configuration_resource_invalid",
                        f"Resource kind does not belong in {directory}/.",
                        source_path,
                    )
                target = {
                    "models": models,
                    "mcp": mcp,
                    "agents": agents,
                    "projects": projects,
                }[directory]
                _insert_unique(target, resource.id, resource, source_path)
            kind = resource.kind
            resource_id = resource.id
        sources.append(
            SourceDocument(
                relative_path=relative_path,
                source_digest=digest,
                resource_kind=kind,
                resource_id=resource_id,
                content=text,
            )
        )

    plugin_diagnostics = _merge_content_plugin_subagents(
        content_plugins,
        local_subagents=subagents,
        sources=sources,
    )

    # Package resources share the canonical Markdown path, but never a mutable user source.
    from a13n_harness_ui.subagents import builtin_subagent_sources

    for name, content in builtin_subagent_sources():
        relative_path = f"built-in-subagents/{name}.md"
        resource = parse_canonical_markdown(Path(relative_path), content)
        _insert_unique(subagents, resource.id, resource, Path(relative_path))
        sources.append(
            SourceDocument(
                relative_path=relative_path,
                source_digest=hashlib.sha256(content).hexdigest(),
                resource_kind="subagent",
                resource_id=resource.id,
                content=content.decode("utf-8"),
            )
        )

    try:
        return LoadedHarnessUiConfiguration(
            document=document,
            root_digest=sources[0].source_digest,
            source_digest=canonical_digest(
                (
                    _NORMALIZATION_VERSION,
                    tuple((item.relative_path, item.source_digest) for item in sources),
                    tuple(plugin_diagnostics),
                )
            ),
            sources=tuple(sources),
            content_plugins=content_plugins,
            content_plugin_diagnostics=tuple(plugin_diagnostics),
            models=models,
            harness_plugins=plugins,
            environment_profiles=profiles,
            environment_run_extensions=run_extensions,
            mcp_servers=mcp,
            agents=agents,
            subagents=subagents,
            projects=projects,
        )
    except ValidationError as exc:
        raise _validation_error(
            "configuration_invalid",
            "Harness UI configuration references are invalid.",
            root_path,
            exc,
        ) from exc


def _parse_json_mapping(path: Path, content: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        raw = json.loads(
            _decode_source(path, content, code="configuration_resource_invalid"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
        if not isinstance(raw, dict):
            raise ValueError("expected JSON object")
        pending: list[tuple[Any, int]] = [(raw, 0)]
        nodes = 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if nodes > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
                raise _error("configuration_source_limit", "MCP JSON exceeds structural limits.", path)
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        return raw
    except (ValueError, RecursionError):
        raise _error(
            "configuration_resource_invalid", "MCP JSON must be a valid object with unique keys.", path
        ) from None


def _parse_mcp_resources(path: Path, content: bytes) -> tuple[McpServerResource, ...]:
    # Do not chain raw parser/Pydantic errors: their inputs may contain literal tokens.
    try:
        return _normalize_mcp_resources(path, content)
    except ConfigurationError as exc:
        error = ConfigurationError(str(exc), code=exc.code, details=exc.details)
    raise error


def _normalize_mcp_resources(path: Path, content: bytes) -> tuple[McpServerResource, ...]:
    raw = (
        _parse_json_mapping(path, content)
        if path.suffix == ".json"
        else _parse_yaml_mapping(path, content, code="configuration_resource_invalid")
    )
    digest = hashlib.sha256(content).hexdigest()
    if "mcpServers" not in raw:
        definitions = [(raw, ("transport",))]
    else:
        if set(raw) != {"mcpServers"} or not isinstance(raw["mcpServers"], dict):
            raise _error("configuration_resource_invalid", "Expected an mcpServers object without extra fields.", path)
        definitions = []
        for name, server in raw["mcpServers"].items():
            if not isinstance(name, str) or not name or not isinstance(server, dict):
                raise _error("configuration_resource_invalid", "MCP server entries must be named objects.", path)
            transport = dict(server)
            transport_type = transport.pop("type", None)
            expected_type = "stdio" if "command" in transport else "http"
            if transport_type == "streamable-http":
                transport_type = "http"
            if transport_type not in (None, expected_type):
                raise _error("configuration_resource_invalid", "MCP type must match stdio or http transport.", path)
            for external, canonical in (("args", "arguments"), ("env", "environment")):
                if canonical in transport:
                    raise _error("configuration_resource_invalid", "mcpServers entries use args and env fields.", path)
                if external in transport:
                    transport[canonical] = transport.pop(external)
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not slug:
                slug = hashlib.sha256(name.encode()).hexdigest()[:12]
            resource_id = slug if slug.startswith("mcp-") else f"mcp-{slug}"
            definitions.append(
                (
                    {
                        "schema_version": "1",
                        "kind": "mcp_server",
                        "id": resource_id,
                        "name": name,
                        "transport": transport,
                    },
                    ("mcpServers", name),
                )
            )
    resources = []
    for definition, prefix in definitions:
        transport = definition.get("transport")
        if isinstance(transport, dict):
            for field in ("environment", "headers"):
                values = transport.get(field, {})
                if not isinstance(values, dict):
                    continue  # The typed transport validator reports invalid mappings.
                for key, value in values.items():
                    if isinstance(value, str):
                        if "\x00" in value:
                            raise _error("configuration_resource_invalid", "MCP values must be NUL-free.", path)
                        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
                        source_field = "env" if prefix[0] == "mcpServers" and field == "environment" else field
                        values[key] = (
                            {"env": match[1]}
                            if match
                            else {
                                "file": f"mcp/{path.name}",
                                "source_digest": digest,
                                "path": [*prefix, source_field, key],
                            }
                        )
                    elif not isinstance(value, dict) or set(value) != {"env"}:
                        raise _error(
                            "configuration_resource_invalid", "MCP values must be strings or env references.", path
                        )
        resources.append(_validate_model(McpServerResource, definition, path, code="configuration_resource_invalid"))
    return tuple(resources)


def read_mcp_file_values(
    configuration_root: Path, sources: tuple[McpFileValueSource, ...]
) -> dict[tuple[str, ...], str]:
    """Read one exact source file for Run-local literal/template resolution."""
    source = sources[0]
    path = configuration_root / source.file
    # The directory follows the same non-symlink boundary as discovery.
    if path.parent.is_symlink():
        raise _error("configuration_source_invalid", "The MCP source directory cannot be a symlink.", path)
    content, _ = _read_bounded_stable(path, _MAX_SOURCE_BYTES)
    if hashlib.sha256(content).hexdigest() != source.source_digest:
        raise _error(
            "mcp_source_changed", "The captured MCP source changed; start a new Run with current configuration.", path
        )
    raw = (
        _parse_json_mapping(path, content)
        if path.suffix == ".json"
        else _parse_yaml_mapping(path, content, code="configuration_resource_invalid")
    )
    result = {}
    for item in sources:
        value: Any = raw
        for key in item.path:
            value = value[key]
        if not isinstance(value, str):
            raise _error("configuration_resource_invalid", "The captured MCP value is not a string.", path)
        result[item.path] = value
    return result


def _merge_content_plugin_subagents(
    content_plugins: tuple[InstalledContentPlugin, ...],
    *,
    local_subagents: dict[str, CanonicalSubagent],
    sources: list[SourceDocument],
) -> list[str]:
    diagnostics: list[str] = []
    plugin_subagents: dict[str, tuple[CanonicalSubagent, Path, bytes, str]] = {}
    total_bytes = 0
    total_files = 0
    for plugin in sorted(content_plugins, key=lambda item: item.plugin_id):
        plugin_content = plugin.model_dump_json(exclude={"subagent_paths"}, indent=2)
        plugin_digest = hashlib.sha256(plugin_content.encode()).hexdigest()
        sources.append(
            SourceDocument(
                relative_path=f"content-plugins/{plugin.plugin_id}/plugin.json",
                source_digest=plugin_digest,
                resource_kind="content_plugin",
                content=plugin_content,
            )
        )
        for source_value in plugin.subagent_paths:
            source_path = Path(source_value)
            try:
                content, _fingerprint_value = _read_bounded_stable(source_path, _MAX_SOURCE_BYTES)
            except ConfigurationError as exc:
                diagnostics.append(f"{source_path}: {exc}")
                continue
            total_files += 1
            total_bytes += len(content)
            if total_files > _MAX_FILES or total_bytes > _MAX_TOTAL_BYTES:
                diagnostics.append(f"{source_path}: Plugin subagent source limit exceeded.")
                continue
            try:
                resource = parse_canonical_markdown(source_path, content)
            except ConfigurationError as exc:
                diagnostics.append(f"{source_path}: {exc}")
                sources.append(
                    SourceDocument(
                        relative_path=f"content-plugins/{plugin.plugin_id}/subagents/{source_path.name}",
                        source_digest=hashlib.sha256(content).hexdigest(),
                        resource_kind="content_plugin_invalid",
                        content=content.decode("utf-8", errors="replace"),
                    )
                )
                continue
            if resource.id in plugin_subagents:
                diagnostics.append(f"{source_path}: Overrides plugin subagent {resource.id}.")
            relative_path = f"content-plugins/{plugin.plugin_id}/subagents/{source_path.name}"
            plugin_subagents[resource.id] = (resource, source_path, content, relative_path)

    for resource_id, (resource, _path, content, relative_path) in sorted(plugin_subagents.items()):
        overridden = resource_id in local_subagents
        if not overridden:
            local_subagents[resource_id] = resource
        sources.append(
            SourceDocument(
                relative_path=relative_path,
                source_digest=hashlib.sha256(content).hexdigest(),
                resource_kind="content_plugin_subagent" if overridden else "subagent",
                resource_id=None if overridden else resource_id,
                content=_decode_source(Path(relative_path), content, code="configuration_markdown_invalid"),
            )
        )

    return diagnostics


def _validate_extension(raw: dict[str, Any], path: Path) -> ExtensionResource:
    try:
        serialized = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return _EXTENSION_ADAPTER.validate_json(serialized, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            raise _validation_error(
                "configuration_resource_invalid",
                "An extension resource is invalid.",
                path,
                exc,
            ) from exc
        raise _error("configuration_resource_invalid", "An extension resource is invalid.", path) from exc


def _validate_model(model_type: type[Any], raw: dict[str, Any], path: Path, *, code: str) -> Any:
    try:
        serialized = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return model_type.model_validate_json(serialized, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            raise _validation_error(code, "A configuration document is invalid.", path, exc) from exc
        raise _error(code, "A configuration document is invalid.", path) from exc


def parse_canonical_markdown(path: Path, content: bytes) -> CanonicalSubagent:
    text = _decode_source(path, content, code="configuration_markdown_invalid")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0] != "---":
        raise _error("configuration_markdown_invalid", "Canonical subagent Markdown requires YAML frontmatter.", path)
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise _error("configuration_markdown_invalid", "Canonical subagent frontmatter is not closed.", path) from exc
    raw = _parse_yaml_mapping(
        path,
        "\n".join(lines[1:closing]).encode(),
        code="configuration_markdown_invalid",
    )
    if "body" in raw:
        raise _error("configuration_markdown_invalid", "Markdown body is not a frontmatter field.", path)
    if "model" in raw:
        raise _error(
            "configuration_markdown_invalid",
            "Markdown children inherit the parent model. Remove model; reference an Agent for independent settings.",
            path,
        )
    name = raw.get("name")
    if "id" not in raw and isinstance(name, str):
        raw["id"] = f"subagent-{name}"
    raw["body"] = "\n".join(lines[closing + 1 :]).strip()
    return _validate_model(
        CanonicalSubagent,
        raw,
        path,
        code="configuration_markdown_invalid",
    )


def _insert_unique(target: dict[str, Any], resource_id: str, value: Any, path: Path) -> None:
    if resource_id in target:
        raise _error(
            "configuration_duplicate_resource",
            f"Duplicate resource ID: {resource_id}",
            path,
        )
    target[resource_id] = value


def _read_bounded_stable(path: Path, max_bytes: int) -> tuple[bytes, tuple[int, int, int, int]]:
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        descriptor = -1
        try:
            path_before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(path_before.st_mode):
                raise _error(
                    "configuration_source_invalid",
                    "A configuration source must be a non-symlink regular file.",
                    path,
                )
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
            content = os.read(descriptor, max_bytes + 1)
            after = os.fstat(descriptor)
            path_after = path.stat(follow_symlinks=False)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("settings_unavailable", "A selected configuration source cannot be read.", path) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        fingerprint = _fingerprint(before)
        if len(content) > max_bytes:
            raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
        if (
            fingerprint == _fingerprint(after)
            and fingerprint == _fingerprint(path_before)
            and fingerprint == _fingerprint(path_after)
            and len(content) == before.st_size
        ):
            return content, fingerprint
    raise _error("settings_source_unstable", "A configuration source changed during bounded reads.", path)


def _regular_file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise _error("settings_unavailable", "A selected configuration source cannot be read.", path) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _error(
            "configuration_source_invalid",
            "A configuration source must be a non-symlink regular file.",
            path,
        )
    return _fingerprint(metadata)


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    if os.name == "nt":
        return (0, 0, metadata.st_size, metadata.st_mtime_ns)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _decode_source(path: Path, content: bytes, *, code: str) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(code, "A configuration source must be valid UTF-8.", path) from exc
    if "\x00" in text:
        raise _error(code, "A configuration source contains NUL.", path)
    return text


def _parse_yaml_mapping(path: Path, content: bytes, *, code: str) -> dict[str, Any]:
    text = _decode_source(path, content, code=code)
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise _error(code, "Configuration YAML forbids anchors and aliases.", path)
            tag = getattr(event, "tag", None)
            if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
                raise _error(code, "Configuration YAML forbids custom tags.", path)
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                nodes += 1
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.events.ScalarEvent):
                nodes += 1
            if nodes > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
                raise _error("configuration_source_limit", "Configuration YAML exceeds structural limits.", path)
        value = yaml.load(text, Loader=_UniqueSafeLoader)
    except ConfigurationError:
        raise
    except (yaml.YAMLError, RecursionError, UnicodeError, ValueError) as exc:
        raise _error(code, "Configuration YAML is malformed.", path) from exc
    if value is None:
        value = {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _error(code, "Configuration YAML must be a string-keyed mapping.", path)
    return value


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<":
            raise yaml.constructor.ConstructorError(None, None, "YAML merge keys are forbidden", key_node.start_mark)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate YAML key: {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _validation_error(code: str, message: str, path: Path, exc: ValidationError) -> ConfigurationError:
    return ConfigurationError(
        message,
        code=code,
        details={"path": str(path)[-4096:], "validation_error_count": exc.error_count()},
    )


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(message, code=code, details={"path": str(path)[-4096:]})


__all__ = ["empty_harness_ui_configuration", "load_harness_ui_configuration", "parse_canonical_markdown"]
