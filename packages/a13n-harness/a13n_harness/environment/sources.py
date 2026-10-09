"""Developer-facing Environment inputs and aggregate normalization."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_environment.models import FILE_EXECUTION_ACTIONS, EnvironmentError, EnvironmentPermissionSet
from a13n_logging import get_logger

from a13n_harness.identity import AgentInstanceContext

from ._mount_path import parse_mount_path, validate_working_directory
from .providers import (
    EnvironmentProviderBinding,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
    _claim_execution,
)

_DEFAULT_ACTIONS = EnvironmentPermissionSet(operations=FILE_EXECUTION_ACTIONS)


@dataclass(frozen=True, slots=True)
class EnvironmentScope:
    thread_id: str
    run_id: str
    agent_instance_id: str
    mount_id: str


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    """One fixed-target connector plus Run-local permission and path policy."""

    connector: EnvironmentConnector
    permission_ceiling: EnvironmentPermissionSet = _DEFAULT_ACTIONS
    working_directory: str | None = None
    mount_path: str | None = None
    provider_root: str = "/"
    observer: Callable[[str, EnvironmentScope, BaseException | None], None] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.connector, EnvironmentConnector):
            raise TypeError("EnvironmentMount connector must be an EnvironmentConnector")
        if not isinstance(self.permission_ceiling, EnvironmentPermissionSet):
            raise TypeError("EnvironmentMount permission_ceiling must be an EnvironmentPermissionSet")
        if self.working_directory is None:
            object.__setattr__(self, "working_directory", self.connector.descriptor.working_directory)
        validate_working_directory(self.working_directory)
        validate_working_directory(self.provider_root)
        if self.mount_path is not None:
            parse_mount_path(self.mount_path)


type EnvironmentEntry = EnvironmentConnector | EnvironmentMount


class _EnvironmentConnectorBinding(EnvironmentProviderBinding):
    def __init__(self, mount: EnvironmentMount) -> None:
        self._connector = mount.connector
        self._observer = mount.observer
        self._used = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        return self._connector.provider_key

    @property
    def environment_id(self) -> str:
        return self._connector.environment_id

    def _observe(self, event: str, scope: EnvironmentScope, error: BaseException | None = None) -> None:
        if self._observer is not None:
            try:
                self._observer(event, scope, error)
            except Exception:
                get_logger(__name__).exception("Environment lifecycle observation failed")

    @asynccontextmanager
    async def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> AsyncGenerator[EnvironmentExecution]:
        if self._used or self._discarded:
            raise EnvironmentError("Environment binding is single-use.", code="environment_provider_binding_reused")
        self._used = True
        scope = EnvironmentScope(thread_id, run_id, instance.agent_instance_id, mount_id)
        self._observe("started", scope)
        try:
            execution = await self._connector.open()
        except BaseException as error:
            self._observe("failed", scope, error)
            raise
        if not _claim_execution(execution, self):
            raise EnvironmentError("Environment execution is already owned.", code="environment_execution_reused")
        self._observe("ready", scope)
        try:
            yield execution
        finally:
            try:
                await execution.close()
            except BaseException as error:
                self._observe("closed", scope, error)
                raise
            else:
                self._observe("closed", scope)

    async def discard(self) -> None:
        # Inert connectors have no resource to close before opening.
        self._discarded = True


def normalize_environment_inputs(
    *,
    environment: EnvironmentEntry | None,
    environments: Mapping[str, EnvironmentEntry] | None,
    default_environment: str | None,
    advanced_binding: EnvironmentRuntime | None,
) -> EnvironmentRuntime:
    from .coordinator import create_empty_environment_runtime, create_environment_runtime

    if environment is not None and environments is not None:
        raise EnvironmentError(
            "environment and environments are mutually exclusive.", code="environment_request_invalid"
        )
    if default_environment is not None and environments is None:
        raise EnvironmentError(
            "default_environment is valid only with environments.", code="environment_request_invalid"
        )
    if advanced_binding is not None and (environment is not None or environments is not None):
        raise EnvironmentError(
            "High-level Environment inputs conflict with RunBindings.environment.",
            code="environment_request_invalid",
        )
    if environment is None and environments is None:
        return advanced_binding or create_empty_environment_runtime()

    if environment is not None:
        entries = (("workspace", _normalize_entry(environment)),)
        default_alias = "workspace"
    else:
        if not isinstance(environments, Mapping) or not environments:
            raise EnvironmentError("environments must be a non-empty mapping.", code="environment_request_invalid")
        entries = tuple((alias, _normalize_entry(entry)) for alias, entry in environments.items())
        aliases = {alias for alias, _entry in entries}
        if default_environment is not None and default_environment not in aliases:
            raise EnvironmentError(
                "default_environment is not present in environments.", code="environment_request_invalid"
            )
        default_alias = (
            default_environment if default_environment is not None else entries[0][0] if len(entries) == 1 else None
        )

    return create_environment_runtime(mounts=dict(entries), default_mount=default_alias)


def _normalize_runtime_mount(entry: EnvironmentEntry | EnvironmentRuntimeMount) -> EnvironmentRuntimeMount:
    if isinstance(entry, EnvironmentRuntimeMount):
        return entry
    mount = _normalize_entry(entry)
    return EnvironmentRuntimeMount(
        binding=_EnvironmentConnectorBinding(mount),
        permission_ceiling=mount.permission_ceiling,
        working_directory=mount.working_directory,
        mount_path=mount.mount_path,
        provider_root=mount.provider_root,
    )


def _normalize_entry(entry: EnvironmentEntry) -> EnvironmentMount:
    if isinstance(entry, EnvironmentMount):
        return entry
    if isinstance(entry, EnvironmentConnector):
        return EnvironmentMount(entry)
    raise EnvironmentError(
        "Environment inputs must be EnvironmentConnector or EnvironmentMount values.",
        code="environment_request_invalid",
    )
