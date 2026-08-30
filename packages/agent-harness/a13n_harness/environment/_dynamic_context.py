"""Dynamic Environment context projection and managed-resource fencing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import ProcessIdentity
from a13n_harness.environment.models import (
    EnvironmentError,
)
from a13n_harness.model_context import (
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
)
from a13n_harness.tools.metadata import CanonicalResource, ToolResourceResolver
from a13n_harness.toolsets.files import FilePathPair

from .configuration import DynamicEnvironmentConfiguration
from .providers import BoundEnvironment


@dataclass(frozen=True, slots=True)
class _BindingFence:
    binding_id: str
    binding_version: int
    observed_generation: str


@dataclass(frozen=True, slots=True)
class _AuthorizationFence:
    topology_version: int
    bindings: tuple[_BindingFence, ...]
    unresolved: bool = False


class _DynamicEnvironmentContext:
    """Run-local topology projection and authorization fence shared by Toolsets."""

    def __init__(
        self,
        configuration: DynamicEnvironmentConfiguration,
        *,
        run_id: str,
        environment: BoundEnvironment,
        resolve_process_identity: Callable[[str], ProcessIdentity],
    ) -> None:
        self.configuration = configuration.model_copy(deep=True)
        self._run_id = run_id
        self._environment = environment
        self._resolve_process_identity = resolve_process_identity
        self._pending_version: int | None = None
        self._notice_pending = False
        self._active_context: RunContext[AgentContext] | None = None
        self._observer_task: asyncio.Task[None] | None = None
        self._authorization_fence: ContextVar[_AuthorizationFence | None] = ContextVar(
            f"environment_authorization_fence_{run_id}",
            default=None,
        )
        self._fence_builder: ContextVar[list[_BindingFence] | None] = ContextVar(
            f"environment_fence_builder_{run_id}",
            default=None,
        )

    def _resource_resolver(self, tool_id: str) -> ToolResourceResolver:
        async def resolve(
            arguments: Mapping[str, object],
            *,
            context: AgentContext,
        ) -> tuple[CanonicalResource, ...]:
            topology_version = context.environment.topology.topology_version
            fences: list[_BindingFence] = []
            token = self._fence_builder.set(fences)
            try:
                resources = self._resolve_resources(tool_id, arguments, context)
            except EnvironmentError as exc:
                if tool_id in {
                    "filesystem.mkdir",
                    "filesystem.move",
                    "filesystem.copy",
                    "filesystem.remove",
                }:
                    raise
                if exc.code not in {
                    "environment_reference_invalid",
                    "environment_reference_stale",
                    "environment_selection_invalid",
                    "environment_unavailable",
                }:
                    raise
                self._authorization_fence.set(
                    _AuthorizationFence(topology_version=topology_version, bindings=(), unresolved=True)
                )
                return ()
            finally:
                self._fence_builder.reset(token)
            self._authorization_fence.set(
                _AuthorizationFence(
                    topology_version=topology_version,
                    bindings=tuple(dict.fromkeys(fences)),
                    unresolved=(tool_id.startswith("environment.process_") and tool_id != "environment.process_status"),
                )
            )
            return resources

        return resolve

    def _resolve_resources(
        self,
        tool_id: str,
        arguments: Mapping[str, object],
        context: AgentContext,
    ) -> tuple[CanonicalResource, ...]:
        if tool_id in {
            "filesystem.view",
            "filesystem.write",
            "filesystem.edit",
            "filesystem.multi_edit",
        }:
            return (self._path_resource(context, _string_argument(arguments, "file_path")),)
        if tool_id == "filesystem.mkdir":
            return self._path_resources(context, _string_sequence_argument(arguments, "paths"))
        if tool_id in {"filesystem.move", "filesystem.copy"}:
            pairs = _path_pair_sequence_argument(arguments, "pairs")
            return self._path_resources(
                context,
                tuple(path for pair in pairs for path in (pair.src, pair.dst)),
            )
        if tool_id == "filesystem.remove":
            return self._path_resources(context, _string_sequence_argument(arguments, "paths"))
        if tool_id == "filesystem.ls":
            return (self._path_resource(context, _string_argument(arguments, "path")),)
        if tool_id in {"filesystem.glob", "filesystem.grep"}:
            return (self._path_resource(context, _optional_string_argument(arguments, "root") or "."),)
        if tool_id == "environment.shell_exec":
            alias = _optional_string_argument(arguments, "alias")
            cwd = _optional_string_argument(arguments, "cwd")
            if cwd is not None:
                return (self._path_resource(context, cwd, alias=alias),)
            return (self._binding_resource(context, alias),)
        if tool_id == "environment.process_status":
            return ()
        if tool_id.startswith("environment.process_"):
            identity = self._resolve_process_identity(_string_argument(arguments, "process_id"))
            return (
                CanonicalResource(
                    namespace="environment",
                    kind="process",
                    identifier=identity.model_dump_json(),
                ),
            )
        if tool_id.startswith("environment.port_"):
            return (self._binding_resource(context, _optional_string_argument(arguments, "alias")),)
        return ()

    def _path_resources(
        self,
        context: AgentContext,
        paths: tuple[str, ...],
    ) -> tuple[CanonicalResource, ...]:
        return tuple(dict.fromkeys(self._path_resource(context, path) for path in paths))

    def _path_resource(
        self,
        context: AgentContext,
        path: str,
        *,
        alias: str | None = None,
    ) -> CanonicalResource:
        selected = context.environment.resolve_path(path, alias=alias)
        binding = next(item for item in context.environment.topology.bindings if item.binding_id == selected.binding_id)
        self._record_fence(binding.binding_id, binding.binding_version, binding.descriptor.generation)
        return CanonicalResource(
            namespace="environment",
            kind="file",
            identifier=(
                f"{selected.binding_id}:{selected.binding_version}:{binding.descriptor.generation}:{selected.path}"
            ),
        )

    def _binding_resource(self, context: AgentContext, alias: str | None) -> CanonicalResource:
        topology = context.environment.topology
        if alias is None:
            binding = next(
                (item for item in topology.bindings if item.binding_id == topology.default_binding_id),
                None,
            )
        else:
            binding = next((item for item in topology.bindings if item.alias == alias), None)
        if binding is None:
            raise EnvironmentError(
                "The selected Environment binding is unavailable.",
                code="environment_selection_invalid",
            )
        self._record_fence(binding.binding_id, binding.binding_version, binding.descriptor.generation)
        return CanonicalResource(
            namespace="environment",
            kind="binding",
            identifier=f"{binding.binding_id}:{binding.binding_version}:{binding.descriptor.generation}",
        )

    def _record_fence(self, binding_id: str, binding_version: int, generation: str) -> None:
        builder = self._fence_builder.get()
        if builder is not None:
            builder.append(_BindingFence(binding_id, binding_version, generation))

    def _assert_authorized_fence(self) -> None:
        fence = self._authorization_fence.get()
        if fence is None:
            return
        topology = self._environment.topology
        if fence.unresolved:
            if topology.topology_version != fence.topology_version:
                raise EnvironmentError(
                    "Environment topology changed after managed resource authorization.",
                    code="environment_stale_binding",
                )
            return
        current = {item.binding_id: item for item in topology.bindings}
        for expected in fence.bindings:
            binding = current.get(expected.binding_id)
            if (
                binding is None
                or binding.binding_version != expected.binding_version
                or binding.descriptor.generation != expected.observed_generation
            ):
                raise EnvironmentError(
                    "Environment binding changed after managed resource authorization.",
                    code="environment_stale_binding",
                )

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Any) -> Any:
        self._active_context = ctx
        self._ensure_observer(ctx)
        try:
            return await handler()
        finally:
            if self._active_context is ctx:
                self._active_context = None

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        if request.kind is ModelContextRequestKind.INPUT:
            topology_version = ctx.deps.environment.topology.topology_version
            if self._pending_version is not None and self._pending_version <= topology_version:
                self._pending_version = None
            self._notice_pending = False
        return projection

    def _ensure_observer(self, ctx: RunContext[AgentContext]) -> None:
        if self._observer_task is not None:
            return
        self._observer_task = asyncio.create_task(self._observe_topology(ctx.deps))
        self._observer_task.add_done_callback(_consume_task_result)

    async def _observe_topology(self, context: AgentContext) -> None:
        observer = context.environment.topology_observer
        cursor = observer.initial_topology_version
        while True:
            try:
                changes = await observer.read(after_version=cursor, wait=True)
            except EnvironmentError as exc:
                if exc.code == "environment_closed":
                    return
                raise
            if not changes:
                return
            cursor = changes[-1].current_version
            self._pending_version = cursor
            active = self._active_context
            if active is not None and not self._notice_pending:
                self._notice_pending = True
                active.enqueue(
                    "The Environment topology changed. A fresh bounded topology snapshot is attached to this request.",
                    priority="asap",
                )


def _string_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _optional_string_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return value


def _string_sequence_argument(arguments: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if not isinstance(value, list | tuple) or not value or not all(isinstance(item, str) and item for item in value):
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return tuple(value)


def _path_pair_sequence_argument(arguments: Mapping[str, object], name: str) -> tuple[FilePathPair, ...]:
    value = arguments.get(name)
    if not isinstance(value, list | tuple) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    pairs: list[FilePathPair] = []
    for item in value:
        if isinstance(item, FilePathPair):
            pairs.append(item)
            continue
        if isinstance(item, Mapping):
            try:
                pairs.append(FilePathPair.model_validate(item, strict=True))
            except ValueError as exc:
                raise EnvironmentError(
                    f"Environment tool argument {name!r} is invalid.",
                    code="environment_request_invalid",
                ) from exc
            continue
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
        )
    return tuple(pairs)


def _consume_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        # The Environment lifecycle remains authoritative; notices are best effort.
        return


__all__ = ["_DynamicEnvironmentContext"]
