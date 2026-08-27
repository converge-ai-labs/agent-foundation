from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import a13n_harness.environment.coordinator as environment_coordinator
import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentBindingState,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
    EnvironmentRunExtensionContext,
    EnvironmentState,
    FileMetadata,
    FileWriteResult,
)
from a13n_harness.environment.advanced import (
    EnvironmentBindingRequest,
    EnvironmentProviderBinding,
    EnvironmentProviderOperations,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    NoopBoundEnvironment,
    create_environment_run_binding,
    create_noop_environment_run_binding,
)
from a13n_harness.environment.commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandRequest,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessStartResult,
    ProcessStatus,
)
from a13n_harness.environment.models import EnvironmentOperationReceipt
from a13n_harness.environment.retention import (
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    OpaqueProcessHandle,
)

pytestmark = pytest.mark.anyio


class _Files:
    async def stat(self, path: str) -> FileMetadata:
        return FileMetadata(path=path, kind="file", size=0, writable=True)


@dataclass
class _BoundProvider:
    provider_type: str
    environment_id: str
    descriptor: EnvironmentDescriptor
    operations: EnvironmentProviderOperations
    availability: EnvironmentAvailability
    ready_calls: list[frozenset[str]] = field(default_factory=list)
    export_calls: int = 0
    restore_calls: int = 0

    async def ensure_ready(self, operations: frozenset[str]) -> None:
        self.ready_calls.append(operations)
        self.availability = EnvironmentAvailability(
            status="available",
            ready_families=self.availability.ready_families | operations,
        )

    async def export_state(self, *, max_bytes: int):
        del max_bytes
        self.export_calls += 1
        return None

    async def restore_state(self, state) -> None:
        del state
        self.restore_calls += 1


class _Binding(EnvironmentProviderBinding):
    def __init__(
        self,
        name: str,
        *,
        families: frozenset[str] = frozenset({"files"}),
        operations: EnvironmentProviderOperations | None = None,
        fail_entry: bool = False,
        permissions: frozenset[EnvironmentAction] | None = None,
    ) -> None:
        self.name = name
        self._families = families
        self._operations = operations if operations is not None else EnvironmentProviderOperations(files=_Files())
        self._fail_entry = fail_entry
        self.entered = 0
        self.exited = 0
        self.exit_event = asyncio.Event()
        self.discarded = 0
        selected_permissions = permissions
        if selected_permissions is None:
            selected_permissions = frozenset({EnvironmentAction.FILE_STAT}) if "files" in families else frozenset()
        self.bound = _BoundProvider(
            provider_type="test.provider",
            environment_id=f"environment:{name}",
            descriptor=EnvironmentDescriptor(
                generation=f"generation-{name}",
                operation_families=families,
                permissions=EnvironmentPermissionSet(operations=selected_permissions),
                limits={"nested": {"items": [1, 2]}},
            ),
            operations=self._operations,
            availability=EnvironmentAvailability(status="preparing"),
        )

    @property
    def provider_type(self) -> str:
        return "test.provider"

    @property
    def environment_id(self) -> str:
        return f"environment:{self.name}"

    @asynccontextmanager
    async def bind(self, **kwargs: Any) -> AsyncGenerator[Any]:
        del kwargs
        self.entered += 1
        if self._fail_entry:
            raise RuntimeError("entry failed")
        try:
            yield self.bound
        finally:
            self.exited += 1
            self.exit_event.set()

    async def discard(self) -> None:
        self.discarded += 1


class _RunExtension:
    def __init__(
        self,
        extension_id: str,
        events: list[str],
        *,
        fail_entry: bool = False,
        fail_exit: bool = False,
        provider: _Binding | None = None,
    ) -> None:
        self._extension_id = extension_id
        self._events = events
        self._fail_entry = fail_entry
        self._fail_exit = fail_exit
        self._provider = provider

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context: EnvironmentRunExtensionContext) -> AsyncGenerator[None]:
        self._events.append(f"enter:{self.extension_id}:{context.environment.restored_state_topology_version}")
        if self._fail_entry:
            raise RuntimeError("secret extension entry failure")
        try:
            yield
        finally:
            if self._provider is not None:
                observation = await context.environment.files.stat("/workspace/extension.txt")
                self._events.append(f"open:{observation.path}:{self._provider.exited}")
            self._events.append(f"exit:{self.extension_id}")
            if self._fail_exit:
                raise RuntimeError("secret extension exit failure")


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-1",
    )


def _binding_request(
    provider: _Binding | None,
    *,
    binding_id: str,
    revision: int,
    alias: str,
    default_working_directory: str | None = "/",
) -> EnvironmentBindingRequest:
    return EnvironmentBindingRequest(
        binding_id=binding_id,
        binding_revision=revision,
        alias=alias,
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_STAT})),
        default_working_directory=default_working_directory,
        provider_binding=provider,
    )


def _request(*bindings: _Binding) -> EnvironmentTopologyRequest:
    entries = tuple(
        EnvironmentBindingRequest(
            binding_id=f"binding-{index}",
            binding_revision=1,
            alias=f"workspace-{index}",
            permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_STAT})),
            default_working_directory="/",
            provider_binding=binding,
        )
        for index, binding in enumerate(bindings, start=1)
    )
    return EnvironmentTopologyRequest(
        topology_version=1,
        bindings=entries,
        default_binding_id=entries[0].binding_id if entries else None,
    )


async def test_noop_uses_complete_aggregate_and_is_single_use() -> None:
    binding = create_noop_environment_run_binding()
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        assert isinstance(environment, NoopBoundEnvironment)
        assert environment.topology.bindings == ()
        assert (await environment.export_state()).bindings == {}
        await environment.activate()
        await binding.controller.wait_until_active()
        with pytest.raises(EnvironmentError) as unavailable:
            await environment.files.stat("/workspace/missing")
        assert unavailable.value.code == "environment_selection_invalid"
    with pytest.raises(EnvironmentError) as reused:
        async with binding.bind(run_id="run-2", instance=_instance()):
            pass
    assert reused.value.code == "environment_binding_reused"


async def test_static_aggregate_intersects_permissions_and_scopes_readiness() -> None:
    first = _Binding("first")
    second = _Binding("second")
    binding = create_environment_run_binding(
        initial_topology=_request(first, second),
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=5),
        state_limits=EnvironmentStateLimits(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        assert [item.alias for item in environment.topology.bindings] == ["workspace-1", "workspace-2"]
        await environment.ensure_ready(
            EnvironmentReadinessRequirement(
                operations=frozenset({"files"}),
                binding_ids=frozenset({"binding-2"}),
            )
        )
        assert first.bound.ready_calls == []
        assert second.bound.ready_calls == [frozenset({"files"})]
        assert (await environment.describe("binding-2")).availability.status == "available"
    assert first.exited == second.exited == 1
    assert first.discarded == second.discarded == 0


async def test_environment_run_extensions_follow_aggregate_lifecycle_and_remain_open_on_exit() -> None:
    events: list[str] = []
    provider = _Binding("extensions")
    first = _RunExtension("first", events, provider=provider)
    second = _RunExtension("second", events)
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
        extensions=(first, second),
    )

    async with binding.bind(run_id="run-extensions", instance=_instance()) as environment:
        await environment.restore_state(EnvironmentState(observed_topology_version=9))
        await environment.activate()
        assert events == ["enter:first:9", "enter:second:9"]
        retained = _binding_request(
            None,
            binding_id="binding-1",
            revision=1,
            alias="workspace-1",
        )
        await binding.controller.apply(
            EnvironmentTopologyRequest(
                topology_version=2,
                bindings=(retained,),
                default_binding_id="binding-1",
            )
        )
        assert environment.topology.topology_version == 2
        assert events == ["enter:first:9", "enter:second:9"]

    assert events == [
        "enter:first:9",
        "enter:second:9",
        "exit:second",
        "open:/workspace/extension.txt:0",
        "exit:first",
    ]
    assert provider.exited == 1


async def test_environment_run_extension_entry_failure_unwinds_and_keeps_controller_inactive() -> None:
    events: list[str] = []
    provider = _Binding("extension-entry-failure")
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
        extensions=(
            _RunExtension("first", events),
            _RunExtension("failing", events, fail_entry=True),
        ),
    )

    async with binding.bind(run_id="run-extension-failure", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as exc_info:
            await environment.activate()
        assert exc_info.value.code == "environment_extension_bind_failed"
        assert events == ["enter:first:None", "enter:failing:None", "exit:first"]
        with pytest.raises(EnvironmentError) as repeated:
            await environment.activate()
        assert repeated.value.code == "environment_extension_bind_failed"
        assert binding.controller.can_apply is False

    assert provider.exited == 1


async def test_environment_run_extension_cleanup_continues_after_failure() -> None:
    events: list[str] = []
    provider = _Binding("extension-exit-failure")
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
        extensions=(
            _RunExtension("first", events),
            _RunExtension("failing", events, fail_exit=True),
        ),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        async with binding.bind(run_id="run-extension-cleanup", instance=_instance()) as environment:
            await environment.activate()

    assert "Environment entered-resource cleanup failed" in str(exc_info.value)
    assert events == [
        "enter:first:None",
        "enter:failing:None",
        "exit:failing",
        "exit:first",
    ]
    assert provider.exited == 1


@pytest.mark.parametrize("extension_id", ["", " spaced", "x" * 201])
def test_environment_run_extension_ids_are_validated(extension_id: str) -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        create_environment_run_binding(
            initial_topology=_request(),
            topology_limits=EnvironmentTopologyLimits(),
            state_limits=EnvironmentStateLimits(),
            extensions=(_RunExtension(extension_id, []),),
        )

    assert exc_info.value.code == "environment_extension_id_invalid"


def test_environment_run_extension_ids_must_be_unique() -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        create_environment_run_binding(
            initial_topology=_request(),
            topology_limits=EnvironmentTopologyLimits(),
            state_limits=EnvironmentStateLimits(),
            extensions=(_RunExtension("same", []), _RunExtension("same", [])),
        )

    assert exc_info.value.code == "environment_extension_duplicate"


async def test_environment_state_restore_is_rejected_after_extension_activation_begins() -> None:
    binding = create_environment_run_binding(
        initial_topology=_request(),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
        extensions=(_RunExtension("extension", []),),
    )

    async with binding.bind(run_id="run-late-restore", instance=_instance()) as environment:
        await environment.activate()
        with pytest.raises(EnvironmentError) as exc_info:
            await environment.restore_state(EnvironmentState(observed_topology_version=1))

    assert exc_info.value.code == "state_invalid"


async def test_readiness_is_reacquired_after_live_availability_regresses() -> None:
    provider = _Binding("readiness-regression")
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    requirement = EnvironmentReadinessRequirement(operations=frozenset({"files"}))
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.ensure_ready(requirement)
        provider.bound.availability = EnvironmentAvailability(status="preparing")
        await environment.ensure_ready(requirement)

    assert provider.bound.ready_calls == [frozenset({"files"}), frozenset({"files"})]


async def test_descriptor_values_are_recursively_detached_and_immutable() -> None:
    source = {"nested": {"items": [1, 2]}}
    descriptor = EnvironmentDescriptor(
        generation="generation-1",
        operation_families=frozenset(),
        permissions=EnvironmentPermissionSet(),
        limits=source,
    )
    source["nested"]["items"].append(3)
    nested = descriptor.limits["nested"]
    assert nested["items"] == (1, 2)
    with pytest.raises(TypeError):
        descriptor.limits["other"] = 1


async def test_descriptor_facet_mismatch_fails_and_all_candidates_are_owned() -> None:
    invalid = _Binding("invalid", operations=EnvironmentProviderOperations())
    never_entered = _Binding("later")
    binding = create_environment_run_binding(
        initial_topology=_request(invalid, never_entered),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    with pytest.raises(EnvironmentError) as failure:
        async with binding.bind(run_id="run-1", instance=_instance()):
            pass
    assert failure.value.code == "environment_provider_failure"
    assert invalid.entered == invalid.exited == 1
    assert never_entered.entered == 0
    assert never_entered.discarded == 1


async def test_partial_entry_failure_closes_entered_and_discards_remaining_candidates() -> None:
    entered = _Binding("entered")
    failed = _Binding("failed", fail_entry=True)
    later = _Binding("later")
    binding = create_environment_run_binding(
        initial_topology=_request(entered, failed, later),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    with pytest.raises(RuntimeError, match="entry failed"):
        async with binding.bind(run_id="run-1", instance=_instance()):
            pass
    assert entered.exited == 1
    assert failed.discarded == later.discarded == 1


async def test_invalid_topology_is_rejected_before_provider_entry() -> None:
    candidate = _Binding("one")
    request = _request(candidate)
    duplicate = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(request.bindings[0], request.bindings[0]),
        default_binding_id="binding-1",
    )
    with pytest.raises(EnvironmentError):
        create_environment_run_binding(
            initial_topology=duplicate,
            topology_limits=EnvironmentTopologyLimits(),
            state_limits=EnvironmentStateLimits(),
        )
    assert candidate.entered == candidate.discarded == 0


async def test_closed_environment_rejects_new_provider_work() -> None:
    provider = _Binding("closed")
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))
    calls = len(provider.bound.ready_calls)
    with pytest.raises(EnvironmentError) as closed:
        await environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))
    assert closed.value.code == "environment_closed"
    assert len(provider.bound.ready_calls) == calls


async def test_readiness_requires_live_ready_observation() -> None:
    provider = _Binding("not-ready")

    async def no_progress(operations: frozenset[str]) -> None:
        provider.bound.ready_calls.append(operations)

    provider.bound.ensure_ready = no_progress  # type: ignore[method-assign]
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as unavailable:
            await environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))
        assert unavailable.value.code == "environment_unavailable"


async def test_unavailable_status_rejects_residual_ready_family() -> None:
    provider = _Binding("unavailable")

    async def become_unavailable(operations: frozenset[str]) -> None:
        provider.bound.ready_calls.append(operations)
        provider.bound.availability = EnvironmentAvailability(
            status="unavailable",
            ready_families=operations,
            reason_code="gone",
        )

    provider.bound.ensure_ready = become_unavailable  # type: ignore[method-assign]
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as unavailable:
            await environment.files.stat("/workspace/value")
        assert unavailable.value.code == "environment_unavailable"


async def test_accepted_operation_is_cancelled_before_provider_exit() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingFiles:
        async def stat(self, path: str) -> dict[str, str]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return {"path": path}

    provider = _Binding("leased", operations=EnvironmentProviderOperations(files=BlockingFiles()))
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    task: asyncio.Task[Any]
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        task = asyncio.create_task(environment.files.stat("/workspace/value"))
        await started.wait()
    assert cancelled.is_set()
    assert task.cancelled()
    assert provider.exited == 1


async def test_file_operation_fallback_covers_readiness_and_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment_coordinator, "DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS", 0.01)

    class BlockingFiles:
        async def stat(self, path: str) -> dict[str, str]:
            del path
            await asyncio.Event().wait()
            return {}

    provider = _Binding("timeout", operations=EnvironmentProviderOperations(files=BlockingFiles()))
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as timed_out:
            await environment.files.stat("/workspace/value")
        assert timed_out.value.code == "environment_timeout"


async def test_each_provider_exit_has_an_independent_cleanup_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment_coordinator, "DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS", 0.01)
    reached: list[str] = []

    class HangingBinding(_Binding):
        @asynccontextmanager
        async def bind(self, **kwargs: Any) -> AsyncGenerator[Any]:
            del kwargs
            self.entered += 1
            try:
                yield self.bound
            finally:
                reached.append(self.name)
                await asyncio.Event().wait()

    first = HangingBinding("first-exit")
    second = HangingBinding("second-exit")
    binding = create_environment_run_binding(
        initial_topology=_request(first, second),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    with pytest.raises(BaseExceptionGroup):
        async with binding.bind(run_id="run-1", instance=_instance()):
            pass
    assert reached == ["second-exit", "first-exit"]


async def test_invalid_identity_does_not_transfer_or_consume_binding() -> None:
    provider = _Binding("identity")
    binding = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    with pytest.raises(EnvironmentError):
        async with binding.bind(run_id="", instance=_instance()):
            pass
    assert provider.entered == provider.discarded == 0
    async with binding.bind(run_id="run-1", instance=_instance()):
        pass
    assert provider.entered == 1


async def test_state_callbacks_require_exact_effective_actions() -> None:
    provider = _Binding(
        "state",
        families=frozenset({"state"}),
        operations=EnvironmentProviderOperations(),
        permissions=frozenset({EnvironmentAction.STATE_EXPORT, EnvironmentAction.STATE_RESTORE}),
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-state",
                binding_revision=1,
                alias="state",
                permission_ceiling=EnvironmentPermissionSet(),
                default_working_directory=None,
                provider_binding=provider,
            ),
        ),
        default_binding_id=None,
    )
    binding = create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    state = EnvironmentState(
        observed_topology_version=1,
        bindings={
            "binding-state": EnvironmentBindingState(
                provider_type="test.provider",
                state_version="state-1",
                resource_compatibility="portable",
                data={},
            )
        },
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied_export:
            await environment.export_state()
        assert denied_export.value.code == "environment_denied"
        with pytest.raises(EnvironmentError) as denied_restore:
            await environment.restore_state(state)
        assert denied_restore.value.code == "environment_denied"
    assert provider.bound.export_calls == provider.bound.restore_calls == 0


async def test_state_provider_type_is_rejected_before_provider_readiness() -> None:
    provider = _Binding(
        "state-compatibility",
        families=frozenset({"state"}),
        operations=EnvironmentProviderOperations(),
        permissions=frozenset({EnvironmentAction.STATE_RESTORE}),
    )
    aggregate = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-state",
                    binding_revision=1,
                    alias="state",
                    permission_ceiling=EnvironmentPermissionSet(
                        operations=frozenset({EnvironmentAction.STATE_RESTORE})
                    ),
                    default_working_directory=None,
                    provider_binding=provider,
                ),
            ),
            default_binding_id=None,
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    incompatible = EnvironmentState(
        observed_topology_version=1,
        bindings={
            "binding-state": EnvironmentBindingState(
                provider_type="other.provider",
                state_version="state-1",
                resource_compatibility="portable",
                data={},
            )
        },
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as invalid:
            await environment.restore_state(incompatible)
        assert invalid.value.code == "state_invalid"
        assert provider.bound.ready_calls == []
        assert provider.bound.restore_calls == 0


async def test_dynamic_topology_add_replay_conflict_and_observer_chain() -> None:
    aggregate = create_noop_environment_run_binding(
        topology_version=0,
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=3),
    )
    provider = _Binding("dynamic")
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            _binding_request(
                provider,
                binding_id="binding-dynamic",
                revision=1,
                alias="dynamic",
            ),
        ),
        default_binding_id="binding-dynamic",
    )
    observer = None
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        observer = environment.topology_observer
        waiter = asyncio.create_task(observer.read(after_version=0, wait=True))
        await environment.activate()
        await aggregate.controller.wait_until_active()
        change = await aggregate.controller.apply(request)
        assert change.previous_version == 0
        assert change.current_version == 1
        assert [item.kind for item in change.bindings] == ["added"]
        assert environment.topology.default_binding_id == "binding-dynamic"
        assert (await waiter) == (change,)

        replay = EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                _binding_request(
                    None,
                    binding_id="binding-dynamic",
                    revision=1,
                    alias="dynamic",
                ),
            ),
            default_binding_id="binding-dynamic",
        )
        assert await aggregate.controller.apply(replay) == change

        conflict = EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                _binding_request(
                    None,
                    binding_id="binding-dynamic",
                    revision=1,
                    alias="other",
                ),
            ),
            default_binding_id="binding-dynamic",
        )
        with pytest.raises(EnvironmentError) as conflicting:
            await aggregate.controller.apply(conflict)
        assert conflicting.value.code == "topology_conflict"

        with pytest.raises(EnvironmentError) as stale:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(topology_version=0, bindings=(), default_binding_id=None)
            )
        assert stale.value.code == "topology_stale"
        with pytest.raises(EnvironmentError) as invalid_cursor:
            await observer.read(after_version=99)
        assert invalid_cursor.value.code == "environment_topology_cursor_invalid"

    assert observer is not None
    with pytest.raises(EnvironmentError) as closed:
        await observer.read(after_version=1, wait=True)
    assert closed.value.code == "environment_closed"
    assert provider.exited == 1


async def test_dynamic_topology_preserves_historical_alias_and_default_ownership() -> None:
    initial = _Binding("owner")
    initial_request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(_binding_request(initial, binding_id="binding-owner", revision=1, alias="owner"),),
        default_binding_id="binding-owner",
    )
    aggregate = create_environment_run_binding(
        initial_topology=initial_request,
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=5),
        state_limits=EnvironmentStateLimits(),
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        await aggregate.controller.apply(
            EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        )

        alias_thief = _Binding("thief-alias")
        with pytest.raises(EnvironmentError) as alias_error:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=3,
                    bindings=(_binding_request(alias_thief, binding_id="binding-thief", revision=1, alias="owner"),),
                    default_binding_id=None,
                )
            )
        assert alias_error.value.code == "environment_topology_invalid"
        assert alias_thief.discarded == 1

        default_thief = _Binding("thief-default")
        with pytest.raises(EnvironmentError) as default_error:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=3,
                    bindings=(
                        _binding_request(
                            default_thief,
                            binding_id="binding-thief",
                            revision=1,
                            alias="thief",
                        ),
                    ),
                    default_binding_id="binding-thief",
                )
            )
        assert default_error.value.code == "environment_topology_invalid"
        assert default_thief.discarded == 1

        refreshed = _Binding("owner")
        restored = await aggregate.controller.apply(
            EnvironmentTopologyRequest(
                topology_version=3,
                bindings=(_binding_request(refreshed, binding_id="binding-owner", revision=2, alias="owner"),),
                default_binding_id="binding-owner",
            )
        )
        assert restored.bindings[0].kind == "added"
        assert environment.topology.bindings[0].binding_revision == 2


async def test_dynamic_prepare_failure_is_atomic_and_cleans_every_candidate() -> None:
    initial = _Binding("initial")
    aggregate = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(_binding_request(initial, binding_id="binding-initial", revision=1, alias="initial"),),
            default_binding_id="binding-initial",
        ),
        topology_limits=EnvironmentTopologyLimits(max_bindings=4, max_committed_changes=4),
        state_limits=EnvironmentStateLimits(),
    )
    valid = _Binding("valid")
    invalid = _Binding("invalid", operations=EnvironmentProviderOperations())
    later = _Binding("later")
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        with pytest.raises(EnvironmentError) as failed:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=2,
                    bindings=(
                        _binding_request(None, binding_id="binding-initial", revision=1, alias="initial"),
                        _binding_request(valid, binding_id="binding-valid", revision=1, alias="valid"),
                        _binding_request(invalid, binding_id="binding-invalid", revision=1, alias="invalid"),
                        _binding_request(later, binding_id="binding-later", revision=1, alias="later"),
                    ),
                    default_binding_id="binding-initial",
                )
            )
        assert failed.value.code == "environment_provider_failure"
        assert environment.topology.topology_version == 1
        assert [item.binding_id for item in environment.topology.bindings] == ["binding-initial"]
    assert valid.entered == valid.exited == 1
    assert invalid.entered == invalid.exited == 1
    assert later.entered == 0
    assert later.discarded == 1


async def test_removed_scope_retires_only_after_accepted_revision_operation_drains() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingFiles:
        async def stat(self, path: str) -> FileMetadata:
            started.set()
            await release.wait()
            return FileMetadata(path=path, kind="file", size=1, writable=True)

    provider = _Binding("retire", operations=EnvironmentProviderOperations(files=BlockingFiles()))
    aggregate = create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(_binding_request(provider, binding_id="binding-retire", revision=1, alias="retire"),),
            default_binding_id="binding-retire",
        ),
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        operation_done = asyncio.Event()
        finish_caller = asyncio.Event()

        async def operation_caller() -> FileMetadata:
            result = await environment.files.stat("/workspace/value")
            operation_done.set()
            await finish_caller.wait()
            return result

        operation = asyncio.create_task(operation_caller())
        await started.wait()
        change = await aggregate.controller.apply(
            EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        )
        assert change.bindings[0].kind == "removed"
        assert provider.exited == 0
        release.set()
        await operation_done.wait()
        for _ in range(10):
            if provider.exited:
                break
            await asyncio.sleep(0)
        assert provider.exited == 1
        assert not operation.done()
        finish_caller.set()
        assert (await operation).path == "/workspace/value"


async def test_cancelled_queued_apply_discards_its_transferred_candidates() -> None:
    entry_started = asyncio.Event()
    release_entry = asyncio.Event()

    class SlowBinding(_Binding):
        @asynccontextmanager
        async def bind(self, **kwargs: Any) -> AsyncGenerator[Any]:
            del kwargs
            self.entered += 1
            entry_started.set()
            await release_entry.wait()
            try:
                yield self.bound
            finally:
                self.exited += 1

    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=3)
    )
    slow = SlowBinding("slow")
    queued = _Binding("queued")
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        first = asyncio.create_task(
            aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=1,
                    bindings=(_binding_request(slow, binding_id="binding-slow", revision=1, alias="slow"),),
                    default_binding_id="binding-slow",
                )
            )
        )
        await entry_started.wait()
        second = asyncio.create_task(
            aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=2,
                    bindings=(_binding_request(queued, binding_id="binding-queued", revision=1, alias="queued"),),
                    default_binding_id="binding-queued",
                )
            )
        )
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert queued.entered == 0
        assert queued.discarded == 1
        release_entry.set()
        assert (await first).current_version == 1


async def test_controller_is_terminal_after_aggregate_close() -> None:
    aggregate = create_noop_environment_run_binding()
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
    with pytest.raises(EnvironmentError) as closed:
        await aggregate.controller.apply(
            EnvironmentTopologyRequest(topology_version=1, bindings=(), default_binding_id=None)
        )
    assert closed.value.code == "environment_closed"


async def test_cleanup_timeout_supervises_provider_exit_after_operation_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment_coordinator, "DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS", 0.01)
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornFiles:
        async def stat(self, path: str) -> FileMetadata:
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return FileMetadata(path=path, kind="file", size=1, writable=True)

    provider = _Binding("stubborn", operations=EnvironmentProviderOperations(files=StubbornFiles()))
    aggregate = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    scope = aggregate.bind(run_id="run-1", instance=_instance())
    environment = await scope.__aenter__()
    await environment.activate()
    operation = asyncio.create_task(environment.files.stat("/workspace/value"))
    await started.wait()

    with pytest.raises(BaseExceptionGroup):
        await asyncio.wait_for(scope.__aexit__(None, None, None), timeout=0.2)
    assert provider.exited == 0

    release.set()
    assert (await operation).path == "/workspace/value"
    await asyncio.wait_for(provider.exit_event.wait(), timeout=0.2)
    assert provider.exited == 1


async def test_readiness_timeout_cancels_an_unowned_worker_and_releases_its_revision() -> None:
    readiness_started = asyncio.Event()
    readiness_cancelled = asyncio.Event()

    async def ensure_ready(operations: frozenset[str]) -> None:
        del operations
        readiness_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            readiness_cancelled.set()
            raise

    provider = _Binding("readiness-timeout")
    provider.bound.ensure_ready = ensure_ready  # type: ignore[method-assign]
    aggregate = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        operation = asyncio.create_task(
            environment.ensure_ready(
                EnvironmentReadinessRequirement(
                    operations=frozenset({"files"}),
                    timeout_seconds=0.05,
                )
            )
        )
        await readiness_started.wait()
        await aggregate.controller.apply(
            EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        )

        with pytest.raises(EnvironmentError) as timed_out:
            await operation
        assert timed_out.value.code == "environment_timeout"
        await asyncio.wait_for(readiness_cancelled.wait(), timeout=0.5)
        await asyncio.wait_for(provider.exit_event.wait(), timeout=0.5)
        assert provider.exited == 1


async def test_readiness_timeout_preserves_a_worker_owned_by_another_waiter() -> None:
    readiness_started = asyncio.Event()
    release_readiness = asyncio.Event()

    provider = _Binding("shared-readiness")

    async def ensure_ready(operations: frozenset[str]) -> None:
        readiness_started.set()
        await release_readiness.wait()
        provider.bound.availability = EnvironmentAvailability(
            status="available",
            ready_families=operations,
        )

    provider.bound.ensure_ready = ensure_ready  # type: ignore[method-assign]
    aggregate = create_environment_run_binding(
        initial_topology=_request(provider),
        topology_limits=EnvironmentTopologyLimits(max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        short_waiter = asyncio.create_task(
            environment.ensure_ready(
                EnvironmentReadinessRequirement(
                    operations=frozenset({"files"}),
                    timeout_seconds=0.1,
                )
            )
        )
        await readiness_started.wait()
        long_waiter = asyncio.create_task(
            environment.ensure_ready(
                EnvironmentReadinessRequirement(
                    operations=frozenset({"files"}),
                    timeout_seconds=1,
                )
            )
        )
        for _ in range(20):
            if sum(environment._readiness_waiters.values()) == 2:
                break
            await asyncio.sleep(0)
        assert sum(environment._readiness_waiters.values()) == 2

        await aggregate.controller.apply(
            EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        )
        with pytest.raises(EnvironmentError) as timed_out:
            await short_waiter
        assert timed_out.value.code == "environment_timeout"
        assert not long_waiter.done()
        assert provider.exited == 0

        release_readiness.set()
        await long_waiter
        await asyncio.wait_for(provider.exit_event.wait(), timeout=0.5)
        assert provider.exited == 1


async def test_reused_candidate_rejection_discards_fresh_peers_without_identity_history() -> None:
    aggregate = create_noop_environment_run_binding(
        topology_limits=EnvironmentTopologyLimits(max_bindings=2, max_committed_changes=2)
    )
    active = _Binding("active")
    fresh = _Binding("fresh")
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        await aggregate.controller.apply(
            EnvironmentTopologyRequest(
                topology_version=1,
                bindings=(_binding_request(active, binding_id="binding-active", revision=1, alias="active"),),
                default_binding_id="binding-active",
            )
        )
        with pytest.raises(EnvironmentError) as reused:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=2,
                    bindings=(
                        _binding_request(active, binding_id="binding-active", revision=1, alias="active"),
                        _binding_request(fresh, binding_id="binding-fresh", revision=1, alias="fresh"),
                    ),
                    default_binding_id="binding-active",
                )
            )
        assert reused.value.code == "environment_binding_reused"
        assert fresh.entered == 0
        assert fresh.discarded == 1
        assert not hasattr(environment, "_transferred_candidates")


async def test_candidate_discard_deadline_is_hard_and_does_not_block_later_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(environment_coordinator, "DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()
    finished = asyncio.Event()

    class StubbornDiscardBinding(_Binding):
        async def discard(self) -> None:
            self.discarded += 1
            try:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
            finally:
                finished.set()

    stubborn = StubbornDiscardBinding("stubborn-discard")
    later = _Binding("later-discard")
    aggregate = create_noop_environment_run_binding()
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        with pytest.raises(BaseExceptionGroup):
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=1,
                    bindings=(
                        _binding_request(stubborn, binding_id="binding-a", revision=1, alias="duplicate"),
                        _binding_request(later, binding_id="binding-b", revision=1, alias="duplicate"),
                    ),
                    default_binding_id="binding-a",
                )
            )
        assert stubborn.discarded == 1
        assert later.discarded == 1
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.2)


async def test_provider_bound_artifacts_must_match_selected_revision() -> None:
    class WrongReceiptFiles:
        async def write_text(self, path: str, text: str, **kwargs: Any) -> FileWriteResult:
            del text, kwargs
            return FileWriteResult(
                path=path,
                bytes_written=1,
                receipt=EnvironmentOperationReceipt(
                    binding_id="another-binding",
                    binding_revision=99,
                    observed_generation="another-generation",
                    operation_id="operation-1",
                    stage="completed",
                    outcome="succeeded",
                ),
            )

    provider = _Binding(
        "wrong-artifact",
        operations=EnvironmentProviderOperations(files=WrongReceiptFiles()),
        permissions=frozenset({EnvironmentAction.FILE_WRITE_TEXT}),
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="workspace",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_WRITE_TEXT})),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    aggregate = create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        with pytest.raises(EnvironmentError) as invalid:
            await environment.files.write_text("value", "x", mode="upsert")
        assert invalid.value.code == "environment_provider_failure"


async def test_dynamic_validation_and_scope_cleanup_errors_are_both_preserved() -> None:
    class CleanupFailureBinding(_Binding):
        @asynccontextmanager
        async def bind(self, **kwargs: Any) -> AsyncGenerator[Any]:
            del kwargs
            self.entered += 1
            try:
                yield self.bound
            finally:
                self.exited += 1
                self.exit_event.set()
                raise RuntimeError("scope cleanup failed")

    candidate = CleanupFailureBinding("invalid", operations=EnvironmentProviderOperations())
    aggregate = create_noop_environment_run_binding()
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        with pytest.raises(BaseExceptionGroup) as raised:
            await aggregate.controller.apply(
                EnvironmentTopologyRequest(
                    topology_version=1,
                    bindings=(_binding_request(candidate, binding_id="binding-1", revision=1, alias="invalid"),),
                    default_binding_id="binding-1",
                )
            )

    def flatten(error: BaseException) -> list[BaseException]:
        if isinstance(error, BaseExceptionGroup):
            return [nested for child in error.exceptions for nested in flatten(child)]
        return [error]

    failures = flatten(raised.value)
    assert any(isinstance(error, EnvironmentError) for error in failures)
    assert any(isinstance(error, RuntimeError) and str(error) == "scope cleanup failed" for error in failures)


class _IdempotentProcessOperations:
    def __init__(self, handles: tuple[BoundProcessHandle, ...]) -> None:
        self._handles = handles
        self._next = 0
        self.inspect_result = handles[0]

    @staticmethod
    def _receipt() -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            binding_id="binding-1",
            binding_revision=1,
            observed_generation="generation-processes",
            operation_id="operation-1",
            stage="completed",
            outcome="succeeded",
        )

    @staticmethod
    def _info(handle: BoundProcessHandle) -> ProcessInfo:
        empty = EnvironmentOutputCapture(
            kind="empty",
            producer_complete=True,
            content_complete=True,
            produced_bytes=0,
            captured_bytes=0,
            dropped_bytes=0,
        )
        return ProcessInfo(
            handle=handle,
            status=ProcessStatus(phase="running"),
            stdin_open=False,
            output=ProcessOutputSnapshot(stdout=empty, stderr=empty),
        )

    async def start(self, request: CommandRequest) -> ProcessStartResult:
        del request
        handle = self._handles[self._next]
        self._next += 1
        return ProcessStartResult(process=self._info(handle), receipt=self._receipt())

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        del handle
        return self._info(self.inspect_result)

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        del handle
        return self._receipt()


def _process_test_binding() -> tuple[Any, _IdempotentProcessOperations, tuple[BoundProcessHandle, ...]]:
    handles = tuple(
        BoundProcessHandle(
            binding_id="binding-1",
            binding_revision=1,
            observed_generation="generation-processes",
            handle=OpaqueProcessHandle._from_payload(f"process-{index}"),
        )
        for index in (1, 2)
    )
    operations = _IdempotentProcessOperations(handles)
    provider = _Binding(
        "processes",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=operations),
        permissions=frozenset(
            {
                EnvironmentAction.PROCESS_START,
                EnvironmentAction.PROCESS_INSPECT,
                EnvironmentAction.PROCESS_RELEASE,
            }
        ),
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="processes",
                permission_ceiling=EnvironmentPermissionSet(
                    operations=frozenset(
                        {
                            EnvironmentAction.PROCESS_START,
                            EnvironmentAction.PROCESS_INSPECT,
                            EnvironmentAction.PROCESS_RELEASE,
                        }
                    )
                ),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    aggregate = create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(max_committed_changes=2),
        state_limits=EnvironmentStateLimits(),
    )
    return aggregate, operations, handles


def _process_request() -> CommandRequest:
    return CommandRequest(
        command=ArgvCommand(executable="fake-process"),
        output_policy=EnvironmentOutputPolicy(
            max_inline_bytes=64,
            max_output_bytes=64,
            overflow="truncate",
        ),
    )


async def test_duplicate_idempotent_process_release_does_not_underflow_revision_fence() -> None:
    aggregate, _, _ = _process_test_binding()
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        first = await environment.processes.start(_process_request())
        second = await environment.processes.start(_process_request())
        await environment.processes.release(first.process.handle)
        await environment.processes.release(first.process.handle)

        removal = EnvironmentTopologyRequest(topology_version=2, bindings=(), default_binding_id=None)
        with pytest.raises(EnvironmentError) as in_use:
            await aggregate.controller.apply(removal)
        assert in_use.value.code == "topology_in_use"

        await environment.processes.release(second.process.handle)
        assert (await aggregate.controller.apply(removal)).current_version == 2


async def test_process_result_cannot_substitute_another_same_revision_handle() -> None:
    aggregate, operations, handles = _process_test_binding()
    async with aggregate.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.activate()
        first = await environment.processes.start(_process_request())
        await environment.processes.start(_process_request())
        operations.inspect_result = handles[1]

        with pytest.raises(EnvironmentError) as retargeted:
            await environment.processes.inspect(first.process.handle)
        assert retargeted.value.code == "environment_provider_failure"
