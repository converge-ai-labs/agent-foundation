"""Harness owns opened scopes while library identities stay independent of mounts."""

import asyncio
from pathlib import Path

import pytest
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.environment import EnvironmentError, EnvironmentReadinessRequirement
from a13n_harness.environment.advanced import create_environment_runtime

from .environment_helpers import Source

pytestmark = pytest.mark.anyio
INSTANCE = AgentInstanceContext(
    identity=AgentIdentityRef(issuer="test", subject="agent"), agent_instance_id="agent-test"
)


async def test_explicit_readiness_can_open_a_mount_without_requesting_operations(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    runtime = create_environment_runtime(mounts={"workspace": Source(connector)})
    async with runtime.bind(thread_id="thread", run_id="run", instance=INSTANCE, host_refs={}) as bound:
        assert connector.opened == []
        await bound.ensure_ready(
            EnvironmentReadinessRequirement(mounts=frozenset({"workspace"}), operations=frozenset())
        )
        assert len(connector.opened) == 1
        await bound.files.stat("/environment/workspace")
        assert len(connector.opened) == 1
    assert connector.closed == [connector.opened[0].execution_id]


def test_readiness_without_operations_requires_explicit_nonempty_mounts() -> None:
    for mounts in (None, frozenset()):
        with pytest.raises(ValueError):
            EnvironmentReadinessRequirement(mounts=mounts, operations=frozenset())


class Connector(EnvironmentConnector):
    def __init__(self, root: Path, *, failure: bool = False, reuse: bool = False):
        self.delegate = DIRECT_LOCAL.execution_connector({"root": {"path": str(root)}})
        self.failure = failure
        self.reuse = reuse
        self.opened: list[EnvironmentExecution] = []
        self.closed: list[str] = []

    @property
    def provider_key(self):
        return self.delegate.provider_key

    @property
    def environment_id(self):
        return self.delegate.environment_id

    @property
    def descriptor(self):
        return self.delegate.descriptor

    @property
    def state(self):
        return self.delegate.state

    async def open(self):
        if self.failure:
            raise RuntimeError("opening failed")
        if self.reuse and self.opened:
            return self.opened[0]
        execution = await self.delegate.open()
        self.opened.append(execution)
        original_close = execution.close

        async def close():
            self.closed.append(execution.execution_id)
            await original_close()

        execution.close = close
        return execution


def bind(runtime, run="run-test"):
    return runtime.bind(thread_id="thread-test", run_id=run, instance=INSTANCE, host_refs={})


async def test_same_source_opens_only_used_mounts_and_keeps_execution_identity(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    source = Source(connector)
    runtime = create_environment_runtime(mounts={"first": source, "second": source}, default_mount="first")
    async with bind(runtime) as environment:
        await runtime._activate()
        assert not connector.opened
        assert source.ready_calls == 0
        await environment.files.write_text("file.txt", "content", mode="create")
        first = environment.resolve_path("file.txt", alias="first")
        assert len(connector.opened) == source.ready_calls == 1
        await environment.ensure_ready(
            EnvironmentReadinessRequirement(operations=frozenset({"files"}), mounts=frozenset({"second"}))
        )
        second = environment.resolve_path("file.txt", alias="second")
        assert len(connector.opened) == source.ready_calls == 2
        assert first.mount_id != second.mount_id
        assert first.execution_id != second.execution_id
        assert first.execution_id != first.mount_id
        result = await environment.files.read_text("file.txt")
        assert result.text == "content"
    assert set(connector.closed) == {first.execution_id, second.execution_id}
    assert len(connector.closed) == 2


async def test_failed_open_is_deferred_and_does_not_affect_another_mount(tmp_path: Path) -> None:
    first = Connector(tmp_path)
    second = Connector(tmp_path, failure=True)
    runtime = create_environment_runtime(mounts={"first": Source(first), "second": Source(second)})
    async with bind(runtime) as environment:
        await runtime._activate()
        assert not first.opened and not second.opened
        with pytest.raises(RuntimeError, match="opening failed"):
            await environment.files.stat("/environment/second/file.txt")
        await environment.files.write_text("/environment/first/file.txt", "ok", mode="create")
        assert not first.closed
    assert first.closed == [first.opened[0].execution_id]


async def test_reused_execution_rejection_does_not_close_the_existing_owner(tmp_path: Path) -> None:
    connector = Connector(tmp_path, reuse=True)
    source = Source(connector)
    first = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    second = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    async with bind(first, "run-first") as environment:
        await first._activate()
        await environment.files.write_text("file.txt", "ok", mode="create")
        async with bind(second, "run-second") as other:
            await second._activate()
            with pytest.raises(EnvironmentError) as failure:
                await other.files.stat("file.txt")
            assert failure.value.code == "environment_execution_reused"
        assert not connector.closed
        await environment.files.write_text("still-open.txt", "ok", mode="create")
    assert connector.closed == [connector.opened[0].execution_id]


async def test_replacement_is_inert_until_used_and_retires_old_scope(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    source = Source(connector)
    runtime = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        await environment.files.write_text("file.txt", "ok", mode="create")
        before = environment.resolve_path(".")
        await runtime.replace("workspace", source)
        assert len(connector.opened) == 1
        await environment.files.stat("file.txt")
        after = environment.resolve_path(".")
        assert before.mount_id != after.mount_id
        assert before.execution_id != after.execution_id
    assert len(connector.closed) == 2


async def test_unused_source_is_not_activated_by_context_or_state_export(tmp_path: Path) -> None:
    from a13n_harness.model_context import ModelContextProjectionRequest, ModelContextRequestKind

    source = Source(Connector(tmp_path))
    runtime = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        assert (await environment.describe("workspace")).availability.status == "preparing"
        await environment.project_model_context(ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT))
        assert environment.dump_states() == {}
        assert source.ready_calls == 0
    assert not source.connector.opened and not source.connector.closed


async def test_concurrent_first_use_shares_activation_and_cancelled_waiter_does_not_interrupt_it(
    tmp_path: Path,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingSource(Source):
        async def ensure_ready(self):
            self.ready_calls += 1
            started.set()
            await release.wait()
            return self.connector

    source = WaitingSource(Connector(tmp_path))
    runtime = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    (tmp_path / "file.txt").write_text("ok")
    async with bind(runtime) as environment:
        await runtime._activate()
        one = asyncio.create_task(environment.files.read_text("file.txt"))
        await started.wait()
        two = asyncio.create_task(environment.files.read_text("file.txt"))
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        release.set()
        assert (await two).text == "ok"
        assert source.ready_calls == len(source.connector.opened) == 1
    assert len(source.connector.closed) == 1


async def test_source_failure_is_cached_until_replacement(tmp_path: Path) -> None:
    from a13n_harness.errors import EnvironmentActivationError

    class FailedSource(Source):
        async def ensure_ready(self):
            self.ready_calls += 1
            raise RuntimeError("Host could not prepare target")

    source = FailedSource(Connector(tmp_path))
    runtime = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        for _ in range(2):
            with pytest.raises(EnvironmentActivationError) as failure:
                await environment.files.stat("file.txt")
            assert isinstance(failure.value.__cause__, RuntimeError)
        assert source.ready_calls == 1
        assert not source.connector.opened
        await runtime.replace("workspace", Source(source.connector))
        await environment.files.write_text("file.txt", "recovered", mode="create")


async def test_bare_connector_is_rejected_by_every_public_mount_entry(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    with pytest.raises(EnvironmentError, match="EnvironmentSource"):
        create_environment_runtime(mounts={"workspace": connector})
    runtime = create_environment_runtime(mounts={})
    async with bind(runtime):
        await runtime._activate()
        with pytest.raises(EnvironmentError, match="EnvironmentSource"):
            await runtime.mount("workspace", connector)
    assert not connector.opened


async def test_late_open_after_replacement_is_closed_without_dispatch(tmp_path: Path) -> None:
    started = asyncio.Event()

    class LateConnector(Connector):
        async def open(self):
            execution = await super().open()
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A provider may finish acquisition while cancellation is delivered.
                return execution

    connector = LateConnector(tmp_path)
    runtime = create_environment_runtime(mounts={"workspace": Source(connector)}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        write = asyncio.create_task(environment.files.write_text("old.txt", "must not dispatch", mode="create"))
        await started.wait()
        await runtime.replace("workspace", Source(Connector(tmp_path)))
        with pytest.raises(EnvironmentError) as failure:
            await write
        assert failure.value.code == "environment_closed"
        assert connector.closed == [connector.opened[0].execution_id]
        assert not (tmp_path / "old.txt").exists()
        await environment.files.write_text("new.txt", "ok", mode="create")
    assert len(connector.closed) == 1


async def test_run_exit_settles_activation_after_its_only_waiter_cancels(tmp_path: Path) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    class WaitingSource(Source):
        async def ensure_ready(self):
            self.ready_calls += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return self.connector

    source = WaitingSource(Connector(tmp_path))
    runtime = create_environment_runtime(mounts={"workspace": source}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        use = asyncio.create_task(environment.files.stat("file.txt"))
        await started.wait()
        use.cancel()
        with pytest.raises(asyncio.CancelledError):
            await use
        assert not cancelled.is_set()
    assert cancelled.is_set()
    assert source.ready_calls == 1
    assert source.connector.opened == source.connector.closed == []
