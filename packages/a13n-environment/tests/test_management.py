"""Management evidence and execution ownership are separate public contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from a13n_environment._backend import BackendTarget
from a13n_environment._backend_factory import BackendFactory
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.errors import (
    EnvironmentManagementError,
    EnvironmentProviderError,
    observed_environment_state,
    provider_error,
)
from a13n_environment.errors import (
    EnvironmentProviderErrorCategory as Category,
)
from a13n_environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_environment.operations import EnvironmentOperations
from pydantic import BaseModel

pytestmark = pytest.mark.anyio
STATE = EnvironmentState(provider_key="test_provider", state_version="1", state={"target": "native-1"})
DESCRIPTOR = EnvironmentDescriptor(
    generation="generation-test", operation_families=frozenset(), permissions=EnvironmentPermissionSet()
)


class Inputs(BaseModel):
    pass


@dataclass
class Runtime:
    closed: int = 0
    events: list[str] = field(default_factory=list)

    async def close(self) -> None:
        self.closed += 1


class Target(BackendTarget):
    provider_key = "test_provider"
    environment_id = "env-test"
    descriptor = DESCRIPTOR
    availability = EnvironmentAvailability(status="available")
    operations = EnvironmentOperations()

    def __init__(self, state: EnvironmentState | None, runtime: Runtime):
        super().__init__(state)
        self.runtime = runtime
        self.create_error: BaseException | None = None
        self.open_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.mutate_on_open = False

    async def create(self) -> None:
        self.runtime.events.append("create")
        self._cache_state(STATE)
        if self.create_error is not None:
            raise self.create_error

    async def open(self, *, execution_id: str) -> None:
        self.runtime.events.append(f"open:{execution_id}")
        if self.mutate_on_open:
            self._cache_state(STATE.model_copy(update={"state": {"target": "replacement"}}))
        if self.open_error is not None:
            raise self.open_error

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        self.runtime.events.append("check")

    async def destroy(self) -> None:
        self.runtime.events.append("destroy")

    async def close(self) -> None:
        self.runtime.events.append("close")
        if self.close_error is not None:
            raise self.close_error


def definition(*, configure=lambda target: None):
    runtimes: list[Runtime] = []
    targets: list[Target] = []

    async def acquire(*, configuration, credential):
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime

    def construct(*, configuration, environment_id, state, runtime, operation_id):
        assert runtime is not None
        target = Target(state, runtime)
        configure(target)
        targets.append(target)
        return target

    factory = BackendFactory("test_provider", Inputs, construct, lambda recipe: DESCRIPTOR, acquire)
    provider = EnvironmentProviderDefinition(
        type="test_provider",
        display_name="Test",
        configuration_model=Inputs,
        environment_model=Inputs,
        describe_environment=lambda recipe: DESCRIPTOR,
        connector_factory=factory.connector,
        provider_factory=factory.provider,
    )
    return provider, runtimes, targets


async def test_connector_is_inert_and_each_open_owns_an_independent_execution() -> None:
    provider, runtimes, targets = definition()
    connector = provider.execution_connector({}, environment_id="env-test", state=STATE)
    assert runtimes == targets == []
    first = await connector.open()
    second = await connector.open()
    assert first.execution_id != second.execution_id
    assert len(runtimes) == 2
    assert all(runtime.events[0].startswith("open:exec-") for runtime in runtimes)
    assert all(runtime.events[1] == "check" for runtime in runtimes)
    await first.close()
    await first.close()
    assert runtimes[0].closed == 1 and runtimes[1].closed == 0
    await second.check_ready(frozenset())
    await second.close()
    assert runtimes[1].closed == 1
    assert all("create" not in runtime.events and "destroy" not in runtime.events for runtime in runtimes)


async def test_management_provider_close_cannot_close_its_connectors_execution() -> None:
    definition_, runtimes, _ = definition()
    manager = await definition_.open_provider()
    state = await manager.create({}, environment_id="env-test", operation_id="op-create")
    assert state == STATE
    connector = manager.execution_connector({}, environment_id="env-test", state=state)
    execution = await connector.open()
    await manager.close()
    assert runtimes[0].closed == 1 and runtimes[1].closed == 0
    await execution.check_ready(frozenset())
    await execution.close()
    assert runtimes[0].events == ["create", "close"]


async def test_connector_detaches_state_from_both_input_and_readers() -> None:
    definition_, _, _ = definition()
    state = STATE.model_copy(deep=True)
    connector = definition_.execution_connector({}, state=state)
    state.state["target"] = "changed-input"  # type: ignore[index]
    returned = connector.state
    assert returned is not None
    returned.state["target"] = "changed-output"  # type: ignore[index]
    execution = await connector.open()
    assert connector.state == execution.state == STATE
    await execution.close()


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_failed_open_releases_partial_resources_and_preserves_primary_failure(error: BaseException) -> None:
    def configure(target: Target) -> None:
        target.open_error = error
        target.close_error = RuntimeError("cleanup failed")

    definition_, runtimes, _ = definition(configure=configure)
    with pytest.raises(type(error)) as failure:
        await definition_.execution_connector({}, state=STATE).open()
    assert failure.value is error
    assert runtimes[0].events[-1] == "close"
    assert runtimes[0].closed == 1
    assert "cleanup failed" in failure.value.__notes__[0]


async def test_open_cannot_replace_the_selected_state() -> None:
    def configure(target: Target) -> None:
        target.mutate_on_open = True

    definition_, runtimes, _ = definition(configure=configure)
    connector = definition_.execution_connector({}, state=STATE)
    with pytest.raises(EnvironmentProviderError, match="conflicts") as failure:
        await connector.open()
    assert failure.value.code == "provider_target_changed"
    assert connector.state == STATE
    assert runtimes[0].closed == 1


async def test_borrowed_runtime_is_never_closed_by_manager_or_execution() -> None:
    definition_, runtimes, _ = definition()
    borrowed = Runtime()
    manager = await definition_.open_provider(runtime=borrowed)
    execution = await manager.execution_connector({}, environment_id="env-test", state=STATE).open()
    await manager.close()
    await execution.close()
    assert runtimes == [] and borrowed.closed == 0


async def test_management_failure_preserves_observed_state_and_operation_identity() -> None:
    def configure(target: Target) -> None:
        target.create_error = provider_error("test_provider", "readiness_failed", Category.UNAVAILABLE)

    definition_, runtimes, _ = definition(configure=configure)
    async with await definition_.open_provider() as manager:
        with pytest.raises(EnvironmentManagementError) as failure:
            await manager.create({}, environment_id="env-test", operation_id="op-current")
        assert failure.value.state == STATE
        assert failure.value.context.operation_id == "op-current"
        assert observed_environment_state(failure.value, None) == STATE
    assert runtimes[0].events == ["create", "close"]
    assert runtimes[0].closed == 1


async def test_management_timeout_retains_observed_state_through_cancellation() -> None:
    definition_, _, targets = definition()
    async with await definition_.open_provider() as manager:
        original = Target.create

        async def suspended_create(self: Target) -> None:
            await original(self)
            await asyncio.Event().wait()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Target, "create", suspended_create)
            with pytest.raises(TimeoutError) as failure:
                async with asyncio.timeout(0.01):
                    await manager.create({}, environment_id="env-test", operation_id="op-cancelled")
        assert observed_environment_state(failure.value, None) == STATE
        assert targets[0].runtime.events == ["create", "close"]


async def test_confirmed_destroy_reports_absence_even_when_cleanup_fails() -> None:
    def configure(target: Target) -> None:
        target.close_error = RuntimeError("close failed")

    definition_, _, _ = definition(configure=configure)
    async with await definition_.open_provider() as manager:
        with pytest.raises(EnvironmentManagementError) as failure:
            await manager.destroy({}, environment_id="env-test", operation_id="op-destroy", state=STATE)
        assert failure.value.category == Category.CLEANUP
        assert observed_environment_state(failure.value, STATE) is None


async def test_closed_execution_rejects_access_and_readiness_without_reopening() -> None:
    definition_, runtimes, _ = definition()
    execution = await definition_.execution_connector({}, state=STATE).open()
    await execution.close()
    with pytest.raises(EnvironmentError) as failure:
        await execution.check_ready(frozenset())
    assert failure.value.code == "environment_closed"
    with pytest.raises(EnvironmentError):
        _ = execution.operations
    assert len(runtimes) == 1


async def test_cancelled_management_close_finishes_once_and_fences_new_work(monkeypatch) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed_close(self: Runtime) -> None:
        started.set()
        await finish.wait()
        self.closed += 1

    monkeypatch.setattr(Runtime, "close", delayed_close)
    definition_, runtimes, _ = definition()
    manager = await definition_.open_provider()
    closing = asyncio.create_task(manager.close())
    await started.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(EnvironmentProviderError) as failure:
        await manager.create({}, environment_id="env-test", operation_id="op-late")
    assert failure.value.code == "provider_closed"
    finish.set()
    await manager.close()
    await manager.close()
    assert runtimes[0].closed == 1
