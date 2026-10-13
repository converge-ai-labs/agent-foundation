"""Worker-local builtin Skill mounts and their restricted file tools."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness import AgentContext
from a13n_harness.capabilities.skills import FileSkillSource
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.models import (
    EnvironmentAction,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.environment.providers import BoundEnvironment, FileScopeSelection
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity, source_tool_id
from a13n_harness.toolsets.files import FileToolset
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_service.providers.registry import Registry, web_operations
from a13n_service.resources.agents.toolsets import catalog
from a13n_service.resources.providers.service import list_provider_types
from a13n_service.settings import Settings

ROOT = "/platform-skills/platform-operations"
INSTANCE_ROOT = "/platform-instance"
SOURCE = FileSkillSource("a13n-service-builtin-skills", (ROOT,))
_PACKAGE = Path(__file__).parent.parent / "builtin_skills"
_MOUNTS = frozenset({"builtin-skills", "builtin-instance"})
_READ_TEXT = EnvironmentPermissionSet(
    operations=frozenset(
        {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
        }
    )
)


@dataclass(frozen=True, slots=True)
class _LocalSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


def _mount(root: Path, path: str) -> EnvironmentMount:
    connector = DIRECT_LOCAL.execution_connector({"root": {"path": root}, "max_value_bytes": 4 * 1024 * 1024})
    return EnvironmentMount(_LocalSource(connector), permission_ceiling=_READ_TEXT, mount_path=path)


@dataclass(frozen=True, slots=True)
class BuiltinSkills:
    mounts: dict[str, EnvironmentMount]
    files: "BuiltinFilesCapability"


@asynccontextmanager
async def open_builtin_skills(registry: Registry, settings: Settings) -> AsyncIterator[BuiltinSkills]:
    """Prepare deployment-specific references once for the process, outside individual attempts."""
    instance = {
        "providers": {
            kind: list_provider_types(registry, kind).model_dump(mode="json")
            for kind in ("model", "environment", "connector", "web")
        },
        "toolsets": catalog(
            frozenset(operation for definition in registry.web.values() for operation in web_operations(definition))
        ).model_dump(mode="json"),
        "plugins": list(settings.plugins.keys),
    }
    references = {"catalog.json": json.dumps(instance, indent=2)}
    directory = await asyncio.to_thread(TemporaryDirectory, prefix="a13n-platform-")
    try:
        root = Path(directory.name)
        digest = await asyncio.to_thread(_write_references, root, references)
        yield BuiltinSkills(
            {"builtin-skills": _mount(_PACKAGE, "/platform-skills"), "builtin-instance": _mount(root, INSTANCE_ROOT)},
            BuiltinFilesCapability(digest),
        )
    finally:
        await asyncio.to_thread(directory.cleanup)


def _write_references(root: Path, references: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(_PACKAGE.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(_PACKAGE)).encode())
            digest.update(path.read_bytes())
    for name, content in sorted(references.items()):
        (root / name).write_text(content, encoding="utf-8")
        digest.update(name.encode())
        digest.update(content.encode())
    return digest.hexdigest()[:16]


@dataclass
class BuiltinFilesCapability(AbstractCapability[AgentContext]):
    """Only file-tool binding; SkillsCapability owns discovery, instructions and access events."""

    digest: str

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(_tools, per_run_step=False)

    def get_instructions(self) -> str:
        return (
            f"Builtin documents version: {self.digest}. If this changed, reread the relevant documents. "
            "Use platform_view, platform_ls, platform_glob and platform_grep with absolute paths "
            f"under /platform-skills or {INSTANCE_ROOT} to read builtin Skill documents and references. "
            "These local text tools grant no platform operation permissions."
        )


def _tools(ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
    environment = ctx.deps.environment
    return (
        FileToolset(environment.files, file_scopes=_BuiltinScopes(environment))
        .get_toolset(include_mutations=False, allowed_names=frozenset({"view", "ls", "glob", "grep"}))
        .prepared(_identities)
        .prefixed("platform")
    )


@dataclass(frozen=True, slots=True)
class _BuiltinScopes:
    environment: BoundEnvironment
    mount_ids: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mount_ids",
            frozenset(self.environment.select_files(".", alias=name).resolved_path.mount_id for name in _MOUNTS),
        )

    def select_files(self, path: str) -> FileScopeSelection:
        selection = self.environment.select_files(path)
        self._check(selection)
        return selection

    async def resolve_files(self, path: str) -> FileScopeSelection:
        self.select_files(path)
        selection = await self.environment.resolve_files(path)
        self._check(selection)
        return selection

    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]:
        self._check(selection)
        return self.environment.open_files(selection)

    def _check(self, selection: FileScopeSelection) -> None:
        if selection.resolved_path.mount_id not in self.mount_ids:
            raise EnvironmentError("Path is outside the builtin documents.", code="environment_denied")


def _identities(_ctx: RunContext[AgentContext], tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Local instructions have independent read permissions from the user's environment files."""
    return [
        replace(
            tool,
            metadata={
                **(tool.metadata or {}),
                TOOL_IDENTITY_KEY: ToolIdentity(source_tool_id("builtin_skills", tool.name)),
            },
        )
        for tool in tools
    ]
