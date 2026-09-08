"""Explicit preview and last-write-wins import of external subagent definitions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from anyio import to_thread
from pydantic import ValidationError

from a13n_harness_ui.errors import ConfigurationError

from .loader import load_harness_ui_configuration
from .models import CanonicalSubagent, LoadedHarnessUiConfiguration, SourceDocument
from .mutation import (
    CandidateValidator,
    ConfigurationMutationResult,
    ResourceMutationRequest,
    mutate_configuration_source,
)

_MAX_EXTERNAL_SOURCE_BYTES = 1024 * 1024
_MAX_EXTERNAL_FILES = 1024
_MAX_YAML_NODES = 100_000
_MAX_YAML_DEPTH = 64
_STABLE_READ_ATTEMPTS = 3


class ExternalSubagentProduct(StrEnum):
    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    CODEX = "codex"


class ExternalSubagentScope(StrEnum):
    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class ExternalSourceFact:
    """Exact external source bytes observed by a preview."""

    path: Path
    source_digest: str


@dataclass(frozen=True, slots=True)
class ExternalImportDiagnostic:
    severity: Literal["warning", "error"]
    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalSubagentImportCandidate:
    """One deterministic import candidate with captured source provenance."""

    configuration_path: Path
    product: ExternalSubagentProduct
    scope: ExternalSubagentScope
    name: str
    source_facts: tuple[ExternalSourceFact, ...]
    target_relative_path: str
    canonical_content: str | None
    canonical_content_digest: str | None
    diagnostics: tuple[ExternalImportDiagnostic, ...]
    status: Literal["ready", "unchanged", "invalid"]


@dataclass(frozen=True, slots=True)
class ExternalSubagentImportPreview:
    """Detached result of scanning exactly one product and one scope."""

    configuration_path: Path
    product: ExternalSubagentProduct
    scope: ExternalSubagentScope
    source_root: Path
    candidates: tuple[ExternalSubagentImportCandidate, ...]


@dataclass(frozen=True, slots=True)
class _ExternalDefinition:
    name_hint: str
    source_facts: tuple[ExternalSourceFact, ...]
    fields: dict[str, Any]
    body: str
    parse_error: str | None = None
    extra_unsupported: tuple[tuple[str, str], ...] = ()


async def preview_external_subagent_import(
    configuration_path: Path,
    *,
    product: ExternalSubagentProduct | str,
    scope: ExternalSubagentScope | str,
    inherit_runtime: bool = False,
    project_root: Path | None = None,
    user_home: Path | None = None,
    content_plugin_root: Path | None = None,
) -> ExternalSubagentImportPreview:
    """Scan one explicitly selected external product/scope and render a preview."""

    selected_product = _parse_product(product)
    selected_scope = _parse_scope(scope)
    selected_configuration = await to_thread.run_sync(lambda: configuration_path.expanduser().resolve(strict=False))
    loaded = await load_harness_ui_configuration(
        selected_configuration,
        content_plugin_root=content_plugin_root,
    )
    source_root, definitions = await to_thread.run_sync(
        _scan_external_definitions,
        selected_product,
        selected_scope,
        project_root,
        user_home,
    )
    candidates = tuple(
        _build_candidate(
            selected_configuration,
            selected_product,
            selected_scope,
            definition,
            loaded,
            inherit_runtime=inherit_runtime,
        )
        for definition in definitions
    )
    return ExternalSubagentImportPreview(
        configuration_path=selected_configuration,
        product=selected_product,
        scope=selected_scope,
        source_root=source_root,
        candidates=candidates,
    )


async def apply_external_subagent_import(
    configuration_path: Path,
    candidate: ExternalSubagentImportCandidate,
    *,
    validate_candidate: CandidateValidator | None = None,
    content_plugin_root: Path | None = None,
) -> ConfigurationMutationResult:
    """Validate and publish the captured preview with last-write-wins semantics."""

    selected = await to_thread.run_sync(lambda: configuration_path.expanduser().resolve(strict=False))
    if selected != candidate.configuration_path:
        raise _error(
            "external_subagent_import_invalid",
            "The preview belongs to a different Harness UI configuration.",
            selected,
        )
    if candidate.status == "invalid" or candidate.canonical_content is None:
        raise _error(
            "external_subagent_import_not_applicable",
            "The selected external subagent preview cannot be applied.",
            selected,
        )
    digest = hashlib.sha256(candidate.canonical_content.encode("utf-8")).hexdigest()
    if digest != candidate.canonical_content_digest:
        raise _error(
            "external_subagent_import_invalid",
            "The canonical preview content does not match its digest.",
            selected,
        )

    return await mutate_configuration_source(
        selected,
        candidate.target_relative_path,
        ResourceMutationRequest(content=candidate.canonical_content),
        validate_candidate=validate_candidate,
        content_plugin_root=content_plugin_root,
    )


# Short names for callers that already operate in the subagent-import namespace.
preview_subagent_import = preview_external_subagent_import
apply_subagent_import = apply_external_subagent_import


def _parse_product(value: ExternalSubagentProduct | str) -> ExternalSubagentProduct:
    try:
        return ExternalSubagentProduct(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "External subagent product must be claude-code, cursor, or codex.",
            code="external_subagent_import_invalid",
        ) from exc


def _parse_scope(value: ExternalSubagentScope | str) -> ExternalSubagentScope:
    try:
        return ExternalSubagentScope(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "External subagent scope must be user or project.",
            code="external_subagent_import_invalid",
        ) from exc


def _scan_external_definitions(
    product: ExternalSubagentProduct,
    scope: ExternalSubagentScope,
    project_root: Path | None,
    user_home: Path | None,
) -> tuple[Path, tuple[_ExternalDefinition, ...]]:
    if scope is ExternalSubagentScope.PROJECT:
        if project_root is None:
            raise ConfigurationError(
                "Project scope requires an explicit Project root.",
                code="external_subagent_import_invalid",
            )
        base = project_root.expanduser().resolve(strict=False)
    else:
        base = (user_home if user_home is not None else Path.home()).expanduser().resolve(strict=False)

    directory_name = {
        ExternalSubagentProduct.CLAUDE_CODE: ".claude",
        ExternalSubagentProduct.CURSOR: ".cursor",
        ExternalSubagentProduct.CODEX: ".codex",
    }[product]
    source_root = base / directory_name
    if product is ExternalSubagentProduct.CODEX:
        return source_root, _scan_codex(source_root)
    return source_root, _scan_markdown_product(source_root / "agents")


def _scan_markdown_product(directory: Path) -> tuple[_ExternalDefinition, ...]:
    paths = _immediate_files(directory, ".md")
    definitions: list[_ExternalDefinition] = []
    for path in paths:
        content, digest = _read_external_source(path)
        fact = ExternalSourceFact(path=path, source_digest=digest)
        try:
            fields, body = _parse_external_markdown(path, content)
            definitions.append(
                _ExternalDefinition(
                    name_hint=path.stem,
                    source_facts=(fact,),
                    fields=fields,
                    body=body,
                )
            )
        except ConfigurationError as exc:
            definitions.append(
                _ExternalDefinition(
                    name_hint=path.stem,
                    source_facts=(fact,),
                    fields={},
                    body="",
                    parse_error=str(exc),
                )
            )
    return tuple(definitions)


def _scan_codex(source_root: Path) -> tuple[_ExternalDefinition, ...]:
    config_path = source_root / "config.toml"
    config_content: bytes | None = None
    config_fact: ExternalSourceFact | None = None
    config: dict[str, Any] = {}
    if _path_exists(config_path):
        config_content, digest = _read_external_source(config_path)
        config_fact = ExternalSourceFact(path=config_path, source_digest=digest)
        try:
            parsed = tomllib.loads(config_content.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise _error(
                "external_subagent_source_invalid",
                "The selected Codex config.toml is malformed.",
                config_path,
            ) from exc
        if not isinstance(parsed, dict):
            raise _error(
                "external_subagent_source_invalid",
                "The selected Codex config.toml must be a table.",
                config_path,
            )
        config = parsed

    definitions: list[_ExternalDefinition] = []
    referenced: set[Path] = set()
    agents = config.get("agents", {})
    if agents is not None and not isinstance(agents, dict):
        raise _error(
            "external_subagent_source_invalid",
            "Codex agents must be a table.",
            config_path,
        )
    if isinstance(agents, dict):
        for name, registration in sorted(agents.items()):
            if not isinstance(registration, dict):
                continue  # e.g. max_threads
            facts = (config_fact,) if config_fact is not None else ()
            config_file = registration.get("config_file")
            child: dict[str, Any] = {}
            parse_error: str | None = None
            if not isinstance(config_file, str) or not config_file:
                parse_error = "Codex agent registration requires config_file."
            else:
                try:
                    child_path = _safe_codex_child_path(source_root, config_file)
                    content, digest = _read_external_source(child_path)
                    child = tomllib.loads(content.decode("utf-8"))
                    referenced.add(child_path)
                    facts = (*facts, ExternalSourceFact(path=child_path, source_digest=digest))
                except (ConfigurationError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                    parse_error = str(exc)
            merged = dict(child) if isinstance(child, dict) else {}
            merged["name"] = name
            if "description" in registration:
                merged["description"] = registration["description"]
            extras = tuple(
                (f"registration.{field}", "Codex registration behavior is not represented by canonical Markdown.")
                for field in sorted(set(registration) - {"description", "config_file"})
            )
            definitions.append(
                _ExternalDefinition(
                    name_hint=name,
                    source_facts=tuple(fact for fact in facts if fact is not None),
                    fields=merged,
                    body=_codex_instruction(merged),
                    parse_error=parse_error,
                    extra_unsupported=extras,
                )
            )

    # Also admit explicit standalone role files. Registered files win so one
    # source is never previewed twice.
    for child_path in _immediate_files(source_root / "agents", ".toml"):
        if child_path in referenced:
            continue
        content, digest = _read_external_source(child_path)
        fact = ExternalSourceFact(path=child_path, source_digest=digest)
        try:
            child = tomllib.loads(content.decode("utf-8"))
            fields = dict(child)
            fields.setdefault("name", child_path.stem)
            definitions.append(
                _ExternalDefinition(
                    name_hint=child_path.stem,
                    source_facts=(fact,),
                    fields=fields,
                    body=_codex_instruction(fields),
                )
            )
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            definitions.append(
                _ExternalDefinition(
                    name_hint=child_path.stem,
                    source_facts=(fact,),
                    fields={},
                    body="",
                    parse_error=str(exc),
                )
            )
    return tuple(sorted(definitions, key=lambda item: (item.name_hint, str(item.source_facts[-1].path))))


def _codex_instruction(fields: dict[str, Any]) -> str:
    value = fields.get("developer_instructions", fields.get("instructions", ""))
    return value if isinstance(value, str) else ""


def _safe_codex_child_path(source_root: Path, configured: str) -> Path:
    if "\x00" in configured:
        raise _error("external_subagent_source_invalid", "Codex config_file contains NUL.", source_root)
    candidate = Path(configured).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (source_root / candidate).resolve(strict=False)
    root = source_root.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise _error(
            "external_subagent_source_invalid",
            "Codex config_file escapes the selected product scope.",
            resolved,
        )
    return resolved


def _build_candidate(
    configuration_path: Path,
    product: ExternalSubagentProduct,
    scope: ExternalSubagentScope,
    definition: _ExternalDefinition,
    loaded: LoadedHarnessUiConfiguration,
    *,
    inherit_runtime: bool = False,
) -> ExternalSubagentImportCandidate:
    diagnostics: list[ExternalImportDiagnostic] = []
    if definition.parse_error is not None:
        diagnostics.append(
            ExternalImportDiagnostic(
                severity="error",
                code="source_invalid",
                message=definition.parse_error,
            )
        )

    fields = definition.fields
    raw_name = fields.get("name", definition.name_hint)
    name = raw_name if isinstance(raw_name, str) else definition.name_hint
    description = fields.get("description")
    instruction = fields.get("instruction")
    body = definition.body.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not isinstance(raw_name, str) or not raw_name:
        diagnostics.append(_field_error("name", "A non-empty string name is required."))
    if not isinstance(description, str) or not description.strip():
        diagnostics.append(_field_error("description", "A non-empty string description is required."))
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip()):
        diagnostics.append(_field_error("instruction", "instruction must be a non-empty string when present."))
        instruction = None

    _report_external_model(fields.get("model"), diagnostics)
    tools = _normalize_tools(fields.get("tools"), diagnostics)
    if inherit_runtime:
        tools = None
        diagnostics.append(
            ExternalImportDiagnostic(
                severity="warning",
                code="runtime_inherited",
                message="Explicit import choice: inherit the parent model and visible tools; external model/tool selections are omitted.",
            )
        )

    supported = {
        "name",
        "description",
        "instruction",
        "model",
        "tools",
        "developer_instructions",
        "instructions",
    }
    for field in sorted(set(fields) - supported):
        diagnostics.append(
            ExternalImportDiagnostic(
                severity="warning",
                code="unsupported_field",
                field=field,
                message=f"External field {field!r} is reported but not imported.",
            )
        )
    for field, message in definition.extra_unsupported:
        diagnostics.append(
            ExternalImportDiagnostic(
                severity="warning",
                code="unsupported_field",
                field=field,
                message=message,
            )
        )

    canonical: CanonicalSubagent | None = None
    if not any(item.severity == "error" for item in diagnostics):
        assert isinstance(description, str)
        try:
            canonical = CanonicalSubagent(
                id=f"subagent-{name}",
                name=name,
                description=description.strip(),
                instruction=instruction.strip() if isinstance(instruction, str) else None,
                tools=tools,
                body=body,
            )
        except ValidationError as exc:
            diagnostics.append(
                ExternalImportDiagnostic(
                    severity="error",
                    code="canonical_validation_failed",
                    message=f"The mapped canonical subagent is invalid ({exc.error_count()} validation errors).",
                )
            )

    target_relative_path = f"subagents/{name}.md"
    canonical_content = _render_canonical_markdown(canonical) if canonical is not None else None
    status: Literal["ready", "unchanged", "invalid"] = "invalid"

    if canonical is not None and canonical_content is not None:
        existing_source, existing = _existing_target(loaded, canonical.id, target_relative_path)
        if existing_source is None:
            status = "ready"
        else:
            if existing_source.relative_path.startswith("subagents/"):
                target_relative_path = existing_source.relative_path
            status = "unchanged" if existing == canonical else "ready"

    content_digest = (
        hashlib.sha256(canonical_content.encode("utf-8")).hexdigest() if canonical_content is not None else None
    )
    return ExternalSubagentImportCandidate(
        configuration_path=configuration_path,
        product=product,
        scope=scope,
        name=name,
        source_facts=definition.source_facts,
        target_relative_path=target_relative_path,
        canonical_content=canonical_content,
        canonical_content_digest=content_digest,
        diagnostics=tuple(diagnostics),
        status=status,
    )


def _report_external_model(
    raw: object,
    diagnostics: list[ExternalImportDiagnostic],
) -> None:
    if raw is None or raw == "inherit":
        return
    diagnostics.append(
        ExternalImportDiagnostic(
            severity="warning",
            code="unsupported_model",
            field="model",
            message="Markdown children inherit the parent model. Use an Agent resource reference for independent model settings.",
        )
    )
    return None


def _normalize_tools(
    raw: object,
    diagnostics: list[ExternalImportDiagnostic],
) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        values: tuple[object, ...] = tuple(item.strip() for item in raw.split(",") if item.strip())
    elif isinstance(raw, list):
        values = tuple(raw)
    else:
        diagnostics.append(_field_error("tools", "tools must be a comma-separated string or sequence."))
        return None
    if any(not isinstance(item, str) or not item.strip() for item in values):
        diagnostics.append(_field_error("tools", "Every tool name must be a non-empty string."))
        return None
    return tuple(dict.fromkeys(item.strip() for item in values if isinstance(item, str)))


def _render_canonical_markdown(value: CanonicalSubagent) -> str:
    lines = [
        "---",
        f"name: {_yaml_string(value.name)}",
        f"description: {_yaml_string(value.description)}",
    ]
    if value.instruction is not None:
        lines.append(f"instruction: {_yaml_string(value.instruction)}")
    if value.tools is not None:
        if value.tools:
            lines.append("tools:")
            lines.extend(f"  - {_yaml_string(tool)}" for tool in value.tools)
        else:
            lines.append("tools: []")
    lines.extend(("---", ""))
    if value.body:
        lines.extend((value.body, ""))
    return "\n".join(lines)


def _yaml_string(value: str) -> str:
    # JSON strings are a deterministic YAML subset and avoid emitter-specific
    # quoting, wrapping, and key ordering.
    return json.dumps(value, ensure_ascii=False)


def _existing_target(
    loaded: LoadedHarnessUiConfiguration,
    resource_id: str,
    planned_relative_path: str,
) -> tuple[SourceDocument | None, CanonicalSubagent | None]:
    existing = loaded.subagents.get(resource_id)
    if existing is not None:
        for source in loaded.sources:
            if source.resource_id == resource_id:
                return source, existing
        raise RuntimeError("loaded subagent omitted its source document")
    try:
        source = loaded.source(planned_relative_path)
    except KeyError:
        return None, None
    occupying = loaded.subagents.get(source.resource_id) if source.resource_id is not None else None
    return source, occupying


def _parse_external_markdown(path: Path, content: bytes) -> tuple[dict[str, Any], str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("external_subagent_source_invalid", "External Markdown must be UTF-8.", path) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0] != "---":
        raise _error(
            "external_subagent_source_invalid",
            "External Markdown requires YAML frontmatter.",
            path,
        )
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise _error(
            "external_subagent_source_invalid",
            "External Markdown frontmatter is not closed.",
            path,
        ) from exc
    raw = _parse_yaml_mapping(path, "\n".join(lines[1:closing]))
    return raw, "\n".join(lines[closing + 1 :]).strip()


def _parse_yaml_mapping(path: Path, text: str) -> dict[str, Any]:
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise ValueError("anchors and aliases are forbidden")
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                nodes += 1
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.events.ScalarEvent):
                nodes += 1
            if nodes > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
                raise ValueError("frontmatter exceeds structural limits")
        value = yaml.load(text, Loader=_UniqueSafeLoader)
    except (yaml.YAMLError, RecursionError, UnicodeError, ValueError) as exc:
        raise _error("external_subagent_source_invalid", "External frontmatter is malformed.", path) from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _error(
            "external_subagent_source_invalid",
            "External frontmatter must be a string-keyed mapping.",
            path,
        )
    return value


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate YAML key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _immediate_files(directory: Path, suffix: str) -> tuple[Path, ...]:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise _error(
            "external_subagent_source_unavailable", "The external source cannot be listed.", directory
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error(
            "external_subagent_source_invalid",
            "The external source directory must be a non-symlink directory.",
            directory,
        )
    paths: list[Path] = []
    try:
        with os.scandir(directory) as iterator:
            for index, entry in enumerate(iterator, start=1):
                if index > _MAX_EXTERNAL_FILES:
                    raise _error(
                        "external_subagent_source_limit",
                        "The external source directory contains too many entries.",
                        directory,
                    )
                if not entry.name.endswith(suffix):
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise _error(
                        "external_subagent_source_invalid",
                        "An external subagent source must be a non-symlink regular file.",
                        Path(entry.path),
                    )
                paths.append(Path(entry.path))
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error(
            "external_subagent_source_unavailable", "The external source cannot be listed.", directory
        ) from exc
    return tuple(sorted(paths, key=lambda path: path.name))


def _read_external_source(path: Path) -> tuple[bytes, str]:
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        descriptor = -1
        try:
            path_before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(path_before.st_mode) or path_before.st_size > _MAX_EXTERNAL_SOURCE_BYTES:
                raise _error(
                    "external_subagent_source_invalid",
                    "An external subagent source must be a bounded non-symlink regular file.",
                    path,
                )
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            content = os.read(descriptor, _MAX_EXTERNAL_SOURCE_BYTES + 1)
            after = os.fstat(descriptor)
            path_after = path.stat(follow_symlinks=False)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error(
                "external_subagent_source_unavailable",
                "An external subagent source cannot be read.",
                path,
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > _MAX_EXTERNAL_SOURCE_BYTES:
            raise _error(
                "external_subagent_source_limit",
                "An external subagent source exceeds its size limit.",
                path,
            )
        fingerprints = tuple(_fingerprint(item) for item in (path_before, before, after, path_after))
        if len(set(fingerprints)) == 1 and len(content) == before.st_size:
            return content, hashlib.sha256(content).hexdigest()
    raise _error(
        "external_subagent_source_unstable",
        "An external subagent source changed during bounded reads.",
        path,
    )


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _error("external_subagent_source_unavailable", "An external source cannot be inspected.", path) from exc
    return True


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    if os.name == "nt":
        return (0, 0, metadata.st_size, metadata.st_mtime_ns)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _field_error(field: str, message: str) -> ExternalImportDiagnostic:
    return ExternalImportDiagnostic(
        severity="error",
        code="invalid_field",
        field=field,
        message=message,
    )


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(message, code=code, details={"path": str(path)[-4096:]})


__all__ = [
    "ExternalImportDiagnostic",
    "ExternalSourceFact",
    "ExternalSubagentImportCandidate",
    "ExternalSubagentImportPreview",
    "ExternalSubagentProduct",
    "ExternalSubagentScope",
    "apply_external_subagent_import",
    "apply_subagent_import",
    "preview_external_subagent_import",
    "preview_subagent_import",
]
