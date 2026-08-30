from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
)
from a13n_harness import (
    EnvironmentAccess,
    EnvironmentMount,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.environment import EnvironmentError
from a13n_harness.environment.advanced import create_empty_environment_runtime
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def _stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "ok"


def _executable():
    return HarnessBuilder().build(
        AgentSpec(name="environment-source-test"),
        output_type=str,
        model=FunctionModel(stream_function=_stream),
    )


def _provider(root: Path, environment_id: str) -> DirectLocalEnvironmentProvider:
    return DirectLocalEnvironmentProvider(
        DirectLocalProviderConfiguration(
            environment_id=environment_id,
            root=DirectLocalRootConfiguration(path=root),
        )
    )


class _TrackingProvider(DirectLocalEnvironmentProvider):
    def __init__(
        self,
        root: Path,
        environment_id: str,
        *,
        lifecycle_events: list[str] | None = None,
    ) -> None:
        super().__init__(
            DirectLocalProviderConfiguration(
                environment_id=environment_id,
                root=DirectLocalRootConfiguration(path=root),
            )
        )
        self.events: list[str] = []
        self._environment_id = environment_id
        self._lifecycle_events = lifecycle_events

    async def create(self, *, operation: EnvironmentOperationContext):
        self.events.append("create")
        if self._lifecycle_events is not None:
            self._lifecycle_events.append(f"create:{self._environment_id}")
        return await super().create(operation=operation)

    async def destroy(self, state, *, operation: EnvironmentOperationContext) -> None:
        self.events.append("destroy")
        if self._lifecycle_events is not None:
            self._lifecycle_events.append(f"destroy:{self._environment_id}")
        await super().destroy(state, operation=operation)


def _operation(action: EnvironmentManagementAction, suffix: str) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{suffix}",
        action=action,
        resource_correlation=f"resource-{suffix}",
        attempt=1,
    )


async def test_run_needs_no_mounts_or_environment_for_ordinary_embedded_use() -> None:
    snapshots = []

    async def prepare(context) -> str:
        snapshots.append(context.environment.snapshot)
        return "hello"

    result = await _executable().run(input_factory=prepare)

    assert result.output_or_raise() == "ok"
    assert snapshots[0].mounts == ()
    assert snapshots[0].default_mount is None


async def test_provider_input_is_harness_owned_and_available_as_workspace(tmp_path: Path) -> None:
    provider = _TrackingProvider(tmp_path, "provider-owned")
    observed = []

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        observed.append(snapshot)
        assert snapshot.default_mount == "workspace"
        assert [mount.name for mount in snapshot.mounts] == ["workspace"]
        await context.environment.files.write_text("/workspace/value.txt", "created", mode="create")
        return "use environment"

    result = await _executable().run(input_factory=prepare, environment=provider)

    assert result.output_or_raise() == "ok"
    assert (tmp_path / "value.txt").read_text() == "created"
    assert len(observed) == 1
    assert provider.events == ["create", "destroy"]


async def test_entered_resource_is_borrowed_and_reusable_across_runs(tmp_path: Path) -> None:
    provider = _provider(tmp_path, "host-owned")
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:

        async def write(context) -> str:
            await context.environment.files.write_text("/workspace/shared.txt", "preserved", mode="create")
            return "write"

        async def read(context) -> str:
            value = await context.environment.files.read_text("/workspace/shared.txt")
            assert value.text == "preserved"
            return "read"

        first = await _executable().run(input_factory=write, environment=resource)
        assert resource.is_entered
        second = await _executable().run(input_factory=read, environment=resource)
        assert resource.is_entered

    await provider.destroy(
        resource.state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )
    assert first.output_or_raise() == second.output_or_raise() == "ok"


async def test_multiple_sources_support_mixed_ownership_access_and_explicit_default(
    tmp_path: Path,
) -> None:
    build_root = tmp_path / "build"
    data_root = tmp_path / "data"
    build_root.mkdir()
    data_root.mkdir()
    (data_root / "input.txt").write_text("source")
    build_provider = _provider(build_root, "build-provider")
    data_provider = _provider(data_root, "data-resource")
    data_resource = await data_provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "data"))

    async with data_resource:

        async def prepare(context) -> str:
            snapshot = context.environment.snapshot
            assert snapshot.default_mount == "build"
            assert [mount.name for mount in snapshot.mounts] == ["build", "data"]
            await context.environment.files.write_text("/workspace/output.txt", "result", mode="create")
            source = await context.environment.files.read_text("/environment/data/input.txt")
            assert source.text == "source"
            with pytest.raises(EnvironmentError) as exc_info:
                await context.environment.files.write_text(
                    "/environment/data/denied.txt",
                    "denied",
                    mode="create",
                )
            assert exc_info.value.code == "environment_denied"
            return "mixed"

        result = await _executable().run(
            input_factory=prepare,
            environments={
                "build": build_provider,
                "data": EnvironmentMount(data_resource, access=EnvironmentAccess.READ_ONLY),
            },
            default_environment="build",
        )
        assert data_resource.is_entered

    assert result.output_or_raise() == "ok"
    assert (build_root / "output.txt").read_text() == "result"
    assert not (data_root / "denied.txt").exists()


async def test_multiple_sources_never_select_default_from_mapping_order(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    async def prepare(context) -> str:
        assert context.environment.snapshot.default_mount is None
        with pytest.raises(EnvironmentError) as exc_info:
            context.environment.resolve_path("/workspace/value.txt")
        assert exc_info.value.code == "environment_selection_invalid"
        resolved = context.environment.resolve_path("/environment/second/value.txt")
        assert resolved.mount_id
        assert resolved.path == "/value.txt"
        return "no default"

    result = await _executable().run(
        input_factory=prepare,
        environments={
            "first": _provider(first_root, "first"),
            "second": _provider(second_root, "second"),
        },
    )
    assert result.output_or_raise() == "ok"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"environment": object(), "environments": {"other": object()}},
        {"environment": object(), "default_environment": "other"},
        {"environments": {}},
        {"environments": {"one": object()}},
        {"environments": {"one": object()}, "default_environment": "missing"},
    ],
)
def test_environment_source_validation_fails_before_stream_entry(kwargs: dict[str, object]) -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream("hello", **kwargs)  # type: ignore[arg-type]
    assert exc_info.value.code == "environment_request_invalid"


@pytest.mark.parametrize("working_directory", ["relative", "/with/../parent", "/double//slash", "/trailing/"])
def test_environment_mount_rejects_noncanonical_working_directory(
    tmp_path: Path,
    working_directory: str,
) -> None:
    with pytest.raises(ValueError, match="canonical absolute path"):
        EnvironmentMount(
            _provider(tmp_path, "invalid-working-directory"),
            working_directory=working_directory,
        )


def test_high_level_sources_conflict_with_advanced_run_binding(tmp_path: Path) -> None:
    bindings = RunBindings.embedded(environment=create_empty_environment_runtime())

    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream(
            "hello",
            environment=_provider(tmp_path, "conflict"),
            bindings=bindings,
        )

    assert exc_info.value.code == "environment_request_invalid"


def test_invalid_run_bindings_fail_before_stream_entry() -> None:
    with pytest.raises(Exception) as exc_info:
        _executable().stream("hello", bindings=object())  # type: ignore[arg-type]

    assert getattr(exc_info.value, "code", None) == "run_bindings_invalid"


async def test_unentered_resource_fails_on_stream_entry(tmp_path: Path) -> None:
    provider = _provider(tmp_path, "unentered")
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "unentered"))

    with pytest.raises(EnvironmentError) as exc_info:
        async with _executable().stream("hello", environment=resource):
            pass

    assert exc_info.value.code == "environment_request_invalid"
    await provider.destroy(
        resource.state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy-unentered"),
    )


async def test_provider_sources_are_cleaned_in_reverse_order_when_later_entry_fails(tmp_path: Path) -> None:
    events: list[str] = []
    first_root = tmp_path / "first-cleanup"
    second_root = tmp_path / "second-cleanup"
    failed_root = tmp_path / "failed-resource"
    first_root.mkdir()
    second_root.mkdir()
    failed_root.mkdir()
    first = _TrackingProvider(first_root, "first-cleanup", lifecycle_events=events)
    second = _TrackingProvider(second_root, "second-cleanup", lifecycle_events=events)
    failed_provider = _provider(failed_root, "failed-resource")
    unentered = await failed_provider.create(
        operation=_operation(EnvironmentManagementAction.CREATE, "failed-resource")
    )

    with pytest.raises(EnvironmentError) as exc_info:
        async with _executable().stream(
            "hello",
            environments={"first": first, "second": second, "failed": unentered},
        ):
            pass

    assert exc_info.value.code == "environment_request_invalid"
    assert events == [
        "create:first-cleanup",
        "create:second-cleanup",
        "destroy:second-cleanup",
        "destroy:first-cleanup",
    ]
    await failed_provider.destroy(
        unentered.state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy-failed-resource"),
    )


async def test_provider_owned_environment_is_cleaned_when_input_preparation_fails(tmp_path: Path) -> None:
    provider = _TrackingProvider(tmp_path, "preparation-failure")

    async def fail(context) -> str:
        await context.environment.files.write_text("/workspace/before.txt", "written", mode="create")
        raise ValueError("invalid input")

    with pytest.raises(Exception) as exc_info:
        await _executable().run(input_factory=fail, environment=provider)

    assert getattr(exc_info.value, "code", None) == "input_factory_failed"
    assert (tmp_path / "before.txt").read_text() == "written"
    assert provider.events == ["create", "destroy"]

    # A second run proves the Provider-owned resource and attachment scopes were fully retired.
    result = await _executable().run("again", environment=provider)
    assert result.output_or_raise() == "ok"
