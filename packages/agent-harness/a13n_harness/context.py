"""Per-run trusted bindings and Pydantic AI dependency context."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import JsonValue
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage

from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.observation import HarnessObservationContext
from a13n_harness.state import AgentContextState, HarnessState

if TYPE_CHECKING:
    from a13n_harness.capabilities.steering import SteeringBridge
    from a13n_harness.environment.models import EnvironmentPath
    from a13n_harness.environment.providers import BoundEnvironment as Environment
    from a13n_harness.environment.providers import EnvironmentRuntime
    from a13n_harness.events import HarnessEventEmitter
    from a13n_harness.execution import AgentDefinition, ExecutableAgent, SubagentDefinition
    from a13n_harness.model_context import (
        ModelContextMiddleware,
        ModelContextProjection,
        ModelContextProjectionRequest,
    )
    from a13n_harness.models import RunModelResolver
    from a13n_harness.plugins import BoundPluginContext
    from a13n_harness.pricing import AbstractModelCostCapability
    from a13n_harness.spec import HarnessModelCharacteristics
    from a13n_harness.tools.deferred import DeferredToolResume
    from a13n_harness.usage import ProviderUsage, ProviderUsageRecord, RunUsageLedger, UsageRecord


@dataclass(frozen=True, slots=True, init=False)
class BuiltSubagent:
    """One authored child edge and its recursively built executable."""

    _declaration: SubagentDefinition = field(repr=False)
    definition: AgentDefinition[Any]
    executable: ExecutableAgent[Any]

    def __init__(
        self,
        *,
        declaration: SubagentDefinition,
        definition: AgentDefinition[Any],
        executable: ExecutableAgent[Any],
    ) -> None:
        object.__setattr__(self, "_declaration", _copy_subagent_declaration(declaration))
        object.__setattr__(self, "definition", definition)
        object.__setattr__(self, "executable", executable)

    @property
    def declaration(self) -> SubagentDefinition:
        """Return a detached edge value so mutable native limits cannot widen the build."""
        return _copy_subagent_declaration(self._declaration)


def _copy_subagent_declaration(declaration: SubagentDefinition) -> SubagentDefinition:
    from a13n_harness.execution import SubagentDefinition

    return SubagentDefinition(
        name=declaration.name,
        description=declaration.description,
        agent=declaration.agent,
        context=declaration.context,
        identity=declaration.identity,
        usage_limits=declaration.usage_limits,
    )


@dataclass(frozen=True, slots=True)
class SubagentCollection(Mapping[str, BuiltSubagent]):
    """Immutable immediate-child collection in authored order."""

    _items: Mapping[str, BuiltSubagent] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_items", MappingProxyType(dict(self._items)))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __getitem__(self, name: str) -> BuiltSubagent:
        return self._items[name]

    def require(self, name: str) -> BuiltSubagent:
        """Return a named immediate child or raise a clear lookup error."""
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"Unknown subagent: {name!r}") from None


EMPTY_SUBAGENTS = SubagentCollection()


@dataclass(frozen=True, slots=True)
class _CapabilityProvenance:
    """Reserved Capability IDs accepted from each trusted composition source."""

    definition_ids: frozenset[str] = frozenset()
    run_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RunBindings:
    """Fresh trusted authority and optional advanced integrations supplied by the caller."""

    instance: AgentInstanceContext
    environment: EnvironmentRuntime | None = None
    model_resolver: RunModelResolver | None = None
    toolset_instructions: bool | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    model_context: ModelContextMiddleware | None = None
    observation: HarnessObservationContext | None = None
    _inherited_model_cost: AbstractModelCostCapability | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.toolset_instructions is not None and not isinstance(self.toolset_instructions, bool):
            raise TypeError("toolset_instructions must be a boolean or None")
        if self.observation is not None and not isinstance(self.observation, HarnessObservationContext):
            raise TypeError("observation must be a HarnessObservationContext or None")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", MappingProxyType(deepcopy(dict(self.metadata))))

    @classmethod
    def embedded(
        cls,
        *,
        identity: AgentIdentityRef | None = None,
        environment: EnvironmentRuntime | None = None,
        model_resolver: RunModelResolver | None = None,
        toolset_instructions: bool | None = None,
        model_context: ModelContextMiddleware | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        metadata: Mapping[str, JsonValue] | None = None,
        observation: HarnessObservationContext | None = None,
    ) -> RunBindings:
        """Create fresh trusted bindings for one embedded run."""
        instance_id = str(uuid4())
        return cls(
            instance=AgentInstanceContext(
                identity=identity or AgentIdentityRef(issuer="local", subject="embedded"),
                agent_instance_id=instance_id,
            ),
            environment=environment,
            model_resolver=model_resolver,
            toolset_instructions=toolset_instructions,
            model_context=model_context,
            capabilities=tuple(capabilities),
            metadata=metadata or {},
            observation=observation,
        )


@dataclass(frozen=True, slots=True)
class SkillPath:
    """One resolved run-scoped skill directory and its provenance."""

    name: str
    source_id: str
    directory: EnvironmentPath

    def __post_init__(self) -> None:
        from a13n_harness.environment.models import EnvironmentPath

        _validate_runtime_metadata_id(self.name, "skill name")
        _validate_runtime_metadata_id(self.source_id, "skill source_id")
        if not isinstance(self.directory, EnvironmentPath):
            raise TypeError("skill directory must be an EnvironmentPath")


class RunSkillPaths:
    """Owner-bound run-scoped publication of resolved skill directories."""

    def __init__(self) -> None:
        self._by_owner: dict[str, tuple[SkillPath, ...]] = {}

    def publish(self, owner_id: str, paths: Sequence[SkillPath]) -> None:
        owner = _validate_runtime_metadata_id(owner_id, "skill path owner_id")
        values = tuple(paths)
        if not all(isinstance(item, SkillPath) for item in values):
            raise TypeError("skill paths must contain only SkillPath values")
        existing = self._by_owner.get(owner)
        if existing is None:
            self._by_owner[owner] = values
        elif existing != values:
            raise RuntimeError(f"Skill path owner {owner!r} already published a different value")

    @property
    def values(self) -> tuple[SkillPath, ...]:
        """Return an immutable snapshot in owner publication order."""
        return tuple(path for paths in self._by_owner.values() for path in paths)


@dataclass(frozen=True, slots=True)
class ToolMetadataKey[T]:
    """Typed process-local key interpreted by its owning Toolset."""

    name: str
    value_type: type[T]

    def __post_init__(self) -> None:
        _validate_runtime_metadata_id(self.name, "tool metadata key")
        if not isinstance(self.value_type, type):
            raise TypeError("tool metadata value_type must be a runtime type")


class ToolRuntimeMetadata:
    """Passive owner-bound metadata consumed by Toolsets during one logical run."""

    def __init__(self) -> None:
        self._key_types: dict[str, type[object]] = {}
        self._by_key: dict[ToolMetadataKey[Any], dict[str, object]] = {}

    def publish[T](self, key: ToolMetadataKey[T], owner_id: str, value: T) -> None:
        if not isinstance(key, ToolMetadataKey):
            raise TypeError("tool metadata key must be a ToolMetadataKey")
        owner = _validate_runtime_metadata_id(owner_id, "tool metadata owner_id")
        if not isinstance(value, key.value_type):
            raise TypeError(
                f"Tool metadata {key.name!r} requires {key.value_type.__name__}, not {type(value).__name__}"
            )
        existing_type = self._key_types.setdefault(key.name, key.value_type)
        if existing_type is not key.value_type:
            raise TypeError(f"Tool metadata key {key.name!r} is already bound to another value type")
        owners = self._by_key.setdefault(key, {})
        if owner not in owners:
            owners[owner] = value
        elif owners[owner] != value:
            raise RuntimeError(f"Tool metadata owner {owner!r} already published a different value for {key.name!r}")

    def values[T](self, key: ToolMetadataKey[T]) -> tuple[T, ...]:
        """Return an immutable snapshot in owner publication order."""
        if not isinstance(key, ToolMetadataKey):
            raise TypeError("tool metadata key must be a ToolMetadataKey")
        existing_type = self._key_types.get(key.name)
        if existing_type is not None and existing_type is not key.value_type:
            raise TypeError(f"Tool metadata key {key.name!r} is already bound to another value type")
        return tuple(cast(T, value) for value in self._by_key.get(key, {}).values())


def _validate_runtime_metadata_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError(f"{field_name} must be a non-blank bounded string without NUL")
    return value


@dataclass(frozen=True, slots=True)
class AgentContext:
    """The one dependency object shared across a Pydantic AI run."""

    run_id: str
    thread_id: str
    instance: AgentInstanceContext
    state: AgentContextState
    environment: Environment
    model_resolver: RunModelResolver | None
    model_characteristics: HarnessModelCharacteristics | None
    _model_inference: RunModelResolver = field(repr=False, compare=False)
    toolset_instructions: bool
    _toolset_instructions_override: bool | None = field(repr=False, compare=False)
    plugins: BoundPluginContext
    subagents: SubagentCollection
    events: HarnessEventEmitter
    usage_attribution: RunUsageLedger = field(repr=False)
    deferred_resume: DeferredToolResume | None
    metadata: Mapping[str, JsonValue]
    _steering: SteeringBridge = field(repr=False, compare=False)
    model_context: ModelContextMiddleware | None = None
    _inherited_model_cost: AbstractModelCostCapability | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _started_at_monotonic: float = field(default_factory=monotonic, repr=False, compare=False)
    _skill_selection_names: frozenset[str] | None = field(default=None, repr=False, compare=False)
    skill_paths: RunSkillPaths = field(default_factory=RunSkillPaths, compare=False)
    tool_metadata: ToolRuntimeMetadata = field(default_factory=ToolRuntimeMetadata, compare=False)
    _capability_provenance: _CapabilityProvenance = field(default_factory=_CapabilityProvenance, repr=False)
    _managed_tool_ids: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _run_capability_instances: dict[str, AbstractCapability[AgentContext]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _run_cleanup_callbacks: dict[str, Callable[[], Awaitable[None]]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _tool_result_spill_store: _ToolResultSpillStore | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _record_managed_tool_surface(self, tool_ids: Mapping[str, str]) -> None:
        object.__setattr__(self, "_managed_tool_ids", MappingProxyType(dict(tool_ids)))

    def _run_capability(self, capability_id: str) -> AbstractCapability[AgentContext] | None:
        """Return a logical-run Capability replacement cached across inner Agent attempts."""
        return self._run_capability_instances.get(capability_id)

    def _record_run_capability(
        self,
        capability_id: str,
        capability: AbstractCapability[AgentContext],
    ) -> None:
        """Retain one fresh Capability replacement for this logical Harness run."""
        existing = self._run_capability_instances.setdefault(capability_id, capability)
        if existing is not capability:
            raise RuntimeError(f"Run Capability {capability_id!r} is already bound")

    def _register_run_cleanup(
        self,
        owner_id: str,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        """Register one owner-bound live collaborator for logical-run teardown."""
        if owner_id in self._run_cleanup_callbacks:
            raise RuntimeError(f"Run cleanup owner {owner_id!r} is already registered")
        self._run_cleanup_callbacks[owner_id] = cleanup

    async def _close_run_cleanups(self) -> None:
        """Close owner-bound collaborators in reverse registration order."""
        callbacks = tuple(reversed(tuple(self._run_cleanup_callbacks.values())))
        self._run_cleanup_callbacks.clear()
        first_error: BaseException | None = None
        for cleanup in callbacks:
            try:
                await cleanup()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str | None:
        """Write one bounded managed result through the current Environment when possible."""
        store = self._tool_result_spill_store
        if store is None:
            store = _ToolResultSpillStore(self)
            object.__setattr__(self, "_tool_result_spill_store", store)
            self._register_run_cleanup("a13n.tool-result-spills", store.close)
        return await store.write(data, suffix=suffix)

    @property
    def identity(self) -> AgentIdentityRef:
        """Return the identity carried by the trusted instance binding."""
        return self.instance.identity

    @property
    def elapsed_seconds(self) -> float:
        """Return monotonic elapsed time for this logical Harness run."""
        return max(0.0, monotonic() - self._started_at_monotonic)

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        """Return a detached snapshot of mixed run-local usage attribution."""
        return self.usage_attribution.records

    async def record_provider_usage(
        self,
        usage: ProviderUsage,
        *,
        source: str,
        tool_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> ProviderUsageRecord:
        """Record one stable non-model usage receipt for the next reporting boundary."""
        return await self.usage_attribution._record_provider(
            usage,
            source=source,
            tool_id=tool_id,
            tool_call_id=tool_call_id,
        )

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        """Project bounded default Agent and Environment context without editing messages."""
        from a13n_harness._json import dump_json_bytes
        from a13n_harness.model_context import (
            ModelContextBlock,
            ModelContextPlacement,
            ModelContextProjection,
            ModelContextRequestKind,
        )

        environment = await self.environment.project_model_context(request)
        payload: dict[str, JsonValue] = {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
        }
        if request.kind is ModelContextRequestKind.TOOL_RESULTS:
            payload.pop("thread_id", None)
        content = (
            '<agent-context source="a13n-harness">\n'
            f"{dump_json_bytes(payload, sort_keys=True).decode('utf-8')}\n"
            "</agent-context>"
        )
        return ModelContextProjection(
            blocks=(
                *environment.blocks,
                ModelContextBlock(
                    source_id="a13n.agent-context",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=content,
                ),
            )
        )

    async def export_state(self, message_history: Sequence[ModelMessage]) -> HarnessState:
        """Export a detached continuation envelope without persistence side effects."""
        return HarnessState(
            schema_version="1",
            thread_id=self.thread_id,
            message_history=tuple(message_history),
            agent_context_state=await self.state.snapshot(),
            environment_state=await self.environment.export_state(),
        )


class _ToolResultSpillStore:
    """One best-effort run-private spill directory over the logical file facade."""

    def __init__(self, context: AgentContext) -> None:
        run_digest = hashlib.sha256(context.run_id.encode("utf-8")).hexdigest()[:12]
        self._files = context.environment.files
        self._directory = f"/workspace/.a13n/tmp/tool-results/run-{run_digest}"
        self._next_sequence = 1
        self._ready = False
        self._closed = False
        self._lock = asyncio.Lock()

    async def write(self, data: bytes, *, suffix: str) -> str | None:
        if suffix not in {".json", ".txt"}:
            raise ValueError("tool result spill suffix is invalid")
        async with self._lock:
            if self._closed:
                return None
            try:
                if not self._ready:
                    await self._files.mkdir(self._directory, parents=True, exist_ok=True)
                    self._ready = True
                path = f"{self._directory}/tool-result-{self._next_sequence}{suffix}"
                self._next_sequence += 1
                await self._files.write_bytes_stream(path, _byte_chunks(data), mode="create")
            except Exception:
                return None
            return path

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self._ready:
                return
            try:
                await self._files.remove(self._directory, recursive=True)
            except Exception:
                # Spill cleanup is finite best effort. It must not turn a completed
                # tool side effect or run into a retry-shaped failure.
                return


async def _byte_chunks(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), 64 * 1024):
        yield data[offset : offset + 64 * 1024]
