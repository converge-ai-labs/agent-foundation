from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.execution import EnvironmentConnector
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    EnvironmentMount,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.environment import (
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import create_empty_environment_runtime, create_environment_runtime
from a13n_harness.environment.sources import EnvironmentScope
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .test_environment_connectors import Connector

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


def _environment(root: Path, environment_id: str) -> EnvironmentConnector:
    provider = DIRECT_LOCAL
    return provider.execution_connector(
        environment=DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
        ),
        state=None,
        environment_id=environment_id,
        runtime=None,
    )


class _TrackingConnector(Connector):
    def __init__(
        self, root: Path, environment_id: str, *, lifecycle_events: list[str] | None = None, fail_entry: bool = False
    ):
        super().__init__(root, failure=fail_entry)
        self.delegate = _environment(root, environment_id)
        self.entry: str | None = None
        self.close_calls = 0
        self._events = lifecycle_events

    async def open(self):
        if self._events is not None:
            self._events.append(f"enter:{self.environment_id}")
        execution = await super().open()
        self.entry = execution.execution_id
        close = execution.close

        async def tracked_close():
            self.close_calls += 1
            if self._events is not None:
                self._events.append(f"close:{self.environment_id}")
            await close()

        execution.close = tracked_close
        return execution


async def test_run_needs_no_environment_for_ordinary_embedded_use() -> None:
    snapshots = []

    async def prepare(context) -> str:
        snapshots.append(context.environment.snapshot)
        return "hello"

    result = await _executable().run(input_factory=prepare)

    assert result.output_or_raise() == "ok"
    assert snapshots[0].mounts == ()
    assert snapshots[0].default_mount is None


async def test_environment_input_is_entered_as_workspace_and_closed_non_destructively(
    tmp_path: Path,
) -> None:
    environment = _TrackingConnector(tmp_path, "workspace-environment")
    observed: list[tuple[str, EnvironmentScope]] = []
    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="agent"),
            agent_instance_id="agent-instance-1",
            host_refs={"attempt": "attempt-1"},
        )
    )

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        assert snapshot.default_mount == "workspace"
        assert [mount.name for mount in snapshot.mounts] == ["workspace"]
        await context.environment.files.write_text("/workspace/value.txt", "preserved", mode="create")
        return "use environment"

    result = await _executable().run(
        input_factory=prepare,
        environment=EnvironmentMount(environment, observer=lambda event, scope, error: observed.append((event, scope))),
        bindings=bindings,
    )

    assert result.output_or_raise() == "ok"
    assert (tmp_path / "value.txt").read_text() == "preserved"
    assert environment.close_calls == 1
    # Execution identity is independent of the Harness mount identity.
    assert environment.entry is not None and environment.entry.startswith("exec-")
    scope = observed[0][1]
    assert (scope.thread_id, scope.run_id) == (result.thread_id, result.run_id)
    assert scope.agent_instance_id == "agent-instance-1"
    assert scope.mount_id != environment.entry
    assert [event for event, _scope in observed] == ["started", "ready", "closed"]


async def test_environment_mount_exposes_an_explicit_aggregate_path(tmp_path: Path) -> None:
    native_root = tmp_path.as_posix()

    async def prepare(context) -> str:
        snapshot = context.environment.snapshot
        assert snapshot.mounts[0].mount_path == native_root
        await context.environment.files.write_text(
            f"{native_root}/value.txt",
            "preserved",
            mode="create",
        )
        with pytest.raises(EnvironmentError) as legacy:
            context.environment.resolve_path("/workspace/value.txt")
        assert legacy.value.code == "environment_selection_invalid"
        return "use direct environment"

    result = await _executable().run(
        input_factory=prepare,
        environment=EnvironmentMount(
            _environment(tmp_path, "direct-environment"),
            mount_path=native_root,
        ),
    )

    assert result.output_or_raise() == "ok"
    assert (tmp_path / "value.txt").read_text() == "preserved"


async def test_multiple_environments_apply_permission_ceiling_and_explicit_default(tmp_path: Path) -> None:
    build_root = tmp_path / "build"
    data_root = tmp_path / "data"
    build_root.mkdir()
    data_root.mkdir()
    (data_root / "input.txt").write_text("source")

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
            "build": _environment(build_root, "build"),
            "data": EnvironmentMount(
                _environment(data_root, "data"),
                permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS),
            ),
        },
        default_environment="build",
    )

    assert result.output_or_raise() == "ok"
    assert (build_root / "output.txt").read_text() == "result"
    assert not (data_root / "denied.txt").exists()


async def test_multiple_environments_do_not_infer_default_from_mapping_order(tmp_path: Path) -> None:
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
            "first": _environment(first_root, "first"),
            "second": _environment(second_root, "second"),
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


async def test_one_connector_can_be_mounted_twice(tmp_path: Path) -> None:
    connector = _TrackingConnector(tmp_path, "duplicate")
    result = await _executable().run("hello", environments={"one": connector, "two": connector})
    assert result.output_or_raise() == "ok"
    assert len(connector.opened) == connector.close_calls == 2
    assert connector.opened[0].execution_id != connector.opened[1].execution_id


@pytest.mark.parametrize(
    "mount_path",
    ["relative", "/with/../parent", "/double//slash", "/trailing/", "C:\\native\\path"],
)
def test_environment_mount_rejects_noncanonical_mount_path(
    tmp_path: Path,
    mount_path: str,
) -> None:
    with pytest.raises(ValueError, match="mount_path"):
        EnvironmentMount(
            _environment(tmp_path, "invalid-mount-path"),
            mount_path=mount_path,
        )


@pytest.mark.parametrize("working_directory", ["relative", "/with/../parent", "/double//slash", "/trailing/"])
def test_environment_mount_rejects_noncanonical_working_directory(
    tmp_path: Path,
    working_directory: str,
) -> None:
    with pytest.raises(ValueError, match="canonical absolute path"):
        EnvironmentMount(
            _environment(tmp_path, "invalid-working-directory"),
            working_directory=working_directory,
        )


def test_high_level_sources_conflict_with_advanced_run_binding(tmp_path: Path) -> None:
    bindings = RunBindings.embedded(environment=create_empty_environment_runtime())

    with pytest.raises(EnvironmentError) as exc_info:
        _executable().stream(
            "hello",
            environment=_environment(tmp_path, "conflict"),
            bindings=bindings,
        )

    assert exc_info.value.code == "environment_request_invalid"


async def test_connector_opens_fresh_executions_across_runs(tmp_path: Path) -> None:
    connector = _TrackingConnector(tmp_path, "repeated")
    for prompt in ("first", "second"):
        assert (await _executable().run(prompt, environment=connector)).output_or_raise() == "ok"
    assert len(connector.opened) == connector.close_calls == 2
    assert connector.opened[0].execution_id != connector.opened[1].execution_id


async def test_entered_environments_close_in_reverse_order_when_later_preparation_fails(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    roots = [tmp_path / name for name in ("first", "second", "failed")]
    for root in roots:
        root.mkdir()
    first = _TrackingConnector(roots[0], "first", lifecycle_events=events)
    second = _TrackingConnector(roots[1], "second", lifecycle_events=events)
    failed = _TrackingConnector(roots[2], "failed", lifecycle_events=events, fail_entry=True)

    with pytest.raises(RuntimeError, match="opening failed"):
        async with _executable().stream(
            "hello",
            environments={"first": first, "second": second, "failed": failed},
        ):
            pytest.fail("Partially opened environments became visible")

    assert events == [
        "enter:first",
        "enter:second",
        "enter:failed",
        "close:second",
        "close:first",
    ]


@pytest.mark.parametrize("explicit_ceiling", [False, True])
async def test_runtime_accepts_environments_with_exact_mount_policy(tmp_path: Path, explicit_ceiling: bool) -> None:
    (tmp_path / "value.txt").write_text("preserved")
    environment = _TrackingConnector(tmp_path, "runtime-input")
    permissions = EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_READ_TEXT}))
    mount = EnvironmentMount(environment, permission_ceiling=permissions) if explicit_ceiling else environment
    runtime = create_environment_runtime(mounts={"workspace": mount}, default_mount="workspace")

    async def prepare(context) -> str:
        assert (await context.environment.files.read_text("/workspace/value.txt")).text == "preserved"
        if explicit_ceiling:
            assert context.environment.snapshot.mounts[0].permission_ceiling == permissions
            with pytest.raises(EnvironmentError) as denied:
                await context.environment.files.write_text("/workspace/other.txt", "denied", mode="create")
            assert denied.value.code == "environment_denied"
        return "use environment"

    result = await _executable().run(input_factory=prepare, bindings=RunBindings.embedded(environment=runtime))
    assert result.output_or_raise() == "ok"
    assert environment.close_calls == 1
    assert not (tmp_path / "other.txt").exists()


async def test_connector_can_open_another_run_without_closing_the_active_one(tmp_path: Path) -> None:
    connector = _TrackingConnector(tmp_path, "shared-runtime-input")
    runtime = create_environment_runtime(mounts={"workspace": connector}, default_mount="workspace")
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        assert (await _executable().run("another run", environment=connector)).output_or_raise() == "ok"
        assert connector.close_calls == 1
        await bound.files.write_text("/workspace/still-active.txt", "preserved", mode="create")
    assert connector.close_calls == 2
    assert (tmp_path / "still-active.txt").read_text() == "preserved"


async def test_dynamic_environment_inputs_preserve_transfer_and_replacement(tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("first", "mounted", "replacement")]
    for root in roots:
        root.mkdir()
    first, mounted, replacement = [_TrackingConnector(root, root.name) for root in roots]
    runtime = create_environment_runtime(mounts={"workspace": first}, default_mount="workspace")
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        await runtime._activate()
        await runtime.mount("mounted", mounted)
        await runtime.replace("workspace", EnvironmentMount(replacement))
        await bound.files.write_text("/workspace/replaced.txt", "new target", mode="create")
        await bound.files.write_text("/environment/mounted/added.txt", "added target", mode="create")
    assert [item.close_calls for item in (first, mounted, replacement)] == [1, 1, 1]
    assert not (roots[0] / "replaced.txt").exists()
    assert (roots[2] / "replaced.txt").read_text() == "new target"
    assert (roots[1] / "added.txt").read_text() == "added target"


async def test_failed_initial_open_leaves_unopened_connectors_usable(tmp_path: Path) -> None:
    first = _TrackingConnector(tmp_path, "first")
    failed = _TrackingConnector(tmp_path, "failed", fail_entry=True)
    later = _TrackingConnector(tmp_path, "later")
    runtime = create_environment_runtime(mounts={"first": first, "failed": failed, "later": later})
    with pytest.raises(RuntimeError, match="opening failed"):
        await _executable().run("initial entry", bindings=RunBindings.embedded(environment=runtime))
    assert [item.close_calls for item in (first, failed, later)] == [1, 0, 0]
    assert (await _executable().run("usable", environment=later)).output_or_raise() == "ok"
    assert later.close_calls == 1


async def test_opened_execution_is_not_a_high_level_connector(tmp_path: Path) -> None:
    connector = _TrackingConnector(tmp_path, "external")
    async with await connector.open() as execution:
        with pytest.raises(EnvironmentError) as failure:
            await _executable().run("cannot take ownership", environment=execution)
        assert failure.value.code == "environment_request_invalid"
        assert connector.close_calls == 0
    assert connector.close_calls == 1


async def test_rejected_dynamic_route_does_not_consume_an_environment(tmp_path: Path) -> None:
    first = _TrackingConnector(tmp_path, "first")
    candidate = _TrackingConnector(tmp_path, "candidate")
    runtime = create_environment_runtime(
        mounts={"workspace": EnvironmentMount(first, mount_path="/project")}, default_mount="workspace"
    )
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        await runtime._activate()
        with pytest.raises(EnvironmentError) as conflict:
            await runtime.mount("conflicting", EnvironmentMount(candidate, mount_path="/project"))
        assert conflict.value.code == "environment_request_invalid"
        assert candidate.close_calls == 0
        assert candidate.opened == []
        await runtime.replace("workspace", EnvironmentMount(candidate, mount_path="/project"))
        await bound.files.write_text("/project/value.txt", "accepted", mode="create")
    assert first.close_calls == candidate.close_calls == 1
    assert (tmp_path / "value.txt").read_text() == "accepted"
