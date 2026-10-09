"""Harness owns opened scopes while library identities stay independent of mounts."""

from pathlib import Path

import pytest
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.environment import EnvironmentError
from a13n_harness.environment.advanced import create_environment_runtime

pytestmark = pytest.mark.anyio
INSTANCE = AgentInstanceContext(
    identity=AgentIdentityRef(issuer="test", subject="agent"), agent_instance_id="agent-test"
)


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


async def test_same_connector_opens_independent_mounts_and_keeps_execution_identity(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    runtime = create_environment_runtime(mounts={"first": connector, "second": connector}, default_mount="first")
    async with bind(runtime) as environment:
        await runtime._activate()
        assert len(connector.opened) == 2
        first = environment.resolve_path("file.txt", alias="first")
        second = environment.resolve_path("file.txt", alias="second")
        assert first.mount_id != second.mount_id
        assert first.execution_id != second.execution_id
        assert first.execution_id != first.mount_id
        result = await environment.files.write_text("file.txt", "content", mode="create")
        assert result.receipt.execution_id == first.execution_id
        assert "mount_id" not in result.receipt.model_dump()
    assert set(connector.closed) == {first.execution_id, second.execution_id}
    assert len(connector.closed) == 2


async def test_failed_initial_open_closes_every_successful_scope(tmp_path: Path) -> None:
    first = Connector(tmp_path)
    second = Connector(tmp_path, failure=True)
    runtime = create_environment_runtime(mounts={"first": first, "second": second})
    with pytest.raises(RuntimeError, match="opening failed"):
        async with bind(runtime):
            pytest.fail("Partial mount set became visible")
    assert first.closed == [first.opened[0].execution_id]
    assert not second.opened


async def test_reused_execution_rejection_does_not_close_the_existing_owner(tmp_path: Path) -> None:
    connector = Connector(tmp_path, reuse=True)
    first = create_environment_runtime(mounts={"workspace": connector}, default_mount="workspace")
    second = create_environment_runtime(mounts={"workspace": connector}, default_mount="workspace")
    async with bind(first, "run-first") as environment:
        await first._activate()
        with pytest.raises(EnvironmentError) as failure:
            async with bind(second, "run-second"):
                pytest.fail("Execution was accepted twice")
        assert failure.value.code == "environment_execution_reused"
        assert not connector.closed
        await environment.files.write_text("still-open.txt", "ok", mode="create")
    assert connector.closed == [connector.opened[0].execution_id]


async def test_explicit_replacement_opens_new_execution_and_retires_old_scope(tmp_path: Path) -> None:
    connector = Connector(tmp_path)
    runtime = create_environment_runtime(mounts={"workspace": connector}, default_mount="workspace")
    async with bind(runtime) as environment:
        await runtime._activate()
        before = environment.resolve_path(".")
        await runtime.replace("workspace", connector)
        after = environment.resolve_path(".")
        assert before.mount_id != after.mount_id
        assert before.execution_id != after.execution_id
    assert len(connector.closed) == 2
