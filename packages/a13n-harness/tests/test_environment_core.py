from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import a13n_harness.environment.coordinator as environment_coordinator
import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
)
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
    EnvironmentRunCallbacks,
    EnvironmentRunExtensionContext,
    FileEntriesResult,
    FileMetadata,
    FileWriteResult,
)
from a13n_harness.environment.advanced import (
    NoopBoundEnvironment,
    create_empty_environment_runtime,
    create_environment_runtime,
)
from a13n_harness.environment.providers import (
    EnvironmentProviderBinding,
    EnvironmentRuntimeMount,
)
from a13n_harness.providers.environment.commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandRequest,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessStartResult,
    ProcessStatus,
)
from a13n_harness.providers.environment.models import EnvironmentOperationReceipt, EnvironmentState
from a13n_harness.providers.environment.operations import EnvironmentOperations as EnvironmentProviderOperations
from a13n_harness.providers.environment.retention import (
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
    provider_key: str
    environment_id: str
    descriptor: EnvironmentDescriptor
    operations: EnvironmentProviderOperations
    availability: EnvironmentAvailability
    ready_calls: list[frozenset[str]] = field(default_factory=list)
    cached_state: EnvironmentState | None = None

    async def ensure_ready(self, operations: frozenset[str]) -> None:
        self.ready_calls.append(operations)
        self.availability = EnvironmentAvailability(
            status="available",
            ready_families=self.availability.ready_families | operations,
        )

    def dump_state(self) -> EnvironmentState | None:
        return self.cached_state


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
        self.mount_id: str | None = None
        self.exit_event = asyncio.Event()
        self.discarded = 0
        selected_permissions = permissions
        if selected_permissions is None:
            selected_permissions = frozenset({EnvironmentAction.FILE_STAT}) if "files" in families else frozenset()
        self.bound = _BoundProvider(
            provider_key="test_provider",
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
        return "test_provider"

    @property
    def environment_id(self) -> str:
        return f"environment:{self.name}"

    @asynccontextmanager
    async def bind(self, **kwargs: Any) -> AsyncGenerator[Any]:
        self.mount_id = kwargs["mount_id"]
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
        self._events.append(f"enter:{self.extension_id}")
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


def _runtime_mount(
    provider: _Binding,
    *,
    working_directory: str | None = "/",
    mount_path: str | None = None,
    permissions: frozenset[EnvironmentAction] = frozenset({EnvironmentAction.FILE_STAT}),
) -> EnvironmentRuntimeMount:
    return EnvironmentRuntimeMount(
        binding=provider,
        permission_ceiling=EnvironmentPermissionSet(operations=permissions),
        working_directory=working_directory,
        mount_path=mount_path,
    )


def _request(*bindings: _Binding) -> dict[str, EnvironmentRuntimeMount]:
    return {f"workspace-{index}": _runtime_mount(binding) for index, binding in enumerate(bindings, start=1)}


async def test_noop_uses_complete_aggregate_and_is_single_use() -> None:
    binding = create_empty_environment_runtime()
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        assert isinstance(environment, NoopBoundEnvironment)
        assert environment.snapshot.mounts == ()
        assert environment.dump_states() == {}
        await binding._activate()
        await binding.wait_until_active()
        with pytest.raises(EnvironmentError) as unavailable:
            await environment.files.stat("/workspace/missing")
        assert unavailable.value.code == "environment_selection_invalid"
    with pytest.raises(EnvironmentError) as reused:
        async with binding.bind(thread_id="thread-1", run_id="run-2", instance=_instance(), host_refs={}):
            pass
    assert reused.value.code == "environment_runtime_reused"


async def test_static_aggregate_intersects_permissions_and_scopes_readiness() -> None:
    first = _Binding("first")
    second = _Binding("second")
    binding = create_environment_runtime(
        mounts=_request(first, second),
        default_mount="workspace-1",
    )
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        assert [item.name for item in environment.snapshot.mounts] == ["workspace-1", "workspace-2"]
        await environment.ensure_ready(
            EnvironmentReadinessRequirement(
                operations=frozenset({"files"}),
                mounts=frozenset({"workspace-2"}),
            )
        )
        assert first.bound.ready_calls == []
        assert second.bound.ready_calls == [frozenset({"files"})]
        assert (await environment.describe("workspace-2")).availability.status == "available"
    assert first.exited == second.exited == 1
    assert first.discarded == second.discarded == 0


async def test_environment_run_extensions_follow_aggregate_lifecycle_and_remain_open_on_exit() -> None:
    events: list[str] = []
    provider = _Binding("extensions")
    first = _RunExtension("first", events, provider=provider)
    second = _RunExtension("second", events)
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
        extensions=(first, second),
    )

    added = _Binding("added")
    async with binding.bind(
        thread_id="thread-1", run_id="run-extensions", instance=_instance(), host_refs={}
    ) as environment:
        await binding._activate()
        assert events == ["enter:first", "enter:second"]
        change = await binding.mount("added", _runtime_mount(added))
        assert change.kind == "mounted"
        assert [item.name for item in environment.snapshot.mounts] == ["workspace-1", "added"]
        assert events == ["enter:first", "enter:second"]

    assert events == [
        "enter:first",
        "enter:second",
        "exit:second",
        "open:/workspace/extension.txt:0",
        "exit:first",
    ]
    assert provider.exited == 1


async def test_environment_run_callbacks_follow_the_extension_lifecycle() -> None:
    events: list[str] = []
    provider = _Binding("callbacks")

    async def enter_first(context: EnvironmentRunExtensionContext) -> None:
        events.append(f"enter:first:{context.run_id}")

    async def exit_first(context: EnvironmentRunExtensionContext) -> None:
        observation = await context.environment.files.stat("/workspace/callback.txt")
        events.append(f"exit:first:{observation.path}:{provider.exited}")

    async def enter_second(context: EnvironmentRunExtensionContext) -> None:
        events.append(f"enter:second:{context.run_id}")

    async def exit_second(context: EnvironmentRunExtensionContext) -> None:
        events.append(f"exit:second:{context.run_id}")

    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
        extensions=(
            EnvironmentRunCallbacks(
                extension_id="first",
                on_enter=enter_first,
                on_exit=exit_first,
            ),
            EnvironmentRunCallbacks(
                extension_id="second",
                on_enter=enter_second,
                on_exit=exit_second,
            ),
        ),
    )

    async with binding.bind(thread_id="thread-1", run_id="run-callbacks", instance=_instance(), host_refs={}):
        await binding._activate()
        assert events == [
            "enter:first:run-callbacks",
            "enter:second:run-callbacks",
        ]

    assert events == [
        "enter:first:run-callbacks",
        "enter:second:run-callbacks",
        "exit:second:run-callbacks",
        "exit:first:/workspace/callback.txt:0",
    ]
    assert provider.exited == 1


async def test_environment_run_callback_entry_failure_only_unwinds_admitted_callbacks() -> None:
    events: list[str] = []

    async def enter_first(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("enter:first")

    async def exit_first(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("exit:first")

    async def enter_failing(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("enter:failing")
        raise RuntimeError("secret callback entry failure")

    async def exit_failing(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("exit:failing")

    binding = create_environment_runtime(
        mounts=_request(),
        extensions=(
            EnvironmentRunCallbacks(
                extension_id="first",
                on_enter=enter_first,
                on_exit=exit_first,
            ),
            EnvironmentRunCallbacks(
                extension_id="failing",
                on_enter=enter_failing,
                on_exit=exit_failing,
            ),
        ),
    )

    async with binding.bind(
        thread_id="thread-1", run_id="run-callback-entry-failure", instance=_instance(), host_refs={}
    ):
        with pytest.raises(EnvironmentError) as exc_info:
            await binding._activate()

    assert exc_info.value.code == "environment_extension_bind_failed"
    assert events == ["enter:first", "enter:failing", "exit:first"]


async def test_environment_run_callback_cleanup_continues_after_failure() -> None:
    events: list[str] = []

    async def exit_first(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("exit:first")

    async def exit_failing(context: EnvironmentRunExtensionContext) -> None:
        del context
        events.append("exit:failing")
        raise RuntimeError("secret callback exit failure")

    binding = create_environment_runtime(
        mounts=_request(),
        extensions=(
            EnvironmentRunCallbacks(extension_id="first", on_exit=exit_first),
            EnvironmentRunCallbacks(extension_id="failing", on_exit=exit_failing),
        ),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        async with binding.bind(
            thread_id="thread-1", run_id="run-callback-exit-failure", instance=_instance(), host_refs={}
        ):
            await binding._activate()

    assert "Environment entered-resource cleanup failed" in str(exc_info.value)
    assert events == ["exit:failing", "exit:first"]


def test_environment_run_callbacks_require_at_least_one_callback() -> None:
    with pytest.raises(ValueError, match="requires on_enter or on_exit"):
        EnvironmentRunCallbacks(extension_id="empty")


async def test_environment_run_extension_entry_failure_unwinds_and_keeps_runtime_inactive() -> None:
    events: list[str] = []
    provider = _Binding("extension-entry-failure")
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
        extensions=(
            _RunExtension("first", events),
            _RunExtension("failing", events, fail_entry=True),
        ),
    )

    async with binding.bind(thread_id="thread-1", run_id="run-extension-failure", instance=_instance(), host_refs={}):
        with pytest.raises(EnvironmentError) as exc_info:
            await binding._activate()
        assert exc_info.value.code == "environment_extension_bind_failed"
        assert events == ["enter:first", "enter:failing", "exit:first"]
        with pytest.raises(EnvironmentError) as repeated:
            await binding._activate()
        assert repeated.value.code == "environment_extension_bind_failed"
        with pytest.raises(EnvironmentError) as inactive:
            await binding.wait_until_active()
        assert inactive.value.code == "environment_activation_failed"

    assert provider.exited == 1


async def test_environment_run_extension_cleanup_continues_after_failure() -> None:
    events: list[str] = []
    provider = _Binding("extension-exit-failure")
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
        extensions=(
            _RunExtension("first", events),
            _RunExtension("failing", events, fail_exit=True),
        ),
    )

    with pytest.raises(BaseExceptionGroup) as exc_info:
        async with binding.bind(
            thread_id="thread-1", run_id="run-extension-cleanup", instance=_instance(), host_refs={}
        ):
            await binding._activate()

    assert "Environment entered-resource cleanup failed" in str(exc_info.value)
    assert events == [
        "enter:first",
        "enter:failing",
        "exit:failing",
        "exit:first",
    ]
    assert provider.exited == 1


@pytest.mark.parametrize("extension_id", ["", " spaced", "x" * 201])
def test_environment_run_extension_ids_are_validated(extension_id: str) -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        create_environment_runtime(
            mounts=_request(),
            extensions=(_RunExtension(extension_id, []),),
        )

    assert exc_info.value.code == "environment_extension_id_invalid"


def test_environment_run_extension_ids_must_be_unique() -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        create_environment_runtime(
            mounts=_request(),
            extensions=(_RunExtension("same", []), _RunExtension("same", [])),
        )

    assert exc_info.value.code == "environment_extension_duplicate"


async def test_readiness_is_reacquired_after_live_availability_regresses() -> None:
    provider = _Binding("readiness-regression")
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    requirement = EnvironmentReadinessRequirement(operations=frozenset({"files"}))
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    invalid.bound.availability = EnvironmentAvailability(status="available", ready_families=frozenset({"files"}))
    never_entered = _Binding("later")
    binding = create_environment_runtime(
        mounts=_request(invalid, never_entered),
        default_mount="workspace-1",
    )
    with pytest.raises(EnvironmentError) as failure:
        async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}):
            pass
    assert failure.value.code == "environment_provider_failure"
    assert invalid.entered == invalid.exited == 1
    assert never_entered.entered == 0
    assert never_entered.discarded == 1


@pytest.mark.parametrize("prepared_facets", ["valid", "missing_facet", "missing_method"])
async def test_lazy_mount_validates_materialized_operations_before_dispatch(
    prepared_facets: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _Binding("lazy", operations=EnvironmentProviderOperations())

    async def prepare(operations: frozenset[str]) -> None:
        provider.bound.ready_calls.append(operations)
        if prepared_facets == "valid":
            provider.bound.operations = EnvironmentProviderOperations(files=_Files())
        elif prepared_facets == "missing_method":
            provider.bound.operations = EnvironmentProviderOperations(files=cast(Any, object()))
        provider.bound.availability = EnvironmentAvailability(status="available", ready_families=operations)

    monkeypatch.setattr(provider.bound, "ensure_ready", prepare)
    runtime = create_environment_runtime(mounts=_request(provider), default_mount="workspace-1")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        assert provider.bound.ready_calls == []
        if prepared_facets == "valid":
            assert (await environment.files.stat("note.txt")).kind == "file"
        else:
            with pytest.raises(EnvironmentError) as failure:
                await environment.files.stat("note.txt")
            assert failure.value.code == "environment_provider_failure"
        assert provider.bound.ready_calls == [frozenset({"files"})]
    assert provider.entered == provider.exited == 1


async def test_partial_entry_failure_closes_entered_and_discards_remaining_candidates() -> None:
    entered = _Binding("entered")
    failed = _Binding("failed", fail_entry=True)
    later = _Binding("later")
    binding = create_environment_runtime(
        mounts=_request(entered, failed, later),
        default_mount="workspace-1",
    )
    with pytest.raises(RuntimeError, match="entry failed"):
        async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}):
            pass
    assert entered.exited == 1
    assert failed.discarded == later.discarded == 1


async def test_initial_reuse_never_discards_an_active_advanced_binding() -> None:
    active = _Binding("active")
    fresh = _Binding("fresh")
    runtime = create_environment_runtime(mounts=_request(active), default_mount="workspace-1")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as bound:
        await runtime._activate()
        conflicting = create_environment_runtime(mounts=_request(active, fresh))
        with pytest.raises(EnvironmentError) as reused:
            async with conflicting.bind(thread_id="thread-2", run_id="run-2", instance=_instance(), host_refs={}):
                pytest.fail("A transferred candidate cannot enter another runtime")
        assert reused.value.code == "environment_provider_binding_reused"
        assert active.entered == 1
        assert active.exited == active.discarded == 0
        assert fresh.entered == 0
        assert fresh.discarded == 1
        with pytest.raises(EnvironmentError) as discarded:
            await runtime.replace("workspace-1", _runtime_mount(fresh))
        assert discarded.value.code == "environment_provider_binding_reused"
        assert fresh.discarded == 1
        assert (await bound.files.stat("/workspace/value.txt")).kind == "file"
    assert active.exited == 1
    assert active.discarded == 0


async def test_invalid_mount_set_is_rejected_before_provider_entry() -> None:
    candidate = _Binding("one")
    mount = _runtime_mount(candidate)
    with pytest.raises(EnvironmentError) as exc_info:
        create_environment_runtime(
            mounts={"one": mount, "two": mount},
        )
    assert exc_info.value.code == "environment_request_invalid"
    assert candidate.entered == candidate.discarded == 0


async def test_direct_mount_paths_route_by_longest_prefix_without_legacy_aliases() -> None:
    project = _Binding("project")
    nested = _Binding("nested")
    runtime = create_environment_runtime(
        mounts={
            "workspace": _runtime_mount(project, mount_path="/Users/example/project"),
            "workspace-2": _runtime_mount(nested, mount_path="/Users/example/project/vendor"),
        },
        default_mount="workspace",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        root = environment.resolve_path("/Users/example/project/readme.md")
        child = environment.resolve_path("/Users/example/project/./vendor//package.toml")
        relative = environment.resolve_path("src/main.py")

        assert root.mount_id == project.mount_id
        assert root.path == "/readme.md"
        assert child.mount_id == nested.mount_id
        assert child.path == "/package.toml"
        assert relative.mount_id == project.mount_id
        assert relative.path == "/src/main.py"
        assert tuple(item.mount_path for item in environment.snapshot.mounts) == (
            "/Users/example/project",
            "/Users/example/project/vendor",
        )
        with pytest.raises(EnvironmentError) as legacy:
            environment.resolve_path("/workspace/readme.md")
        assert legacy.value.code == "environment_selection_invalid"
        with pytest.raises(EnvironmentError) as mismatch:
            environment.resolve_path("/Users/example/project/vendor/package.toml", alias="workspace")
        assert mismatch.value.code == "environment_selection_invalid"


@pytest.mark.parametrize(
    "root", ["/native/project", "C:/Users/Example", "//server/share/project", "/", "C:/", "//server/share/"]
)
@pytest.mark.parametrize("suffix", ["src/", "src//", "./src/./", "src//./"])
async def test_operation_paths_normalize_before_routing_and_scoped_io(root: str, suffix: str) -> None:
    received: list[str] = []

    class RecordingFiles(_Files):
        async def stat(self, path: str) -> FileMetadata:
            received.append(path)
            return await super().stat(path)

    provider = _Binding("project", operations=EnvironmentProviderOperations(files=RecordingFiles()))
    runtime = create_environment_runtime(
        mounts={"project": _runtime_mount(provider, mount_path=root)}, default_mount="project"
    )
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        absolute = f"{root.rstrip('/')}/{suffix}"
        selected = await environment.resolve_files(absolute)
        assert selected.resolved_path.path == "/src"
        assert environment.resolve_path(suffix) == selected.resolved_path
        assert environment.resolve_path(f"{root.rstrip('/')}/").path == "/"
        assert environment.resolve_path("./").path == "/"
        assert environment.resolve_path("./C:/src").path == "/C:/src"
        async with environment.open_files(selected) as files:
            assert (await files.stat(absolute)).path == absolute
            assert (await files.stat(suffix)).path == suffix
        assert (await environment.files.stat(absolute)).path == absolute
        assert received == ["/src", "/src", "/src"]


@pytest.mark.parametrize("path", ["", "bad\x00path", "../src", "/workspace/src/../other", "/workspace/./../other"])
async def test_invalid_operation_paths_have_actionable_errors(path: str) -> None:
    provider = _Binding("project")
    runtime = create_environment_runtime(mounts={"project": _runtime_mount(provider)}, default_mount="project")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        with pytest.raises(EnvironmentError) as invalid:
            environment.resolve_path(path)
        assert invalid.value.code == "environment_request_invalid"
        assert invalid.value.retry_hint == "request_change"
        assert invalid.value.details["field"] == "path"
        assert invalid.value.details["reason"] == "invalid_path"
        assert invalid.value.details["hint"]


async def test_default_change_rejects_a_new_legacy_route_conflict() -> None:
    legacy = _Binding("legacy")
    direct = _Binding("direct")
    runtime = create_environment_runtime(
        mounts={
            "legacy": _runtime_mount(legacy),
            "direct": _runtime_mount(direct, mount_path="/workspace"),
        },
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        await runtime._activate()
        before = environment.snapshot

        with pytest.raises(EnvironmentError) as conflict:
            await runtime.set_default("legacy")

        assert conflict.value.code == "environment_request_invalid"
        assert environment.snapshot == before
        selected = environment.resolve_path("/workspace/value.txt")
        assert selected.mount_id == direct.mount_id


async def test_direct_mount_paths_support_drive_and_unc_roots() -> None:
    drive = _Binding("drive")
    unc = _Binding("unc")
    runtime = create_environment_runtime(
        mounts={
            "drive": _runtime_mount(drive, mount_path="C:/Users/Example/Project"),
            "unc": _runtime_mount(unc, mount_path="//server/share/project"),
        },
        default_mount="drive",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        drive_path = environment.resolve_path("c:/users/example/project/src/main.py")
        unc_path = environment.resolve_path("//SERVER/SHARE/project/readme.md")

        assert drive_path.mount_id == drive.mount_id
        assert drive_path.path == "/src/main.py"
        assert unc_path.mount_id == unc.mount_id
        assert unc_path.path == "/readme.md"


async def test_direct_file_results_return_reusable_mount_paths() -> None:
    class ListingFiles(_Files):
        async def list(self, path: str, **kwargs: Any) -> FileEntriesResult:
            del kwargs
            return FileEntriesResult(
                entries=(
                    FileMetadata(
                        path=f"{path.rstrip('/')}/child.txt",
                        kind="file",
                        size=1,
                        writable=True,
                    ),
                ),
                offset=0,
                has_more=False,
            )

    provider = _Binding(
        "listing",
        operations=EnvironmentProviderOperations(files=ListingFiles()),
        permissions=frozenset({EnvironmentAction.FILE_LIST, EnvironmentAction.FILE_STAT}),
    )
    runtime = create_environment_runtime(
        mounts={
            "workspace": _runtime_mount(
                provider,
                mount_path="/Users/example/project",
                permissions=frozenset({EnvironmentAction.FILE_LIST, EnvironmentAction.FILE_STAT}),
            )
        },
        default_mount="workspace",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        result = await environment.files.list("/Users/example/project/src")
        returned = result.entries[0].path

        assert returned == "/Users/example/project/src/child.txt"
        selected = environment.resolve_path(returned)
        assert selected.mount_id == provider.mount_id
        assert selected.path == "/src/child.txt"


async def test_equivalent_direct_mount_paths_fail_before_provider_entry() -> None:
    first = _Binding("first")
    second = _Binding("second")

    with pytest.raises(EnvironmentError) as invalid:
        create_environment_runtime(
            mounts={
                "first": _runtime_mount(first, mount_path="C:/Users/Example/Project"),
                "second": _runtime_mount(second, mount_path="c:/users/example/project"),
            },
            default_mount="first",
        )

    assert invalid.value.code == "environment_request_invalid"
    assert first.entered == second.entered == 0


async def test_dynamic_mount_path_conflict_does_not_consume_candidate() -> None:
    initial = _Binding("initial")
    candidate = _Binding("candidate")
    runtime = create_environment_runtime(
        mounts={"initial": _runtime_mount(initial, mount_path="/project")},
        default_mount="initial",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        await runtime._activate()
        before = environment.snapshot
        with pytest.raises(EnvironmentError) as conflict:
            await runtime.mount("candidate", _runtime_mount(candidate, mount_path="/project"))
        assert conflict.value.code == "environment_request_invalid"
        assert environment.snapshot == before

    assert candidate.entered == candidate.discarded == 0


async def test_closed_environment_rejects_new_provider_work() -> None:
    provider = _Binding("closed")
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    task: asyncio.Task[Any]
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
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
    binding = create_environment_runtime(
        mounts=_request(first, second),
        default_mount="workspace-1",
    )
    with pytest.raises(BaseExceptionGroup):
        async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}):
            pass
    assert reached == ["second-exit", "first-exit"]


async def test_invalid_identity_does_not_transfer_or_consume_binding() -> None:
    provider = _Binding("identity")
    binding = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    with pytest.raises(EnvironmentError):
        async with binding.bind(thread_id="thread-1", run_id="", instance=_instance(), host_refs={}):
            pass
    assert provider.entered == provider.discarded == 0
    async with binding.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}):
        pass
    assert provider.entered == 1


async def test_dump_states_returns_provider_cached_state_by_mount_name() -> None:
    provider = _Binding("stateful")
    state = EnvironmentState(
        provider_key="test_provider",
        state_version="state-1",
        state={"target": "environment:stateful"},
    )
    provider.bound.cached_state = state
    runtime = create_environment_runtime(
        mounts={"workspace": _runtime_mount(provider)},
        default_mount="workspace",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        assert environment.dump_states() == {"workspace": state}


async def test_dump_states_rejects_incompatible_provider_state() -> None:
    provider = _Binding("incompatible-state")
    provider.bound.cached_state = EnvironmentState(
        provider_key="other_provider",
        state_version="state-1",
        state={},
    )
    runtime = create_environment_runtime(
        mounts={"workspace": _runtime_mount(provider)},
        default_mount="workspace",
    )

    async with runtime.bind(
        thread_id="thread-1",
        run_id="run-1",
        instance=_instance(),
        host_refs={},
    ) as environment:
        with pytest.raises(EnvironmentError) as invalid:
            environment.dump_states()

    assert invalid.value.code == "state_invalid"


async def test_mount_and_default_change_are_atomic_and_sequenced() -> None:
    aggregate = create_empty_environment_runtime()
    provider = _Binding("dynamic")
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        waiter = asyncio.create_task(environment._read_changes(after_sequence=0, wait=True))

        change = await aggregate.mount(
            "dynamic",
            _runtime_mount(provider),
            make_default=True,
        )

        assert change.sequence == 1
        assert change.kind == "mounted"
        assert change.name == "dynamic"
        assert change.previous_default is None
        assert change.current_default == "dynamic"
        assert environment.snapshot.default_mount == "dynamic"
        assert [item.name for item in environment.snapshot.mounts] == ["dynamic"]
        assert await waiter == (change,)

        cleared = await aggregate.set_default(None)
        assert cleared.sequence == 2
        assert cleared.kind == "default_changed"
        assert cleared.previous_default == "dynamic"
        assert cleared.current_default is None
        assert environment.snapshot.default_mount is None


async def test_replace_preserves_default_and_changes_mount_incarnation() -> None:
    initial = _Binding("initial")
    aggregate = create_environment_runtime(
        mounts={"workspace": _runtime_mount(initial)},
        default_mount="workspace",
    )
    replacement = _Binding("replacement")
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        before = environment.resolve_path("/workspace/value")

        change = await aggregate.replace("workspace", _runtime_mount(replacement))
        after = environment.resolve_path("/workspace/value")

        assert change.sequence == 1
        assert change.kind == "replaced"
        assert change.previous_default == change.current_default == "workspace"
        assert environment.snapshot.default_mount == "workspace"
        assert before.mount_id != after.mount_id
        assert environment.snapshot.mounts[0].descriptor.generation == "generation-replacement"
        await asyncio.wait_for(initial.exit_event.wait(), timeout=0.5)


async def test_unmounting_the_default_clears_it_atomically() -> None:
    first = _Binding("first")
    second = _Binding("second")
    aggregate = create_environment_runtime(
        mounts={
            "first": _runtime_mount(first),
            "second": _runtime_mount(second),
        },
        default_mount="first",
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()

        change = await aggregate.unmount("first")

        assert change.sequence == 1
        assert change.kind == "unmounted"
        assert change.name == "first"
        assert change.previous_default == "first"
        assert change.current_default is None
        assert environment.snapshot.default_mount is None
        assert [item.name for item in environment.snapshot.mounts] == ["second"]
        await asyncio.wait_for(first.exit_event.wait(), timeout=0.5)


async def test_dynamic_prepare_failure_is_atomic_and_cleans_candidate() -> None:
    initial = _Binding("initial")
    aggregate = create_environment_runtime(
        mounts={"initial": _runtime_mount(initial)},
        default_mount="initial",
    )
    invalid = _Binding("invalid", operations=EnvironmentProviderOperations())
    invalid.bound.availability = EnvironmentAvailability(status="available", ready_families=frozenset({"files"}))
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        before = environment.snapshot

        with pytest.raises(EnvironmentError) as failed:
            await aggregate.mount("invalid", _runtime_mount(invalid))

        assert failed.value.code == "environment_provider_failure"
        assert environment.snapshot == before
        assert environment._change_sequence == 0
    assert invalid.entered == invalid.exited == 1


async def test_unmounted_scope_retires_after_accepted_operation_drains() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingFiles:
        async def stat(self, path: str) -> FileMetadata:
            started.set()
            await release.wait()
            return FileMetadata(path=path, kind="file", size=1, writable=True)

    provider = _Binding("retire", operations=EnvironmentProviderOperations(files=BlockingFiles()))
    aggregate = create_environment_runtime(
        mounts={"retire": _runtime_mount(provider)},
        default_mount="retire",
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        operation_done = asyncio.Event()
        finish_caller = asyncio.Event()

        async def operation_caller() -> FileMetadata:
            result = await environment.files.stat("/workspace/value")
            operation_done.set()
            await finish_caller.wait()
            return result

        operation = asyncio.create_task(operation_caller())
        await started.wait()
        change = await aggregate.unmount("retire")
        assert change.kind == "unmounted"
        assert provider.exited == 0

        release.set()
        await operation_done.wait()
        await asyncio.wait_for(provider.exit_event.wait(), timeout=0.5)
        assert not operation.done()
        finish_caller.set()
        assert (await operation).path == "/workspace/value"


async def test_cancelled_queued_mount_does_not_consume_candidate() -> None:
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

    aggregate = create_empty_environment_runtime()
    slow = SlowBinding("slow")
    queued = _Binding("queued")
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        first = asyncio.create_task(aggregate.mount("slow", _runtime_mount(slow)))
        await entry_started.wait()
        second = asyncio.create_task(aggregate.mount("queued", _runtime_mount(queued)))
        await asyncio.sleep(0)

        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert queued.entered == queued.discarded == 0

        release_entry.set()
        assert (await first).kind == "mounted"
        assert (await aggregate.mount("queued", _runtime_mount(queued))).kind == "mounted"
        assert [item.name for item in environment.snapshot.mounts] == ["slow", "queued"]


async def test_prepared_mount_is_cleaned_when_runtime_becomes_terminal() -> None:
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
                self.exit_event.set()

    aggregate = create_empty_environment_runtime()
    candidate = SlowBinding("terminal")
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        mutation = asyncio.create_task(aggregate.mount("terminal", _runtime_mount(candidate)))
        await entry_started.wait()
        aggregate._begin_close()
        release_entry.set()

        with pytest.raises(EnvironmentError) as closed:
            await mutation
        assert closed.value.code == "run_not_active"
        assert environment.snapshot.mounts == ()
        assert candidate.entered == candidate.exited == 1

        with pytest.raises(EnvironmentError) as rejected:
            await aggregate.set_default(None)
        assert rejected.value.code == "run_not_active"


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
    aggregate = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    scope = aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={})
    environment = await scope.__aenter__()
    await aggregate._activate()
    operation = asyncio.create_task(environment.files.stat("/workspace/value"))
    await started.wait()

    with pytest.raises(BaseExceptionGroup):
        await asyncio.wait_for(scope.__aexit__(None, None, None), timeout=0.2)
    assert provider.exited == 0

    release.set()
    assert (await operation).path == "/workspace/value"
    await asyncio.wait_for(provider.exit_event.wait(), timeout=0.2)
    assert provider.exited == 1


async def test_readiness_timeout_cancels_an_unowned_worker_and_releases_its_mount() -> None:
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
    aggregate = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        operation = asyncio.create_task(
            environment.ensure_ready(
                EnvironmentReadinessRequirement(
                    operations=frozenset({"files"}),
                    timeout_seconds=0.05,
                )
            )
        )
        await readiness_started.wait()
        await aggregate.unmount("workspace-1")

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
    aggregate = create_environment_runtime(
        mounts=_request(provider),
        default_mount="workspace-1",
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
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

        await aggregate.unmount("workspace-1")
        with pytest.raises(EnvironmentError) as timed_out:
            await short_waiter
        assert timed_out.value.code == "environment_timeout"
        assert not long_waiter.done()
        assert provider.exited == 0

        release_readiness.set()
        await long_waiter
        await asyncio.wait_for(provider.exit_event.wait(), timeout=0.5)
        assert provider.exited == 1


async def test_reused_candidate_rejection_leaves_independent_candidate_transferable() -> None:
    aggregate = create_empty_environment_runtime()
    active = _Binding("active")
    fresh = _Binding("fresh")
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        await aggregate.mount("active", _runtime_mount(active), make_default=True)

        with pytest.raises(EnvironmentError) as reused:
            await aggregate.replace("active", _runtime_mount(active))
        assert reused.value.code == "environment_provider_binding_reused"
        assert fresh.entered == fresh.discarded == 0

        assert (await aggregate.mount("fresh", _runtime_mount(fresh))).kind == "mounted"
        assert [item.name for item in environment.snapshot.mounts] == ["active", "fresh"]
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

    stubborn = StubbornDiscardBinding("stubborn-discard", fail_entry=True)
    later = _Binding("later-discard")
    aggregate = create_empty_environment_runtime()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        with pytest.raises(BaseExceptionGroup):
            await aggregate.mount("stubborn", _runtime_mount(stubborn))
        assert stubborn.discarded == 1

        assert (await aggregate.mount("later", _runtime_mount(later))).kind == "mounted"
        assert [item.name for item in environment.snapshot.mounts] == ["later"]
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.2)


async def test_provider_bound_artifacts_must_match_selected_mount() -> None:
    class WrongReceiptFiles:
        async def write_text(self, path: str, text: str, **kwargs: Any) -> FileWriteResult:
            del text, kwargs
            return FileWriteResult(
                path=path,
                bytes_written=1,
                receipt=EnvironmentOperationReceipt(
                    mount_id="another-mount",
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
    aggregate = create_environment_runtime(
        mounts={
            "workspace": _runtime_mount(
                provider,
                permissions=frozenset({EnvironmentAction.FILE_WRITE_TEXT}),
            )
        },
        default_mount="workspace",
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
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
    candidate.bound.availability = EnvironmentAvailability(status="available", ready_families=frozenset({"files"}))
    aggregate = create_empty_environment_runtime()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}):
        await aggregate._activate()
        with pytest.raises(BaseExceptionGroup) as raised:
            await aggregate.mount("invalid", _runtime_mount(candidate))

    def flatten(error: BaseException) -> list[BaseException]:
        if isinstance(error, BaseExceptionGroup):
            return [nested for child in error.exceptions for nested in flatten(child)]
        return [error]

    failures = flatten(raised.value)
    assert any(isinstance(error, EnvironmentError) for error in failures)
    assert any(isinstance(error, RuntimeError) and str(error) == "scope cleanup failed" for error in failures)


class _IdempotentProcessOperations:
    def __init__(self) -> None:
        self._handles: tuple[BoundProcessHandle, ...] = ()
        self._next = 0
        self.inspect_result: BoundProcessHandle | None = None
        self.inspect_error: str | None = None
        self._mount_id: str | None = None

    @property
    def handles(self) -> tuple[BoundProcessHandle, ...]:
        return self._handles

    def configure_mount(self, mount_id: str) -> None:
        self._mount_id = mount_id
        self._handles = tuple(
            BoundProcessHandle(
                mount_id=mount_id,
                identity=ProcessIdentity(
                    provider_type="test_provider",
                    environment_id="environment:processes",
                    generation="generation-processes",
                    process_id=f"provider-process-{index}",
                ),
                observed_generation="generation-processes",
                handle=OpaqueProcessHandle._from_payload(f"process-{index}"),
            )
            for index in (1, 2)
        )
        self.inspect_result = self._handles[0]

    def _receipt(self) -> EnvironmentOperationReceipt:
        assert self._mount_id is not None
        return EnvironmentOperationReceipt(
            mount_id=self._mount_id,
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
        if self.inspect_error is not None:
            raise EnvironmentError("process is unavailable", code=self.inspect_error)
        assert self.inspect_result is not None
        return self._info(self.inspect_result)

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        del handle
        return self._receipt()


def _process_permissions() -> frozenset[EnvironmentAction]:
    return frozenset(
        {
            EnvironmentAction.PROCESS_START,
            EnvironmentAction.PROCESS_INSPECT,
            EnvironmentAction.PROCESS_RELEASE,
        }
    )


def _process_test_binding() -> tuple[Any, _IdempotentProcessOperations]:
    operations = _IdempotentProcessOperations()
    provider = _Binding(
        "processes",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=operations),
        permissions=_process_permissions(),
    )
    aggregate = create_environment_runtime(
        mounts={
            "processes": _runtime_mount(
                provider,
                permissions=_process_permissions(),
            )
        },
        default_mount="processes",
    )
    return aggregate, operations


def _process_request() -> CommandRequest:
    return CommandRequest(
        command=ArgvCommand(executable="fake-process"),
        output_policy=EnvironmentOutputPolicy(
            max_inline_bytes=64,
            max_output_bytes=64,
            overflow="truncate",
        ),
    )


async def test_process_rebind_selects_environment_instance_identity_instead_of_default_mount() -> None:
    identity = ProcessIdentity(
        provider_type="test_provider",
        environment_id="environment:a",
        generation="generation-a",
        process_id="provider-process-a",
    )

    class RebindOperations:
        def __init__(self) -> None:
            self.result_handle: BoundProcessHandle | None = None
            self.identities: list[ProcessIdentity] = []

        async def rebind(
            self,
            selected: ProcessIdentity,
            *,
            output_policy: EnvironmentOutputPolicy,
        ) -> ProcessInfo:
            del output_policy
            self.identities.append(selected)
            assert self.result_handle is not None
            return _IdempotentProcessOperations._info(self.result_handle)

        async def inspect(self, selected: BoundProcessHandle) -> ProcessInfo:
            del selected
            assert self.result_handle is not None
            return _IdempotentProcessOperations._info(self.result_handle)

    selected_operations = RebindOperations()
    retarget_operations = RebindOperations()
    permissions = frozenset({EnvironmentAction.PROCESS_INSPECT})
    provider_a = _Binding(
        "a",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=selected_operations),
        permissions=permissions,
    )
    provider_b = _Binding(
        "b",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=retarget_operations),
        permissions=permissions,
    )
    aggregate = create_environment_runtime(
        mounts={
            "original": _runtime_mount(provider_a, permissions=permissions),
            "target": _runtime_mount(provider_b, permissions=permissions),
        },
        default_mount="target",
    )

    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        assert provider_a.mount_id is not None
        assert provider_b.mount_id is not None
        selected_operations.result_handle = BoundProcessHandle(
            mount_id=provider_a.mount_id,
            identity=identity,
            observed_generation="generation-a",
            handle=OpaqueProcessHandle._from_payload("bound-process-a"),
        )
        retarget_operations.result_handle = BoundProcessHandle(
            mount_id=provider_b.mount_id,
            identity=ProcessIdentity(
                provider_type="test_provider",
                environment_id="environment:b",
                generation="generation-b",
                process_id="provider-process-b",
            ),
            observed_generation="generation-b",
            handle=OpaqueProcessHandle._from_payload("bound-process-b"),
        )
        await aggregate._activate()
        rebound = await environment.processes.rebind(
            identity,
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=64,
                max_output_bytes=64,
                overflow="retain",
            ),
        )

    assert rebound.handle.identity == identity
    assert selected_operations.identities == [identity]
    assert retarget_operations.identities == []


async def test_process_rebind_keeps_unattached_and_changed_generation_failures_distinct() -> None:
    class ProcessOperations:
        async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
            del handle
            raise AssertionError("identity validation must happen before provider dispatch")

    permissions = frozenset({EnvironmentAction.PROCESS_INSPECT})
    provider = _Binding(
        "current",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=ProcessOperations()),
        permissions=permissions,
    )
    aggregate = create_environment_runtime(
        mounts={"current": _runtime_mount(provider, permissions=permissions)},
        default_mount="current",
    )
    policy = EnvironmentOutputPolicy(max_inline_bytes=64, max_output_bytes=64, overflow="retain")

    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await aggregate._activate()
        with pytest.raises(EnvironmentError) as unattached:
            await environment.processes.rebind(
                ProcessIdentity(
                    provider_type="test_provider",
                    environment_id="environment:missing",
                    generation="generation-missing",
                    process_id="provider-process",
                ),
                output_policy=policy,
            )
        with pytest.raises(EnvironmentError) as changed_generation:
            await environment.processes.rebind(
                ProcessIdentity(
                    provider_type="test_provider",
                    environment_id="environment:current",
                    generation="generation-old",
                    process_id="provider-process",
                ),
                output_policy=policy,
            )

    assert unattached.value.code == "environment_selection_invalid"
    assert changed_generation.value.code == "environment_process_generation_mismatch"


async def test_resolved_file_routes_cannot_acquire_a_new_lease_after_unmount() -> None:
    provider = _Binding("stale-route")
    runtime = create_environment_runtime(
        mounts={"workspace": _runtime_mount(provider)},
        default_mount="workspace",
    )

    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await runtime._activate()
        selected = environment.resolve_path("/workspace/file.txt")
        scoped = environment.select_files("/workspace")
        assert (await runtime.unmount("workspace")).kind == "unmounted"

        with pytest.raises(EnvironmentError) as stale_route:
            async with cast(Any, environment)._prepare_file(selected, EnvironmentAction.FILE_STAT):
                pass
        with pytest.raises(EnvironmentError) as stale_scope:
            async with environment.open_files(scoped):
                pass

    assert stale_route.value.code == "environment_stale_mount"
    assert stale_scope.value.code == "environment_stale_mount"
    assert provider.bound.ready_calls == []


async def test_file_list_keeps_captured_virtual_root_during_unmount() -> None:
    class BlockingFiles:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def list(self, path: str, **kwargs: Any) -> FileEntriesResult:
            del kwargs
            self.started.set()
            await self.release.wait()
            return FileEntriesResult(
                entries=(FileMetadata(path=f"{path.rstrip('/')}/child.txt", kind="file", size=1, writable=True),),
                offset=0,
                has_more=False,
            )

    files = BlockingFiles()
    permissions = frozenset({EnvironmentAction.FILE_LIST})
    provider = _Binding(
        "blocking-list",
        operations=EnvironmentProviderOperations(files=files),
        permissions=permissions,
    )
    runtime = create_environment_runtime(
        mounts={"workspace": _runtime_mount(provider, permissions=permissions)},
        default_mount="workspace",
    )

    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        await runtime._activate()
        listing = asyncio.create_task(environment.files.list("/workspace"))
        await files.started.wait()
        assert (await runtime.unmount("workspace")).kind == "unmounted"
        assert provider.exited == 0
        files.release.set()
        result = await listing
        assert result.entries[0].path == "/workspace/child.txt"
        await provider.exit_event.wait()


async def test_process_handle_from_another_runtime_is_stale() -> None:
    aggregate, operations = _process_test_binding()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        operations.configure_mount(environment.resolve_path("/workspace").mount_id)
        await aggregate._activate()
        started = await environment.processes.start(_process_request())
        stale_handle = started.process.handle.model_copy(update={"mount_id": "mount-stale"})

        with pytest.raises(EnvironmentError) as stale:
            await environment.processes.inspect(stale_handle)

    assert stale.value.code == "environment_stale_mount"


async def test_authoritative_process_loss_releases_the_mount_handle_fence() -> None:
    aggregate, operations = _process_test_binding()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        operations.configure_mount(environment.resolve_path("/workspace").mount_id)
        await aggregate._activate()
        started = await environment.processes.start(_process_request())
        operations.inspect_error = "environment_not_found"

        with pytest.raises(EnvironmentError) as missing:
            await environment.processes.inspect(started.process.handle)
        assert missing.value.code == "environment_not_found"
        assert (await aggregate.unmount("processes")).kind == "unmounted"


async def test_unmount_retires_mount_after_active_process_handles_are_released() -> None:
    aggregate, operations = _process_test_binding()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        operations.configure_mount(environment.resolve_path("/workspace").mount_id)
        await aggregate._activate()
        first = await environment.processes.start(_process_request())
        second = await environment.processes.start(_process_request())
        await environment.processes.release(first.process.handle)
        await environment.processes.release(first.process.handle)

        assert (await aggregate.unmount("processes")).kind == "unmounted"
        assert environment.snapshot.mounts == ()
        operations.inspect_result = second.process.handle
        assert (await environment.processes.inspect(second.process.handle)).handle == second.process.handle
        await environment.processes.release(second.process.handle)


async def test_replace_retires_old_mount_after_active_process_handles_are_released() -> None:
    aggregate, operations = _process_test_binding()
    replacement_operations = _IdempotentProcessOperations()
    replacement = _Binding(
        "replacement-processes",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=replacement_operations),
        permissions=_process_permissions(),
    )
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        operations.configure_mount(environment.resolve_path("/workspace").mount_id)
        await aggregate._activate()
        started = await environment.processes.start(_process_request())

        assert (
            await aggregate.replace(
                "processes",
                _runtime_mount(replacement, permissions=_process_permissions()),
            )
        ).kind == "replaced"
        assert replacement.entered == 1
        assert replacement.exited == 0
        assert environment.snapshot.mounts[0].descriptor.generation == "generation-replacement-processes"
        assert (await environment.processes.inspect(started.process.handle)).handle == started.process.handle

        await environment.processes.release(started.process.handle)


async def test_process_result_cannot_substitute_another_same_mount_handle() -> None:
    aggregate, operations = _process_test_binding()
    async with aggregate.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        operations.configure_mount(environment.resolve_path("/workspace").mount_id)
        await aggregate._activate()
        first = await environment.processes.start(_process_request())
        await environment.processes.start(_process_request())
        operations.inspect_result = operations.handles[1]

        with pytest.raises(EnvironmentError) as retargeted:
            await environment.processes.inspect(first.process.handle)
        assert retargeted.value.code == "environment_provider_failure"


async def test_readiness_publishes_permissions_before_operation_dispatch() -> None:
    binding = _Binding("narrow")

    async def ready(operations):
        binding.bound.descriptor = binding.bound.descriptor.model_copy(
            update={"permissions": EnvironmentPermissionSet()}
        )
        binding.bound.availability = EnvironmentAvailability(status="available", ready_families=frozenset({"files"}))

    binding.bound.ensure_ready = ready
    runtime = create_environment_runtime(mounts=_request(binding), default_mount="workspace-1")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        before = environment.snapshot
        with pytest.raises(EnvironmentError, match="denied"):
            await environment.files.stat("/workspace/file")
        assert not environment.snapshot.mounts[0].permission_ceiling.operations
        assert before.mounts[0].permission_ceiling.operations


async def test_recovery_publishes_new_generation_even_when_reporting_rebuild() -> None:
    binding = _Binding("rebuild")

    async def ready(operations):
        binding.bound.descriptor = binding.bound.descriptor.model_copy(update={"generation": "generation-new"})
        binding.bound.availability = EnvironmentAvailability(status="available", ready_families=frozenset({"files"}))
        raise EnvironmentError("Rebuilt", code="environment_rebuilt")

    runtime = create_environment_runtime(mounts=_request(binding), default_mount="workspace-1")
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as environment:
        selection = environment.select_files("/workspace/file")
        binding.bound.ensure_ready = ready
        with pytest.raises(EnvironmentError) as caught:
            await environment.files.stat("/workspace/file")
        assert caught.value.code == "environment_rebuilt"
        assert environment.snapshot.mounts[0].descriptor.generation == "generation-new"
        assert (await environment.describe("workspace-1")).mount == environment.snapshot.mounts[0]
        with pytest.raises(EnvironmentError, match="stale"):
            async with environment.open_files(selection):
                pass


async def test_process_start_rechecks_compound_actions_after_readiness() -> None:
    operations = _IdempotentProcessOperations()
    provider = _Binding(
        "processes",
        families=frozenset({"processes"}),
        operations=EnvironmentProviderOperations(processes=operations),
        permissions=_process_permissions(),
    )

    async def narrow(operations):
        provider.bound.descriptor = provider.bound.descriptor.model_copy(
            update={"permissions": EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.PROCESS_START}))}
        )
        provider.bound.availability = EnvironmentAvailability(status="available", ready_families=operations)

    provider.bound.ensure_ready = narrow
    runtime = create_environment_runtime(
        mounts={"processes": _runtime_mount(provider, permissions=_process_permissions())},
        default_mount="processes",
    )
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as env:
        assert provider.mount_id is not None
        operations.configure_mount(provider.mount_id)
        with pytest.raises(EnvironmentError) as error:
            await env.processes.start(_process_request(), required_actions=_process_permissions())
        assert error.value.code == "environment_denied"
        assert operations._next == 0


async def test_command_cwd_resource_prepares_shell_without_file_facet() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY
    from a13n_harness.toolsets.shell import ShellToolset

    provider = _Binding(
        "shell-only",
        families=frozenset({"shell"}),
        operations=EnvironmentProviderOperations(shell=SimpleNamespace(exec=AsyncMock())),
        permissions=frozenset({EnvironmentAction.SHELL_EXEC}),
    )
    runtime = create_environment_runtime(
        mounts={"local": _runtime_mount(provider, permissions=frozenset({EnvironmentAction.SHELL_EXEC}))},
        default_mount="local",
    )
    async with runtime.bind(thread_id="thread-1", run_id="run-1", instance=_instance(), host_refs={}) as env:
        tool = ShellToolset(env).get_toolset().tools["shell_exec"]
        resolver = tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        resources = await resolver(
            {"command": "true", "cwd": "/workspace/work"},
            context=SimpleNamespace(environment=env),
        )
        assert provider.bound.ready_calls == [frozenset({"shell"})]
        assert resources[0].approval_revision == "/work"
        assert resources[0].kind == "file"
