"""Developer-facing Environment inputs and aggregate normalization."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from a13n_environment import Environment

from a13n_harness.identity import AgentInstanceContext

from ._mount_path import parse_mount_path
from .coordinator import create_empty_environment_runtime, create_environment_runtime
from .models import EnvironmentAction, EnvironmentError, EnvironmentPermissionSet
from .providers import (
    BoundEnvironmentProvider,
    EnvironmentProviderBinding,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
)


class EnvironmentAccess(StrEnum):
    """User-facing access level for one Environment mount."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    FULL = "full"

    def permission_set(self) -> EnvironmentPermissionSet:
        if self is EnvironmentAccess.FULL:
            operations = frozenset(EnvironmentAction)
        elif self is EnvironmentAccess.READ_WRITE:
            operations = _READ_WRITE_ACTIONS
        else:
            operations = _READ_ONLY_ACTIONS
        return EnvironmentPermissionSet(operations=operations)


_READ_ONLY_ACTIONS = frozenset(
    {
        EnvironmentAction.FILE_STAT,
        EnvironmentAction.FILE_READ_TEXT,
        EnvironmentAction.FILE_READ_BYTES,
        EnvironmentAction.FILE_LIST,
        EnvironmentAction.FILE_QUERY,
        EnvironmentAction.FILE_SEARCH_TEXT,
        EnvironmentAction.FILE_COPY_SOURCE,
    }
)
_READ_WRITE_ACTIONS = frozenset(action for action in EnvironmentAction if action.value.startswith("environment.file."))


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    """One already constructed Environment plus Run-local access and path policy."""

    environment: Environment
    access: EnvironmentAccess = EnvironmentAccess.FULL
    working_directory: str | None = "/"
    mount_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise TypeError("EnvironmentMount environment must be an Environment")
        if not isinstance(self.access, EnvironmentAccess):
            raise TypeError("EnvironmentMount access must be an EnvironmentAccess")
        directory = self.working_directory
        if directory is not None and (
            not isinstance(directory, str)
            or not directory.startswith("/")
            or "\x00" in directory
            or "//" in directory
            or (directory != "/" and directory.endswith("/"))
            or any(segment in {".", ".."} for segment in directory.split("/"))
        ):
            raise ValueError("EnvironmentMount working_directory must be a canonical absolute path")
        if self.mount_path is not None:
            parse_mount_path(self.mount_path)

    @property
    def permissions(self) -> EnvironmentPermissionSet:
        return self.access.permission_set()


type EnvironmentEntry = Environment | EnvironmentMount


class _EnvironmentAdapterBinding(EnvironmentProviderBinding):
    def __init__(self, environment: Environment) -> None:
        self._environment = environment
        self._used = False
        self._discarded = False

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

    identities = [id(mount.environment) for _name, mount in entries]
    if len(identities) != len(set(identities)):
        raise EnvironmentError(
            "One Environment instance cannot be mounted more than once.", code="environment_request_invalid"
        )
    try:
        mounts = {
            name: EnvironmentRuntimeMount(
                binding=_EnvironmentAdapterBinding(mount.environment),
                permission_ceiling=mount.permissions,
                working_directory=mount.working_directory,
                mount_path=mount.mount_path,
            )
            for name, mount in entries
        }
    except (TypeError, ValueError) as error:
        raise EnvironmentError(
            "Environment names or mount policies are invalid.", code="environment_request_invalid"
        ) from error
    return create_environment_runtime(mounts=mounts, default_mount=default_alias)


def _normalize_entry(entry: EnvironmentEntry) -> EnvironmentMount:
    if isinstance(entry, EnvironmentMount):
        return entry
    if isinstance(entry, Environment):
        return EnvironmentMount(entry)
    raise EnvironmentError(
        "Environment inputs must be Environment or EnvironmentMount values.",
        code="environment_request_invalid",
    )
