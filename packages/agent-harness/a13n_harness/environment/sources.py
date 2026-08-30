"""Developer-facing Environment inputs and their internal aggregate normalization."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from a13n_environment_provider import EnvironmentProvider, EnvironmentResource

from a13n_harness.identity import AgentInstanceContext

from .attachments import create_environment_provider_binding
from .coordinator import create_environment_run_binding, create_noop_environment_run_binding
from .models import (
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
)
from .providers import BoundEnvironmentProvider, EnvironmentProviderBinding, EnvironmentRunBinding

type EnvironmentSource = EnvironmentProvider | EnvironmentResource


class EnvironmentAccess(StrEnum):
    """Convenient permission ceilings for a mounted Environment source."""

    READ_ONLY = "read_only"
    FULL = "full"

    def permission_set(self) -> EnvironmentPermissionSet:
        if self is EnvironmentAccess.FULL:
            return EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))
        return EnvironmentPermissionSet(operations=_READ_ONLY_ACTIONS)


_READ_ONLY_ACTIONS = frozenset(
    {
        EnvironmentAction.FILE_STAT,
        EnvironmentAction.FILE_READ_TEXT,
        EnvironmentAction.FILE_READ_BYTES,
        EnvironmentAction.FILE_LIST,
        EnvironmentAction.FILE_QUERY,
        EnvironmentAction.FILE_SEARCH_TEXT,
        EnvironmentAction.FILE_COPY_SOURCE,
        EnvironmentAction.PROCESS_INSPECT,
        EnvironmentAction.PROCESS_READ_OUTPUT,
        EnvironmentAction.PROCESS_WAIT,
        EnvironmentAction.PROCESS_RELEASE,
        EnvironmentAction.OUTPUT_READ,
        EnvironmentAction.OUTPUT_RELEASE,
        EnvironmentAction.PORT_INSPECT,
        EnvironmentAction.PORT_WAIT,
        EnvironmentAction.STATE_EXPORT,
    }
)


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    """One Environment source with an optional permission and path policy."""

    source: EnvironmentSource
    access: EnvironmentAccess | EnvironmentPermissionSet = EnvironmentAccess.FULL
    working_directory: str | None = "/"

    def __post_init__(self) -> None:
        if not isinstance(self.source, EnvironmentProvider | EnvironmentResource):
            raise TypeError("EnvironmentMount source must be an EnvironmentProvider or EnvironmentResource")
        if not isinstance(self.access, EnvironmentAccess | EnvironmentPermissionSet):
            raise TypeError("EnvironmentMount access must be EnvironmentAccess or EnvironmentPermissionSet")
        if self.working_directory is not None and (
            not isinstance(self.working_directory, str)
            or not self.working_directory.startswith("/")
            or "\x00" in self.working_directory
            or "//" in self.working_directory
            or (self.working_directory != "/" and self.working_directory.endswith("/"))
            or any(segment in {".", ".."} for segment in self.working_directory.split("/"))
        ):
            raise ValueError("EnvironmentMount working_directory must be a canonical absolute path")

    @property
    def permissions(self) -> EnvironmentPermissionSet:
        if isinstance(self.access, EnvironmentAccess):
            return self.access.permission_set()
        return self.access.model_copy(deep=True)


type EnvironmentEntry = EnvironmentSource | EnvironmentMount


class _EnvironmentSourceBinding(EnvironmentProviderBinding):
    """Single-use bridge from a high-level source to one provider binding."""

    def __init__(self, source: EnvironmentSource) -> None:
        self._source = source
        self._provider_type: str | None = None
        self._environment_id: str | None = None
        self._used = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        if self._provider_type is None:
            raise EnvironmentError(
                "Environment source identity is unavailable before attachment entry.",
                code="environment_provider_failure",
            )
        return self._provider_type

    @property
    def environment_id(self) -> str:
        if self._environment_id is None:
            raise EnvironmentError(
                "Environment source identity is unavailable before attachment entry.",
                code="environment_provider_failure",
            )
        return self._environment_id

    @asynccontextmanager
    async def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        binding_id: str,
        binding_version: int,
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        if self._used or self._discarded:
            raise EnvironmentError("Environment source binding is single-use.", code="environment_binding_reused")
        self._used = True
        if isinstance(self._source, EnvironmentProvider):
            correlation = _resource_correlation(run_id, binding_id)
            async with self._source.ephemeral(resource_correlation=correlation) as resource:
                async with self._bind_resource(
                    resource,
                    run_id=run_id,
                    instance=instance,
                    binding_id=binding_id,
                    binding_version=binding_version,
                ) as provider:
                    yield provider
            return

        if not self._source.is_entered:
            raise EnvironmentError(
                "Host-owned EnvironmentResource inputs must be entered before the run starts.",
                code="environment_request_invalid",
            )
        async with self._bind_resource(
            self._source,
            run_id=run_id,
            instance=instance,
            binding_id=binding_id,
            binding_version=binding_version,
        ) as provider:
            yield provider

    @asynccontextmanager
    async def _bind_resource(
        self,
        resource: EnvironmentResource,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        binding_id: str,
        binding_version: int,
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        async with resource.acquire_attachment() as attachment:
            provider_binding = create_environment_provider_binding(attachment)
            self._provider_type = provider_binding.provider_type
            self._environment_id = provider_binding.environment_id
            async with provider_binding.bind(
                run_id=run_id,
                instance=instance,
                binding_id=binding_id,
                binding_version=binding_version,
            ) as provider:
                yield provider

    async def discard(self) -> None:
        self._discarded = True


def normalize_environment_inputs(
    *,
    environment: EnvironmentEntry | None,
    environments: Mapping[str, EnvironmentEntry] | None,
    default_environment: str | None,
    advanced_binding: EnvironmentRunBinding | None,
) -> EnvironmentRunBinding:
    """Normalize public inputs into the existing single aggregate lifecycle."""
    if environment is not None and environments is not None:
        raise EnvironmentError(
            "environment and environments are mutually exclusive.",
            code="environment_request_invalid",
        )
    if default_environment is not None and environments is None:
        raise EnvironmentError(
            "default_environment is valid only with environments.",
            code="environment_request_invalid",
        )
    if advanced_binding is not None and (environment is not None or environments is not None):
        raise EnvironmentError(
            "High-level Environment inputs conflict with RunBindings.environment.",
            code="environment_request_invalid",
        )
    if environment is None and environments is None:
        return advanced_binding or create_noop_environment_run_binding()

    if environment is not None:
        entries = (("workspace", _normalize_entry(environment)),)
        default_alias = "workspace"
    else:
        if not isinstance(environments, Mapping) or not environments:
            raise EnvironmentError(
                "environments must be a non-empty mapping.",
                code="environment_request_invalid",
            )
        entries = tuple((alias, _normalize_entry(entry)) for alias, entry in environments.items())
        aliases = {alias for alias, _entry in entries}
        if default_environment is not None and default_environment not in aliases:
            raise EnvironmentError(
                "default_environment is not present in environments.",
                code="environment_request_invalid",
            )
        default_alias = (
            default_environment if default_environment is not None else entries[0][0] if len(entries) == 1 else None
        )

    try:
        requests = tuple(
            EnvironmentBindingRequest(
                binding_id=_binding_id(alias),
                binding_version=1,
                alias=alias,
                permission_ceiling=mount.permissions,
                default_working_directory=mount.working_directory,
                provider_binding=_EnvironmentSourceBinding(mount.source),
            )
            for alias, mount in entries
        )
        default_binding_id = _binding_id(default_alias) if default_alias is not None else None
        topology = EnvironmentTopologyRequest(
            topology_version=1,
            bindings=requests,
            default_binding_id=default_binding_id,
        )
    except (TypeError, ValueError) as exc:
        raise EnvironmentError(
            "Environment aliases or mount policies are invalid.",
            code="environment_request_invalid",
        ) from exc

    return create_environment_run_binding(
        initial_topology=topology,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _normalize_entry(entry: EnvironmentEntry) -> EnvironmentMount:
    if isinstance(entry, EnvironmentMount):
        return entry
    if isinstance(entry, EnvironmentProvider | EnvironmentResource):
        return EnvironmentMount(entry)
    raise EnvironmentError(
        "Environment inputs must be EnvironmentProvider, EnvironmentResource, or EnvironmentMount values.",
        code="environment_request_invalid",
    )


def _binding_id(alias: str) -> str:
    return f"environment-{alias}"


def _resource_correlation(run_id: str, binding_id: str) -> str:
    digest = sha256(f"{run_id}\0{binding_id}".encode()).hexdigest()[:24]
    return f"resource-{digest}"
