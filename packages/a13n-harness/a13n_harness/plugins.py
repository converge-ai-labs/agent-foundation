"""Code-first Harness plugins, deterministic ordering, and run middleware."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic_ai.capabilities import AbstractCapability

from a13n_harness.context import AgentContext
from a13n_harness.errors import PluginError
from a13n_harness.events import HarnessEvent
from a13n_harness.input import SemanticRunInput
from a13n_harness.result import HarnessRunResult
from a13n_harness.state import HarnessState

type PluginPosition = Literal["outermost", "innermost"]
type PluginRunItem[OutputT] = HarnessEvent | HarnessRunResult[OutputT]
type PluginRunItemValidator[OutputT] = Callable[[PluginRunItem[OutputT]], PluginRunItem[OutputT]]
type StateExporter = Callable[[], Awaitable[HarnessState]]


@runtime_checkable
class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PluginOrdering:
    """Stable-ID middleware ordering constraints."""

    position: PluginPosition | None = None
    wraps: tuple[str, ...] = ()
    wrapped_by: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.position not in {None, "outermost", "innermost"}:
            raise ValueError("PluginOrdering.position must be 'outermost', 'innermost', or None")


@dataclass(frozen=True, slots=True)
class PluginRunExchange:
    """The semantic input and run context visible to one middleware layer."""

    input: SemanticRunInput
    context: AgentContext
    _state_exporter: StateExporter = field(repr=False, compare=False)

    def with_input(self, value: SemanticRunInput) -> PluginRunExchange:
        """Create a transformed exchange without mutating trusted context."""
        return replace(self, input=value)

    async def export_current_state(self) -> HarnessState:
        """Export the Harness-owned latest complete message boundary."""
        return await self._state_exporter()


class PluginRunResponse[OutputT](AsyncIterator[PluginRunItem[OutputT]]):
    """Single-consumer, explicitly closeable plugin response."""

    def __init__(self, iterator: AsyncIterator[PluginRunItem[OutputT]]) -> None:
        self._iterator = iterator
        self._closed = False
        self._iterated = False
        self._item_validator: PluginRunItemValidator[OutputT] | None = None

    def __aiter__(self) -> PluginRunResponse[OutputT]:
        if self._iterated:
            raise PluginError(
                "Plugin responses have exactly one consumer.",
                code="plugin_response_reused",
            )
        self._iterated = True
        return self

    async def __anext__(self) -> PluginRunItem[OutputT]:
        if self._closed:
            raise StopAsyncIteration
        item = await self._iterator.__anext__()
        if self._item_validator is not None:
            item = self._item_validator(item)
        return item

    def _bind_item_validator(self, validator: PluginRunItemValidator[OutputT]) -> None:
        """Bind Harness result validation to this response boundary."""
        self._item_validator = validator

    async def aclose(self) -> None:
        """Close the underlying iterator once."""
        if self._closed:
            return
        try:
            if isinstance(self._iterator, _AsyncClosable):
                await self._iterator.aclose()
        finally:
            self._item_validator = None
            self._closed = True


class PluginRunNext[OutputT]:
    """A one-shot continuation to the next inner middleware layer."""

    def __init__(self, factory: Callable[[PluginRunExchange], PluginRunResponse[OutputT]]) -> None:
        self._factory = factory
        self._called = False

    def __call__(self, exchange: PluginRunExchange) -> PluginRunResponse[OutputT]:
        if self._called:
            raise PluginError(
                "A plugin may call its run continuation at most once.",
                code="plugin_next_reused",
            )
        self._called = True
        return self._factory(exchange)


class AbstractHarnessPlugin(ABC):
    """Trusted code-first extension around the whole Harness run boundary."""

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Return the stable ID used for ordering and run-bound lookup."""

    def get_ordering(self) -> PluginOrdering:
        """Return ordering constraints; definition order is the default."""
        return PluginOrdering()

    def for_agent(self) -> AbstractHarnessPlugin:
        """Return the reentrant instance owned by one executable."""
        return self

    async def for_run(self, context: AgentContext) -> AbstractHarnessPlugin:
        """Return the instance isolated to one run."""
        del context
        return self

    def get_capabilities(self) -> Sequence[AbstractCapability[AgentContext]]:
        """Contribute ordinary Pydantic AI Capabilities at Agent construction."""
        return ()

    def wrap_run(
        self,
        exchange: PluginRunExchange,
        call_next: PluginRunNext[Any],
    ) -> PluginRunResponse[Any]:
        """Wrap the canonical stream path."""
        return call_next(exchange)


class BoundPluginContext:
    """Atomic read-only view of the complete run-bound plugin graph."""

    def __init__(self) -> None:
        self._ordered: tuple[AbstractHarnessPlugin, ...] | None = None
        self._by_id: dict[str, AbstractHarnessPlugin] | None = None

    @property
    def ordered(self) -> tuple[AbstractHarnessPlugin, ...]:
        """Return plugins in outer-to-inner order after binding completes."""
        self._ensure_bound()
        assert self._ordered is not None
        return self._ordered

    def get(self, plugin_id: str) -> AbstractHarnessPlugin | None:
        """Return one run-bound plugin by stable ID."""
        self._ensure_bound()
        assert self._by_id is not None
        return self._by_id.get(plugin_id)

    def require[PluginT: AbstractHarnessPlugin](
        self,
        plugin_id: str,
        expected_type: type[PluginT],
    ) -> PluginT:
        """Require both stable ID and expected concrete API type."""
        plugin = self.get(plugin_id)
        if plugin is None:
            raise PluginError(
                "Required run-bound plugin is absent.",
                code="plugin_required",
                details={"plugin_id": plugin_id},
            )
        if not isinstance(plugin, expected_type):
            raise PluginError(
                "Run-bound plugin has an unexpected type.",
                code="plugin_type_mismatch",
                details={"plugin_id": plugin_id},
            )
        return plugin

    def _freeze(self, plugins: Sequence[AbstractHarnessPlugin]) -> None:
        if self._ordered is not None:
            raise PluginError("Plugin context is already bound.", code="plugins_already_bound")
        ordered = tuple(plugins)
        self._ordered = ordered
        self._by_id = {plugin.plugin_id: plugin for plugin in ordered}

    def _ensure_bound(self) -> None:
        if self._ordered is None:
            raise PluginError(
                "Run-bound plugins are not available while binding is in progress.",
                code="plugins_not_bound",
            )


def bind_agent_plugins(
    plugins: Sequence[AbstractHarnessPlugin],
) -> tuple[tuple[AbstractHarnessPlugin, ...], dict[str, tuple[AbstractCapability[AgentContext], ...]]]:
    """Order and Agent-bind once, retaining exact contribution ownership."""
    ordered = _order_plugins(plugins)
    bound: list[AbstractHarnessPlugin] = []
    contributions: dict[str, tuple[AbstractCapability[AgentContext], ...]] = {}
    for plugin in ordered:
        replacement = plugin.for_agent()
        _validate_replacement(plugin, replacement, phase="agent")
        bound.append(replacement)
        capabilities: list[AbstractCapability[AgentContext]] = []
        for capability in replacement.get_capabilities():
            if not isinstance(capability, AbstractCapability):
                raise PluginError(
                    "Plugin Capability contributions must be AbstractCapability instances.",
                    code="plugin_capability_invalid",
                    details={"plugin_id": replacement.plugin_id},
                )
            capabilities.append(capability)
        contributions[replacement.plugin_id] = tuple(capabilities)
    return tuple(bound), contributions


async def bind_run_plugins(
    plugins: Sequence[AbstractHarnessPlugin],
    context: AgentContext,
) -> tuple[AbstractHarnessPlugin, ...]:
    """Sequentially bind fresh run plugin instances and freeze the shared index."""
    bound: list[AbstractHarnessPlugin] = []
    for plugin in plugins:
        replacement = await plugin.for_run(context)
        _validate_replacement(plugin, replacement, phase="run")
        bound.append(replacement)
    context.plugins._freeze(bound)
    return tuple(bound)


def _validate_replacement(
    original: AbstractHarnessPlugin,
    replacement: AbstractHarnessPlugin,
    *,
    phase: str,
) -> None:
    if not isinstance(replacement, AbstractHarnessPlugin):
        raise PluginError(
            f"Plugin {phase} binding returned an invalid value.",
            code="plugin_binding_invalid",
            details={"plugin_id": original.plugin_id, "phase": phase},
        )
    if (
        type(replacement) is not type(original)
        or replacement.plugin_id != original.plugin_id
        or replacement.get_ordering() != original.get_ordering()
    ):
        raise PluginError(
            f"Plugin {phase} binding changed its identity or ordering.",
            code="plugin_binding_identity_changed",
            details={"plugin_id": original.plugin_id, "phase": phase},
        )


def _order_plugins(plugins: Sequence[AbstractHarnessPlugin]) -> tuple[AbstractHarnessPlugin, ...]:
    values = tuple(plugins)
    by_id: dict[str, AbstractHarnessPlugin] = {}
    orderings: dict[str, PluginOrdering] = {}
    for plugin in values:
        if not isinstance(plugin, AbstractHarnessPlugin):
            raise PluginError("Configured plugins must inherit AbstractHarnessPlugin.", code="plugin_invalid")
        plugin_id = plugin.plugin_id
        if not plugin_id.strip():
            raise PluginError("Plugin IDs must not be blank.", code="plugin_id_invalid")
        if plugin_id in by_id:
            raise PluginError(
                "Plugin IDs must be unique.",
                code="plugin_id_duplicate",
                details={"plugin_id": plugin_id},
            )
        by_id[plugin_id] = plugin
        orderings[plugin_id] = plugin.get_ordering()

    edges: dict[str, set[str]] = {plugin_id: set() for plugin_id in by_id}
    indegree = dict.fromkeys(by_id, 0)

    def add_edge(outer: str, inner: str) -> None:
        if outer == inner:
            raise PluginError(
                "Plugin ordering cannot reference itself.",
                code="plugin_order_self_reference",
                details={"plugin_id": outer},
            )
        if inner not in edges[outer]:
            edges[outer].add(inner)
            indegree[inner] += 1

    for plugin_id, ordering in orderings.items():
        for relation_name, targets in (
            ("wraps", ordering.wraps),
            ("wrapped_by", ordering.wrapped_by),
            ("requires", ordering.requires),
        ):
            for target in targets:
                if not target.strip() or target not in by_id:
                    raise PluginError(
                        "Plugin ordering references an unknown plugin.",
                        code="plugin_order_unknown_reference",
                        details={"plugin_id": plugin_id, "relation": relation_name, "target": target},
                    )
                if relation_name == "wraps":
                    add_edge(plugin_id, target)
                elif relation_name == "wrapped_by":
                    add_edge(target, plugin_id)
                elif target == plugin_id:
                    raise PluginError(
                        "A plugin cannot require itself.",
                        code="plugin_order_self_reference",
                        details={"plugin_id": plugin_id},
                    )

    ids = tuple(by_id)
    for plugin_id in ids:
        position = orderings[plugin_id].position
        if position == "outermost":
            for other in ids:
                if orderings[other].position != "outermost" and other != plugin_id:
                    add_edge(plugin_id, other)
        elif position == "innermost":
            for other in ids:
                if orderings[other].position != "innermost" and other != plugin_id:
                    add_edge(other, plugin_id)

    index = {plugin_id: position for position, plugin_id in enumerate(ids)}
    ready = [plugin_id for plugin_id in ids if indegree[plugin_id] == 0]
    result: list[str] = []
    while ready:
        ready.sort(key=index.__getitem__)
        current = ready.pop(0)
        result.append(current)
        for target in sorted(edges[current], key=index.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(result) != len(ids):
        raise PluginError("Plugin ordering contains a cycle.", code="plugin_order_cycle")
    return tuple(cast(AbstractHarnessPlugin, by_id[plugin_id]) for plugin_id in result)
