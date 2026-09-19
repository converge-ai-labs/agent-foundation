from __future__ import annotations

from dataclasses import replace

import pytest
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from pydantic import BaseModel

pytestmark = pytest.mark.anyio


class _Environment(Environment):
    def __init__(
        self,
        state: EnvironmentState | None = None,
        *,
        enter_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__(state)
        self.events: list[str] = []
        self.enter_error = enter_error
        self.close_error = close_error

    @property
    def provider_key(self) -> str:
        return "test_provider"

    @property
    def environment_id(self) -> str:
        return "environment-test"

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return EnvironmentDescriptor(
            generation="generation-test",
            operation_families=frozenset(),
            permissions=EnvironmentPermissionSet(),
        )

    @property
    def availability(self) -> EnvironmentAvailability:
        return EnvironmentAvailability(status="ready")

    @property
    def operations(self) -> EnvironmentOperations:
        return EnvironmentOperations()

    async def _prepare(self, *, mount_id: str) -> None:
        self.events.append(f"enter:{mount_id}")
        self._cache_state(
            EnvironmentState(
                provider_key="test_provider",
                state_version="1",
                state={"target_id": "target-created"},
            )
        )
        if self.enter_error is not None:
            raise self.enter_error

    async def _ensure_ready(self, operations: frozenset[str]) -> None:  # type: ignore[override]
        self.events.append(f"ready:{','.join(sorted(operations))}")

    async def _close(self) -> None:
        self.events.append("close")
        if self.close_error is not None:
            raise self.close_error

    async def _destroy(self) -> None:
        self.events.append("destroy")


async def test_context_entry_closes_without_destroying_backing_target() -> None:
    environment = _Environment()

    async with environment:
        await environment.prepare()
        assert environment.is_entered
        assert environment.dump_state() is not None

    assert environment.events[0].startswith("enter:mount-")
    assert environment.events[1:] == ["close"]
    assert environment.dump_state() is not None


async def test_entry_receives_ephemeral_correlation_and_is_single_use() -> None:
    environment = _Environment()

    await environment.enter(mount_id="workspace")
    await environment.prepare()
    assert environment.events == ["enter:workspace"]

    with pytest.raises(RuntimeError, match="exactly once"):
        await environment.enter(mount_id="workspace")
        await environment.prepare()
    await environment.close()


async def test_failed_entry_keeps_last_known_cached_state_for_host_publication() -> None:
    environment = _Environment(enter_error=RuntimeError("entry failed"))

    with pytest.raises(RuntimeError, match="entry failed"):
        await environment.enter(mount_id="workspace")
        await environment.prepare()

    assert environment.dump_state() == EnvironmentState(
        provider_key="test_provider",
        state_version="1",
        state={"target_id": "target-created"},
    )
    await environment.close()


async def test_dump_state_is_detached_from_callers_and_the_supplied_state() -> None:
    state = EnvironmentState(
        provider_key="test_provider",
        state_version="1",
        state={"target": {"aliases": ["original"]}},
    )
    environment = _Environment(state)

    state.state["target"]["aliases"].append("supplied")  # type: ignore[index,union-attr]
    dumped = environment.dump_state()
    assert dumped is not None
    dumped.state["target"]["aliases"].append("returned")  # type: ignore[index,union-attr]

    assert environment.dump_state() == EnvironmentState(
        provider_key="test_provider",
        state_version="1",
        state={"target": {"aliases": ["original"]}},
    )
    await environment.close()


async def test_destroy_is_explicit_uses_fresh_adapter_and_clears_state() -> None:
    state = EnvironmentState(
        provider_key="test_provider",
        state_version="1",
        state={"target_id": "target-1"},
    )
    environment = _Environment(state)

    await environment.destroy()
    assert environment.events == ["destroy"]
    assert environment.dump_state() is None
    await environment.close()
    assert environment.events == ["destroy", "close"]


async def test_close_failure_does_not_erase_cached_state() -> None:
    state = EnvironmentState(
        provider_key="test_provider",
        state_version="1",
        state={"target_id": "target-1"},
    )
    environment = _Environment(state, close_error=RuntimeError("close failed"))

    with pytest.raises(RuntimeError, match="close failed"):
        await environment.close()

    assert environment.dump_state() == state


async def test_rebinding_a_mount_is_used_by_later_recovery() -> None:
    """Recovery must prepare against the mount the Host is currently using."""

    class _Recoverable(_Environment):
        recover_on_unavailable = True

    environment = _Recoverable()
    await environment.enter(mount_id="first")
    await environment.prepare()
    environment.bind_mount("second")

    await environment.recover()

    assert environment.events == ["enter:first", "enter:second"]
    await environment.close()


async def test_undeclared_lifecycle_operations_answer_with_one_typed_error() -> None:
    environment = _Environment()

    for operation in (environment.stop(), environment.reconcile()):
        with pytest.raises(EnvironmentProviderError) as failure:
            await operation
        assert failure.value.code == "provider_operation_unsupported"
        assert failure.value.safe_projection().category.value == "unsupported"


class _Inputs(BaseModel):
    pass


class _Runtime:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


def _definition(created: list[_Runtime]) -> EnvironmentProviderDefinition:
    async def runtime_factory(*, configuration, credential, operation_id, allow_create):  # type: ignore[no-untyped-def]
        del configuration, credential, operation_id, allow_create
        created.append(_Runtime())
        return created[-1]

    def construct(*, configuration, environment_id, state, runtime):  # type: ignore[no-untyped-def]
        del configuration, environment_id, runtime
        return _Environment(state)

    return EnvironmentProviderDefinition(
        type="test_provider",
        display_name="Test",
        configuration_model=_Inputs,
        environment_model=_Inputs,
        construct=construct,
        describe_environment=lambda configuration: _Environment().descriptor,
        runtime_factory=runtime_factory,
    )


async def test_create_releases_the_runtime_it_acquired_but_never_a_borrowed_one() -> None:
    created: list[_Runtime] = []
    definition = _definition(created)

    owned = await definition.create({})
    async with owned:
        await owned.prepare()
        assert created[0].closed == 0
    assert created[0].closed == 1
    await owned.close()
    assert created[0].closed == 1

    borrowed_runtime = _Runtime()
    borrowed = await definition.create({}, runtime=borrowed_runtime)
    await borrowed.close()
    assert borrowed_runtime.closed == 0
    assert len(created) == 1

    destroyed = await definition.create({})
    await destroyed.destroy()
    assert created[1].closed == 1

    failing_close = await definition.create({})
    assert isinstance(failing_close, _Environment)
    failing_close.close_error = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        await failing_close.close()
    assert created[2].closed == 1


async def test_create_releases_the_acquired_runtime_when_construction_fails() -> None:
    created: list[_Runtime] = []

    def construct(**kwargs: object) -> Environment:
        raise TypeError("construction failed")

    definition = replace(_definition(created), construct=construct)
    with pytest.raises(TypeError, match="construction failed"):
        await definition.create({})
    assert created[0].closed == 1
