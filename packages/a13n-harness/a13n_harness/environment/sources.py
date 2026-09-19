"""Developer-facing Environment inputs and aggregate normalization."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_environment import Environment

from a13n_harness.identity import AgentInstanceContext

from ._mount_path import parse_mount_path, validate_working_directory
from .models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from .providers import (
    BoundEnvironmentProvider,
    EnvironmentProviderBinding,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
)

_EVERY_ACTION = EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    """One already constructed Environment plus Run-local permission and path policy."""

    environment: Environment
    permission_ceiling: EnvironmentPermissionSet = _EVERY_ACTION
    working_directory: str | None = None
    mount_path: str | None = None
    provider_root: str = "/"

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise TypeError("EnvironmentMount environment must be an Environment")
        if not isinstance(self.permission_ceiling, EnvironmentPermissionSet):
            raise TypeError("EnvironmentMount permission_ceiling must be an EnvironmentPermissionSet")
        if self.working_directory is None:
            object.__setattr__(self, "working_directory", self.environment.descriptor.working_directory)
        validate_working_directory(self.working_directory)
        validate_working_directory(self.provider_root)
        if self.mount_path is not None:
            parse_mount_path(self.mount_path)


type EnvironmentEntry = Environment | EnvironmentMount


class _EnvironmentAdapterBinding(EnvironmentProviderBinding):
    def __init__(self, environment: Environment) -> None:
        self._environment = environment
        self._used = False
        self._discarded = False

    @property
    def _transfer_owner(self) -> object:
        return self._environment

    def _claim_transfer(self) -> bool:
        if self._used or self._discarded or self._environment.is_entered:
            return False
        return super()._claim_transfer()

    @property
    def provider_type(self) -> str:
        return self._environment.provider_key

    @property
    def environment_id(self) -> str:
        return self._environment.environment_id

    @asynccontextmanager
    async def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        if self._used or self._discarded:
            raise EnvironmentError("Environment adapter is single-use.", code="environment_provider_binding_reused")
        self._used = True
        try:
            await self._environment.enter(
                thread_id=thread_id,
                run_id=run_id,
                agent_instance_id=instance.agent_instance_id,
                mount_id=mount_id,
                host_refs=host_refs,
            )
            yield self._environment
        finally:
            await self._environment.close()

    async def discard(self) -> None:
        self._discarded = True
        await self._environment.close()


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
        binding=_EnvironmentAdapterBinding(mount.environment),
        permission_ceiling=mount.permission_ceiling,
        working_directory=mount.working_directory,
        mount_path=mount.mount_path,
        provider_root=mount.provider_root,
    )


def _normalize_entry(entry: EnvironmentEntry) -> EnvironmentMount:
    if isinstance(entry, EnvironmentMount):
        return entry
    if isinstance(entry, Environment):
        return EnvironmentMount(entry)
    raise EnvironmentError(
        "Environment inputs must be Environment or EnvironmentMount values.",
        code="environment_request_invalid",
    )
